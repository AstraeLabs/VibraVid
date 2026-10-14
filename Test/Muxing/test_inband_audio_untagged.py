# 14.10.26

"""Audio muxed in-band inside the video (e.g. RaiPlay HLS .ts, no EXT-X-MEDIA audio) and tracks with no language tag.

- Every streaming-mux fast path must keep the video input's own audio (`-map 0:a?`) when no separate audio track is muxed in.
- get_media_metadata() must list untagged / "und" tracks as UND (same as _flux_media_metadata) instead of dropping them.
"""

import json
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from VibraVid.core.downloader._generic import Generic_Downloader
from VibraVid.core.muxing.helper.video import metadata as metadata_mod
from VibraVid.core.velora._decrypt_pipeline import DecryptPipelineMixin
from VibraVid.core.velora._decrypt_pipeline import streaming_mux as velora_mux


def _has_map(cmd: list[str], spec: str) -> bool:
    return any(a == "-map" and b == spec for a, b in zip(cmd, cmd[1:]))


# ── get_media_metadata ───────────────────────────────────────────────────────

def _probe(monkeypatch, tmp_path, streams):
    media = tmp_path / "media.mkv"
    media.write_bytes(b"x")
    payload = json.dumps({"streams": streams})
    monkeypatch.setattr(metadata_mod, "get_ffprobe_path", lambda: "ffprobe")
    monkeypatch.setattr(
        metadata_mod.subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=0, stdout=payload, stderr="")
    )
    return metadata_mod.get_media_metadata.__wrapped__(str(media))


def test_untagged_audio_is_listed_as_und(monkeypatch, tmp_path):
    md = _probe(monkeypatch, tmp_path, [
        {"codec_type": "video", "codec_name": "h264", "width": 1024, "height": 576},
        {"codec_type": "audio", "codec_name": "aac"},
    ])
    assert md["audio_codec"] == "AAC"
    assert [t["language"] for t in md["audio_tracks"]] == ["UND"]
    assert md["audio_tracks"][0]["language_iso3"] == "und"
    assert md["language"] == ""  # UND never leaks into the %(language) naming token


def test_und_tag_is_treated_as_unknown(monkeypatch, tmp_path):
    md = _probe(monkeypatch, tmp_path, [
        {"codec_type": "audio", "codec_name": "aac", "tags": {"language": "und"}},
        {"codec_type": "audio", "codec_name": "eac3", "tags": {"language": "ita"}},
    ])
    assert [t["language"] for t in md["audio_tracks"]] == ["UND", "ITA"]
    assert md["language"] == "ITA"
    assert md["audio_codec"] == "AAC-E-AC-3"


def test_untagged_subtitle_is_listed_as_und(monkeypatch, tmp_path):
    md = _probe(monkeypatch, tmp_path, [
        {"codec_type": "subtitle", "codec_name": "subrip", "disposition": {"forced": 1}},
    ])
    assert md["subtitle_tracks"][0]["language"] == "UND"
    assert md["subtitle_tracks"][0]["forced"] is True
    assert md["sub_language"] == ""
    assert md["sub_flags"] == "FORCED"


def test_video_only_has_no_audio_tracks(monkeypatch, tmp_path):
    md = _probe(monkeypatch, tmp_path, [{"codec_type": "video", "codec_name": "h264", "width": 1280, "height": 720}])
    assert md["audio_tracks"] == []
    assert md["audio_codec"] == ""


# ── velora streaming-mux fast path ───────────────────────────────────────────

class _CapturingFeeder:
    last_cmd: list[str] | None = None

    def __init__(self, cmd):
        _CapturingFeeder.last_cmd = cmd

    def start(self):
        pass


def _velora_cmd(monkeypatch, tmp_path, streams, results):
    monkeypatch.setattr(velora_mux, "_streaming_mux_enabled", lambda: True)
    monkeypatch.setattr(velora_mux, "get_ffmpeg_path", lambda: "ffmpeg")
    monkeypatch.setattr(velora_mux, "StreamingMuxFeeder", _CapturingFeeder)
    monkeypatch.setattr(velora_mux.config_manager.config, "get", lambda *a, **k: k.get("default", "ffmpeg"))

    done = threading.Event()
    done.set()
    obj = object.__new__(DecryptPipelineMixin)
    obj._streaming_mux_output_path = str(tmp_path / "out.mkv")
    obj._streaming_mux_chapters = None
    obj.other_tracks = []
    obj.external_subtitles = []
    obj.external_audios = []
    obj.streams = streams
    obj._stream_task_key = lambda s: s.id
    obj._track_done_event = lambda key: done
    obj._get_track_result = lambda key: results[key]

    video = SimpleNamespace(type="video", codecs="avc1.4d401f")
    assert obj._try_start_streaming_mux_inner("ts", video) is not None
    return _CapturingFeeder.last_cmd


def test_velora_fast_path_keeps_inband_audio_without_separate_audio(monkeypatch, tmp_path):
    cmd = _velora_cmd(monkeypatch, tmp_path, streams=[], results={})
    assert _has_map(cmd, "0:v:0")
    assert _has_map(cmd, "0:a?")


def test_velora_fast_path_skips_inband_audio_with_separate_audio(monkeypatch, tmp_path):
    audio_file = tmp_path / "audio.m4a"
    audio_file.write_bytes(b"\x00" * 32)
    audio = SimpleNamespace(
        id="aud", type="audio", selected=True, is_external=False, segments=[],
        compute_estimated_size=lambda: 0, resolved_language="ita", language="ita", name="Italiano",
    )
    cmd = _velora_cmd(monkeypatch, tmp_path, streams=[audio], results={"aud": Path(audio_file)})
    assert not _has_map(cmd, "0:a?")
    assert _has_map(cmd, "1:a")


# ── Generic_Downloader direct-mp4 fast path ──────────────────────────────────

@pytest.fixture
def generic(monkeypatch, tmp_path):
    monkeypatch.setattr("VibraVid.core.downloader._generic.get_ffmpeg_path", lambda: "ffmpeg")
    d = object.__new__(Generic_Downloader)
    d.output_path = str(tmp_path / "out.mkv")
    return d


def test_generic_direct_keeps_inband_audio_without_sibling_audio(generic):
    cmd, _ = generic._build_streaming_mux_cmd_direct({"source": {"codecs": "avc1"}}, [])
    assert _has_map(cmd, "0:a?")


def test_generic_direct_skips_inband_audio_with_sibling_audio(generic, tmp_path):
    audio_file = tmp_path / "audio.m4a"
    audio_file.write_bytes(b"\x00" * 32)
    sibling = {
        "source": {"role": "audio"},
        "result": {"path": str(audio_file), "language": "ita"},
        "label": "audio",
    }
    cmd, _ = generic._build_streaming_mux_cmd_direct({"source": {"codecs": "avc1"}}, [sibling])
    assert not _has_map(cmd, "0:a?")
    assert _has_map(cmd, "1:a")
