# 06.10.26

"""``EXTRACT_EMBEDDED_CC`` flag must gate CEA-608/708 extraction in _join_media_ffmpeg."""

import pytest

from VibraVid.core.muxing import merge

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

