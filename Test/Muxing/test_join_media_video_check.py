# 06.10.26

"""``join_media`` must never report success when the source had a video track and the muxed output has none."""

import pytest

from VibraVid.core.muxing import merge

VIDEO = {"codec_type": "video", "codec_name": "h264"}
AUDIO = {"codec_type": "audio", "codec_name": "eac3"}


@pytest.fixture
def probes(monkeypatch):
    """``probes[path] = [streams]`` drives what the fake ffprobe says; unknown paths probe as empty (unreadable)."""
    table = {}
    monkeypatch.setattr(merge, "get_stream_codecs", lambda path: table.get(str(path), []))
    return table


def _fake_join(monkeypatch, tmp_path, result=None):
    out = tmp_path / "out.mkv"

    def fake(video_path, audio_tracks, subtitle_tracks, out_path, *args, **kwargs):
        out.write_bytes(b"muxed")
        return str(out), dict(result or {})

    monkeypatch.setattr(merge, "_join_media_ffmpeg", fake)
    monkeypatch.setattr(merge, "_apply_compatible_extension", lambda video_path, out_path: out_path)
    monkeypatch.setattr(merge, "MUX_ENGINE", "ffmpeg")
    monkeypatch.setattr(merge, "get_mkvmerge_path", lambda: None)
    return out


def test_output_without_video_is_rejected_removed_and_flagged(monkeypatch, tmp_path, probes):
    out = _fake_join(monkeypatch, tmp_path, {"exit_code": 0})
    probes["video.mp4"] = [VIDEO]
    probes[str(out)] = [AUDIO]

    merged, result = merge.join_media("video.mp4", [], [], str(tmp_path / "out.mkv"))

    assert not out.exists()
    assert result["exit_code"] == 1 and result["video_missing"] is True
    assert merged == str(out)


def test_output_with_video_is_kept_untouched(monkeypatch, tmp_path, probes):
    out = _fake_join(monkeypatch, tmp_path, {"exit_code": 0})
    probes["video.mp4"] = [VIDEO]
    probes[str(out)] = [VIDEO, AUDIO]

    merged, result = merge.join_media("video.mp4", [], [], str(tmp_path / "out.mkv"))

    assert out.exists() and result == {"exit_code": 0}


def test_source_without_video_is_not_second_guessed(monkeypatch, tmp_path, probes):
    out = _fake_join(monkeypatch, tmp_path)
    probes["audio_only.m4a"] = [AUDIO]
    probes[str(out)] = [AUDIO]

    _merged, result = merge.join_media("audio_only.m4a", [], [], str(tmp_path / "out.mkv"))

    assert out.exists() and "video_missing" not in result


def test_unprobeable_files_never_cause_a_deletion(monkeypatch, tmp_path, probes):
    out = _fake_join(monkeypatch, tmp_path)
    probes["video.mp4"] = [VIDEO]  # the output cannot be probed -> unknown, keep it

    _merged, result = merge.join_media("video.mp4", [], [], str(tmp_path / "out.mkv"))

    assert out.exists() and "video_missing" not in result


def test_a_real_failure_exit_code_is_preserved(monkeypatch, tmp_path, probes):
    out = _fake_join(monkeypatch, tmp_path, {"exit_code": 7})
    probes["video.mp4"] = [VIDEO]
    probes[str(out)] = [AUDIO]

    _merged, result = merge.join_media("video.mp4", [], [], str(tmp_path / "out.mkv"))

    assert result["exit_code"] == 7 and result["video_missing"] is True
