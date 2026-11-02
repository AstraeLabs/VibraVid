# 02.11.26

"""DASH audio/video sync: an fMP4 audio input must not shift the video by ~8.9s."""

import json
import os
import shutil
import subprocess

import pytest

from VibraVid.core.muxing import merge
from VibraVid.core.muxing.helper.video.ts import audio_editlist_args


@pytest.fixture
def no_io(monkeypatch):
    monkeypatch.setattr(merge, "detect_ts_timestamp_issues", lambda _p: False, raising=False)
    monkeypatch.setattr(merge, "is_mpegts_file", lambda _p: False, raising=False)
    monkeypatch.setattr(merge, "get_ffmpeg_path", lambda: "ffmpeg", raising=False)
    monkeypatch.setattr(merge, "get_video_duration", lambda _p: None, raising=False)
    monkeypatch.setattr(merge, "get_stream_codecs", lambda _p: [], raising=False)
    monkeypatch.setattr(merge, "USE_GPU", False)
    monkeypatch.setattr(merge, "EXTRACT_EMBEDDED_CC", False)


def test_helper_ignores_editlist():
    assert audio_editlist_args() == ["-ignore_editlist", "1"]


def test_helper_only_for_mp4_files(tmp_path):
    mp4 = tmp_path / "a.m4a"
    mp4.write_bytes(b"\x00\x00\x00\x18ftypiso5" + b"\x00" * 32)
    assert audio_editlist_args(str(mp4)) == ["-ignore_editlist", "1"]
    for name, head in {"a.mp3": b"ID3\x04" + b"\x00" * 40, "a.ac3": b"\x0b\x77" + b"\x00" * 40, "a.ts": b"\x47" + b"\x00" * 40}.items():
        f = tmp_path / name
        f.write_bytes(head)
        assert audio_editlist_args(str(f)) == [], name
    assert audio_editlist_args(str(tmp_path / "missing.m4a")) == []


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg required")
@pytest.mark.parametrize("ext,codec", [("m4a", "aac"), ("aac", "aac"), ("ac3", "ac3"), ("eac3", "eac3"), ("mp3", "libmp3lame"), ("opus", "libopus"), ("flac", "flac")])
def test_flag_never_breaks_ffmpeg_for_any_audio_format(tmp_path, ext, codec):
    f = str(tmp_path / f"t.{ext}")
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "sine=d=1", "-c:a", codec, "-y", f], check=True)
    r = subprocess.run(["ffmpeg", "-v", "error", *audio_editlist_args(f), "-i", f, "-c", "copy", "-f", "null", "-"], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


def test_join_ignores_editlist_only_on_audio_input(no_io, monkeypatch, tmp_path):
    calls = []
    audio = tmp_path / "audio.m4a"
    audio.write_bytes(b"\x00\x00\x00\x18ftypiso5" + b"\x00" * 32)
    monkeypatch.setattr(merge, "capture_ffmpeg_real_time", lambda cmd, *_a, **_k: calls.append(list(cmd)) or {"exit_code": 0})

    merge._join_media_ffmpeg(
        video_path="video.mp4",
        audio_tracks=[{"path": str(audio), "name": "it-it", "language": "it-it"}],
        subtitle_tracks=[],
        out_path=str(tmp_path / "out.mkv"),
        use_shortest=False,
    )

    cmd = calls[0]
    inputs = [i for i, tok in enumerate(cmd) if tok == "-i"]
    video_i, audio_i = inputs[0], inputs[1]
    assert cmd[video_i + 1] == "video.mp4" and cmd[audio_i + 1] == str(audio)
    assert "-ignore_editlist" not in cmd[:video_i]
    assert cmd[audio_i - 2 : audio_i] == ["-ignore_editlist", "1"]


def _first_pts(path, stream):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", stream, "-show_entries", "packet=pts_time", "-read_intervals", "%+#1", "-of", "json", path],
        capture_output=True, text=True, check=True,
    ).stdout
    return float(json.loads(out)["packets"][0]["pts_time"])


@pytest.mark.skipif(
    not (shutil.which("ffmpeg") and os.environ.get("VV_SYNC_VIDEO") and os.environ.get("VV_SYNC_AUDIO")),
    reason="set VV_SYNC_VIDEO / VV_SYNC_AUDIO to decrypted files",
)
def test_e2e_video_and_audio_start_together(tmp_path):
    out = str(tmp_path / "out.mkv")
    subprocess.run(
        ["ffmpeg", "-v", "error", "-i", os.environ["VV_SYNC_VIDEO"], *audio_editlist_args(), "-i", os.environ["VV_SYNC_AUDIO"],
         "-map", "0:v:0", "-map", "1:a", "-c", "copy", "-y", out],
        check=True,
    )
    assert abs(_first_pts(out, "v:0") - _first_pts(out, "a:0")) < 0.2
