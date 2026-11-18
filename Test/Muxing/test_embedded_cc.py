# 28.10.26
# ruff: noqa: E402

"""Closed captions baked into an HLS video (EXT-X-MEDIA TYPE=CLOSED-CAPTIONS): selected like any other subtitle, extracted per channel, named and ordered like a subtitle file."""

import os
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

workspace_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(workspace_root))

from VibraVid.core.downloader import hls as hls_module
from VibraVid.core.manifest.m3u8 import HLSParser
from VibraVid.core.muxing.helper.sub import cc_extract
from VibraVid.core.muxing.helper.sub.cc_extract import cc_data_field, extract_embedded_cc
from VibraVid.core.utils.language import track_title_and_iso_lang
from VibraVid.core.utils.selector import tag_matches_filter

_MASTER = """#EXTM3U
#EXT-X-VERSION:6
#EXT-X-MEDIA:TYPE=AUDIO,GROUP-ID="a",NAME="ENG",DEFAULT=YES,AUTOSELECT=YES,LANGUAGE="en",URI="audio/en.m3u8"
#EXT-X-MEDIA:TYPE=CLOSED-CAPTIONS,GROUP-ID="cc",NAME="caption_1",DEFAULT=NO,AUTOSELECT=YES,LANGUAGE="en",INSTREAM-ID="CC1"
#EXT-X-MEDIA:TYPE=CLOSED-CAPTIONS,GROUP-ID="cc",NAME="caption_2",DEFAULT=NO,AUTOSELECT=YES,LANGUAGE="es",INSTREAM-ID="CC3"
#EXT-X-STREAM-INF:BANDWIDTH=2000000,CODECS="avc1.640028,mp4a.40.2",RESOLUTION=1280x720,AUDIO="a",CLOSED-CAPTIONS="cc"
video/720p.m3u8
"""


def _captions() -> list:
    parser = HLSParser(m3u8_url="file:///dummy/master.m3u8", content=_MASTER)
    parser.fetch_manifest()
    with patch.object(HLSParser, "_resolve_drm", lambda self, streams, master_drm: None):
        return [s for s in parser.parse_streams() if s.type == "subtitle" and s.is_cc]


def test_the_manifest_keeps_the_channel_of_each_caption():
    assert {s.language: s.instream_id for s in _captions()} == {"en": "CC1", "es": "CC3"}


@pytest.mark.parametrize(
    "instream_id,field",
    [("CC1", "first"), ("cc2", "first"), ("CC3", "second"), (" CC4 ", "second"), ("SERVICE1", None), ("", None), (None, None)],
)
def test_cc_channels_map_to_the_data_field_that_carries_them(instream_id, field):
    assert cc_data_field(instream_id) == field


def _extract(tmp_path, monkeypatch, instream_id) -> list[str]:
    """The ffmpeg command extract_embedded_cc runs, with a fake ffmpeg that writes some text."""
    video = tmp_path / "out.mp4"
    video.write_bytes(b"x")
    seen = {}

    def fake_run(cmd, **kwargs):
        seen["cmd"] = cmd
        (Path(kwargs["cwd"]) / cmd[-1]).write_text("1\n00:00:01,000 --> 00:00:02,000\nhi\n", encoding="utf-8")
        return SimpleNamespace(returncode=0, stderr="")

    monkeypatch.setattr(cc_extract, "get_ffmpeg_path", lambda: "ffmpeg")
    monkeypatch.setattr(cc_extract.subprocess, "run", fake_run)
    assert extract_embedded_cc(str(video), str(tmp_path / "out.es_cc.srt"), instream_id)
    return seen["cmd"]


def test_a_second_field_caption_is_extracted_from_the_second_field(tmp_path, monkeypatch):
    cmd = _extract(tmp_path, monkeypatch, "CC3")
    assert cmd[cmd.index("-data_field") + 1] == "second"
    assert cmd.index("-data_field") < cmd.index("-f")  # an input option: it must come before the lavfi input


def test_without_a_known_channel_ffmpeg_picks_the_field_like_before(tmp_path, monkeypatch):
    assert "-data_field" not in _extract(tmp_path, monkeypatch, None)


@pytest.mark.parametrize(
    "value,language,kwargs,expected",
    [
        ("spa|eng", "es-419", {}, True),  # a 3-letter request matches a locale of that language
        ("spa|eng", "en-US", {}, True),
        ("spa|eng", "it", {}, False),
        ("1ita|2eng", "en", {}, True),  # slots: the language matters here, not the slot
        ("1ita|2eng", "it", {}, True),
        ("eng_forced", "en", {}, False),
        ("eng_forced", "en", {"forced": True}, True),
        ("eng_sdh", "en", {"sdh": True}, True),
        ("eng_cc", "en", {"cc": True}, True),
        ("eng_cc", "en", {}, False),
        ("all", "xx", {}, True),
        ("", "xx", {}, True),
        ("false", "en", {}, False),
    ],
)
def test_a_track_known_by_its_tag_is_matched_like_a_manifest_stream(value, language, kwargs, expected):
    assert tag_matches_filter(value, "subtitle", language, **kwargs) is expected


def _downloader(captions, monkeypatch, enabled):
    monkeypatch.setattr(hls_module, "EXTRACT_EMBEDDED_CC", enabled)
    host = object.__new__(hls_module.HLS_Downloader)
    host.media_downloader = SimpleNamespace(_ext_lang_matches=lambda lang, kind: tag_matches_filter("spa|eng", kind, lang))
    return host


def test_captions_are_only_collected_when_extract_embedded_cc_is_on(monkeypatch):
    assert _downloader(_captions(), monkeypatch, enabled=False)._collect_embedded_cc(_captions()) == []
    assert len(_downloader(_captions(), monkeypatch, enabled=True)._collect_embedded_cc(_captions())) == 2


def test_extracted_captions_are_named_like_a_subtitle_file_and_extracted_per_channel(tmp_path, monkeypatch):
    video = tmp_path / "out.mp4"
    video.write_bytes(b"x")
    calls = []

    def fake_extract(video_path, srt_path, instream_id=None):
        calls.append((os.path.basename(srt_path), instream_id))
        Path(srt_path).write_text("1\n00:00:01,000 --> 00:00:02,000\nhi\n", encoding="utf-8")
        return srt_path

    monkeypatch.setattr(hls_module, "extract_embedded_cc", fake_extract)
    host = _downloader(None, monkeypatch, enabled=True)
    host.output_dir, host.filename_base = str(tmp_path), "out"
    host._embedded_cc_streams = _captions()

    tracks = host._prepare_subtitle_tracks_for_merge([], str(video))

    assert [instream for _name, instream in calls] == ["CC1", "CC3"]
    assert [track_title_and_iso_lang(t) for t in tracks] == [("English [CC]", "eng"), ("Spanish [CC]", "spa")]
