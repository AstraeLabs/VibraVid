# 06.10.26

"""``EXTRACT_EMBEDDED_CC`` flag must gate CEA-608/708 extraction in _join_media_ffmpeg."""

import os
import pytest

from VibraVid.core.muxing import merge
from VibraVid.core.muxing.helper.video.compat import get_stream_codecs

_CC_STREAM = {"codec_type": "subtitle", "codec_name": "eia_608", "language": "eng"}
_VIDEO_STREAM = {"codec_type": "video", "codec_name": "hevc"}


@pytest.fixture
def no_io(monkeypatch):
    """Suppress helpers that touch real files (unit tests only)."""
    monkeypatch.setattr(merge, "detect_ts_timestamp_issues", lambda _p: False, raising=False)
    monkeypatch.setattr(merge, "is_mpegts_file", lambda _p: False, raising=False)
    monkeypatch.setattr(merge, "get_ffmpeg_path", lambda: "ffmpeg", raising=False)
    monkeypatch.setattr(merge, "get_video_duration", lambda _p: None, raising=False)
    monkeypatch.setattr(merge, "USE_GPU", False)


def _capture_cmd(monkeypatch):
    calls = []

    def fake_capture(cmd, *_a, **_kw):
        calls.append(list(cmd))
        return {"exit_code": 0}

    monkeypatch.setattr(merge, "capture_ffmpeg_real_time", fake_capture)
    return calls


# ── unit tests (no real files) ────────────────────────────────────────────────

def test_cc_not_probed_when_flag_is_false(no_io, monkeypatch, tmp_path):
    """With EXTRACT_EMBEDDED_CC=False, get_stream_codecs must never be called."""
    monkeypatch.setattr(merge, "EXTRACT_EMBEDDED_CC", False)

    probed = []
    monkeypatch.setattr(merge, "get_stream_codecs", lambda p: probed.append(p) or [_VIDEO_STREAM, _CC_STREAM])
    calls = _capture_cmd(monkeypatch)

    try:
        merge._join_media_ffmpeg(
            video_path="video.mp4",
            audio_tracks=[],
            subtitle_tracks=[],
            out_path=str(tmp_path / "out.mp4"),
            use_shortest=False,
        )
    except Exception:
        pass

    assert probed == [], f"get_stream_codecs called despite EXTRACT_EMBEDDED_CC=False: {probed}"
    if calls:
        flat = " ".join(calls[0])
        assert "0:s:0" not in flat, f"CEA-608 map found in command despite flag=False: {flat}"


def test_cc_probed_and_mapped_when_flag_is_true(no_io, monkeypatch, tmp_path):
    """With EXTRACT_EMBEDDED_CC=True, CEA-608 stream must appear in the ffmpeg command."""
    monkeypatch.setattr(merge, "EXTRACT_EMBEDDED_CC", True)
    monkeypatch.setattr(merge, "get_stream_codecs", lambda _p: [_VIDEO_STREAM, _CC_STREAM])
    calls = _capture_cmd(monkeypatch)

    try:
        merge._join_media_ffmpeg(
            video_path="video.mp4",
            audio_tracks=[],
            subtitle_tracks=[],
            out_path=str(tmp_path / "out.mp4"),
            use_shortest=False,
        )
    except Exception:
        pass

    assert calls, "capture_ffmpeg_real_time was never called"
    flat = " ".join(calls[0])
    assert "0:s:0" in flat, f"Expected CEA-608 map '0:s:0' in ffmpeg command, got: {flat}"


# ── integration tests with real local files ───────────────────────────────────

TEMP_VIDEO = r"C:\Users\Testing\Documents\GitHub\VibraVid_speed\Video\.Custom_generic_temp\src0\Custom.mp4"
TEMP_AUDIO_EN = r"C:\Users\Testing\Documents\GitHub\VibraVid_speed\Video\.Custom_generic_temp\src1\Custom.en-us.m4a"
TEMP_AUDIO_IT = r"C:\Users\Testing\Documents\GitHub\VibraVid_speed\Video\.Custom_generic_temp\src1\Custom.it-it.m4a"
TEMP_SUB_EN = r"C:\Users\Testing\Documents\GitHub\VibraVid_speed\Video\.Custom_generic_temp\src0\Custom.en-gb.vtt"
TEMP_SUB_IT = r"C:\Users\Testing\Documents\GitHub\VibraVid_speed\Video\.Custom_generic_temp\src0\Custom.it-it.vtt"

_HAVE_REAL_FILES = all(os.path.exists(p) for p in [TEMP_VIDEO, TEMP_AUDIO_EN, TEMP_AUDIO_IT])
skip_no_files = pytest.mark.skipif(not _HAVE_REAL_FILES, reason="local temp files not present")

AUDIO_TRACKS = [
    {"path": TEMP_AUDIO_EN, "name": "en-us", "language": "en-US", "language_iso2": "en", "language_iso3": "eng"},
    {"path": TEMP_AUDIO_IT, "name": "it-it", "language": "it-IT", "language_iso2": "it", "language_iso3": "ita"},
]
SUBTITLE_TRACKS = [
    {"path": TEMP_SUB_EN, "name": "en-gb", "language": "en-gb", "type": "vtt", "cc": False, "forced": False, "sdh": False},
    {"path": TEMP_SUB_IT, "name": "it-it", "language": "it-it", "type": "vtt", "cc": False, "forced": False, "sdh": False},
]

# Expected subtitle counts (only external VTT subs, no CC):
_EXPECTED_SUB_COUNT_NO_CC = len(SUBTITLE_TRACKS)          # 2
_EXPECTED_SUB_COUNT_WITH_CC = len(SUBTITLE_TRACKS) + 1    # 3


@skip_no_files
def test_real_mux_false_no_cc_track(monkeypatch, tmp_path):
    """Real mux with flag=False must produce exactly the external subtitle tracks, no CC."""
    monkeypatch.setattr(merge, "EXTRACT_EMBEDDED_CC", False)
    out = str(tmp_path / "out_false.mp4")

    _merged, result = merge.join_media(TEMP_VIDEO, AUDIO_TRACKS, SUBTITLE_TRACKS, out)

    assert result.get("exit_code", 0) == 0, f"mux failed: {result}"
    assert os.path.exists(_merged), "output file not created"

    streams = get_stream_codecs(_merged)
    sub_streams = [s for s in streams if s.get("codec_type") == "subtitle"]
    assert len(sub_streams) == _EXPECTED_SUB_COUNT_NO_CC, (
        f"expected {_EXPECTED_SUB_COUNT_NO_CC} subtitle track(s) (no CC), got {len(sub_streams)}: {sub_streams}"
    )


@skip_no_files
def test_real_mux_true_has_cc_track(monkeypatch, tmp_path):
    """Real mux with flag=True must produce one extra subtitle track for the extracted CC."""
    monkeypatch.setattr(merge, "EXTRACT_EMBEDDED_CC", True)
    out = str(tmp_path / "out_true.mp4")

    _merged, result = merge.join_media(TEMP_VIDEO, AUDIO_TRACKS, SUBTITLE_TRACKS, out)

    assert result.get("exit_code", 0) == 0, f"mux failed: {result}"
    assert os.path.exists(_merged), "output file not created"

    streams = get_stream_codecs(_merged)
    sub_streams = [s for s in streams if s.get("codec_type") == "subtitle"]
    assert len(sub_streams) == _EXPECTED_SUB_COUNT_WITH_CC, (
        f"expected {_EXPECTED_SUB_COUNT_WITH_CC} subtitle track(s) (with CC), got {len(sub_streams)}: {sub_streams}"
    )
