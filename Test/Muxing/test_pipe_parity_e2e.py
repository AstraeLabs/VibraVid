# 28.10.26

"""The streaming mux (live mux and chunks pipe) muxing the same files as join_media must write the same chapters, the same cut and the same audio offsets.

Real ffmpeg, made-up chapters (unsorted, repeated, past the end) and made-up audio (longer than the video, delayed).
"""

import json
import subprocess
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from VibraVid.core.muxing import merge
from VibraVid.core.utils import track_order
from VibraVid.core.velora._decrypt_pipeline import DecryptPipelineMixin
from VibraVid.core.velora._decrypt_pipeline import streaming_mux as velora_mux
from VibraVid.core.velora.util.formatting import limit_requested
from VibraVid.setup import get_ffmpeg_path, get_ffprobe_path

try:
    FFMPEG, FFPROBE = get_ffmpeg_path(), get_ffprobe_path()
except Exception:
    FFMPEG = FFPROBE = None
pytestmark = pytest.mark.skipif(not (FFMPEG and FFPROBE and Path(FFMPEG).exists() and Path(FFPROBE).exists()), reason="ffmpeg/ffprobe not available")

DURATION = 30
DIRTY_CHAPTERS = [
    {"name": "Part 2", "seconds": 10},
    {"name": "Part 1", "seconds": 3},
    {"name": "Part 2 again", "seconds": 10},
    {"name": "Past the end", "seconds": 999},
]


def _ffmpeg(*args):
    subprocess.run([FFMPEG, "-v", "error", "-y", *args], check=True)


@pytest.fixture(scope="module")
def media(tmp_path_factory):
    folder = tmp_path_factory.mktemp("parity")
    noise = f"anoisesrc=d={DURATION}:c=white:r=16000:seed=7"
    _ffmpeg("-f", "lavfi", "-i", f"testsrc2=d={DURATION}:s=160x120:r=5", "-c:v", "libx264", "-preset", "ultrafast", str(folder / "video.mp4"))
    _ffmpeg("-i", str(folder / "video.mp4"), "-c", "copy", "-movflags", "frag_keyframe+empty_moov+default_base_moof", str(folder / "video_frag.mp4"))
    _ffmpeg("-f", "lavfi", "-i", noise, "-c:a", "aac", "-b:a", "64k", str(folder / "ref.m4a"))
    _ffmpeg("-f", "lavfi", "-i", noise, "-af", "adelay=5000:all=1", "-c:a", "aac", "-b:a", "64k", str(folder / "delayed.m4a"))  # the same sound 5 s later
    _ffmpeg("-f", "lavfi", "-i", noise, "-af", "atrim=start=5,asetpts=PTS-STARTPTS", "-c:a", "aac", "-b:a", "64k", str(folder / "advanced.m4a"))  # the same sound, 5 s of it already gone
    _ffmpeg("-f", "lavfi", "-i", f"sine=f=440:d={DURATION + 20}:r=16000", "-c:a", "aac", "-b:a", "64k", str(folder / "long.m4a"))  # 20 s past the video
    return folder


def _probe(path) -> dict:
    out = subprocess.run([FFPROBE, "-v", "error", "-show_streams", "-show_chapters", "-show_format", "-of", "json", str(path)], capture_output=True, text=True, check=True).stdout
    return json.loads(out)


def _summary(path) -> dict:
    data = _probe(path)
    audios = [s for s in data["streams"] if s["codec_type"] == "audio"]
    return {
        "chapters": [(round(float(c["start_time"]), 3), (c.get("tags") or {}).get("title")) for c in data.get("chapters", [])],
        "duration": round(float(data["format"]["duration"]), 1),
        "audio_starts": [round(float(s["start_time"]), 2) for s in audios],
        "audio_titles": [(s.get("tags") or {}).get("title") for s in audios],
    }


def _join_media(media, monkeypatch, tmp_path, audios, chapters=None) -> dict:
    monkeypatch.setattr(merge, "MUX_ENGINE", "ffmpeg")
    monkeypatch.setattr(merge, "_get_param_final", lambda: ["-c", "copy"])  # copy, whatever the config re-encodes with
    tracks = [{"path": str(media / f"{name}.m4a"), "name": lang, "language": lang} for name, lang in audios]
    out, result = merge.join_media(str(media / "video.mp4"), tracks, [], str(tmp_path / "join.mkv"), chapters=chapters)
    assert not result.get("exit_code"), result
    return _summary(out)


def _pipe(media, monkeypatch, tmp_path, audios, chapters=None) -> tuple[dict, list[str]]:
    """The streaming mux's own command, run for real with the video on stdin."""
    monkeypatch.setattr(velora_mux, "_streaming_mux_enabled", lambda *a: True)
    monkeypatch.setattr(velora_mux, "get_ffmpeg_path", lambda: FFMPEG)
    monkeypatch.setattr(velora_mux.config_manager.config, "get", lambda *a, **k: k.get("default", "auto"))

    done = threading.Event()
    done.set()
    streams, results = [], {}
    for name, lang in audios:
        streams.append(SimpleNamespace(
            id=name, type="audio", selected=True, is_external=False, segments=[], compute_estimated_size=lambda: 0,
            resolved_language=lang, language=lang[:2], name=lang,
        ))
        results[name] = media / f"{name}.m4a"

    host = object.__new__(DecryptPipelineMixin)
    host._streaming_mux_output_path = str(tmp_path / "pipe.mkv")
    host._streaming_mux_chapters = chapters
    host.temp_dir = str(tmp_path)
    host.other_tracks, host.external_subtitles, host.external_audios = [], [], []
    host.streams = streams
    host.custom_filters = {"audio": "best", "subtitle": "best"}
    host._stream_task_key = lambda stream: stream.id
    host._track_done_event = lambda key: done
    host._get_track_result = lambda key: results[key]

    video = SimpleNamespace(type="video", codecs="avc1.4d401f", width=160, height=120, duration=float(DURATION))
    plan = host._prepare_streaming_mux("mp4", video)
    assert plan is not None
    run = subprocess.run(plan.cmd, input=(media / "video_frag.mp4").read_bytes(), capture_output=True)
    assert run.returncode == 0, run.stderr.decode(errors="replace")[-600:]
    host._cleanup_streaming_mux_chapter_file()
    return _summary(plan.output_path), plan.cmd


AUDIO_REF = [("ref", "it-it")]


def _sidecar(folder) -> list[str]:
    """The chapters.txt a mux persists next to its temporary files: what was really prepared, before the container merges any twins."""
    return [line for line in (Path(folder) / "chapters.txt").read_text(encoding="utf-8").splitlines() if "NAME" not in line]


def test_the_pipe_prepares_dirty_chapters_like_join_media(media, monkeypatch, tmp_path):
    joined = _join_media(media, monkeypatch, tmp_path, AUDIO_REF, chapters=[dict(c) for c in DIRTY_CHAPTERS])
    joined_sidecar = _sidecar(media)
    piped, _cmd = _pipe(media, monkeypatch, tmp_path, AUDIO_REF, chapters=[dict(c) for c in DIRTY_CHAPTERS])

    assert len(joined_sidecar) == 3  # Intro at 0:00, Part 1, Part 2: sorted, the repeated one and the one past the end are gone
    assert _sidecar(tmp_path) == joined_sidecar
    assert piped["chapters"] == joined["chapters"]


def test_without_a_gap_the_pipe_cuts_nothing(media, monkeypatch, tmp_path):
    joined = _join_media(media, monkeypatch, tmp_path, AUDIO_REF)
    piped, cmd = _pipe(media, monkeypatch, tmp_path, AUDIO_REF)

    assert "-t" not in cmd and "-shortest" not in cmd
    assert piped["duration"] == joined["duration"]


def test_an_audio_far_longer_than_the_video_is_cut_to_the_video_like_join_media(media, monkeypatch, tmp_path):
    audios = [("ref", "it-it"), ("long", "en-us")]
    joined = _join_media(media, monkeypatch, tmp_path, audios)
    piped, cmd = _pipe(media, monkeypatch, tmp_path, audios)

    assert "-t" in cmd
    assert joined["duration"] == piped["duration"] == pytest.approx(DURATION, abs=0.6)  # not the 50 s of the long audio


def _misalignment(muxed, tmp_path) -> float:
    """Seconds the second audio of *muxed* is off the first, measured on what is in the file (``first_pts=0`` keeps the silence of a start offset)."""
    from VibraVid.core.muxing.helper.audio import detect_audio_offset

    wavs = []
    for index in (0, 1):
        wav = tmp_path / f"track{index}.wav"
        _ffmpeg("-i", str(muxed), "-map", f"0:a:{index}", "-af", "aresample=16000:async=1:first_pts=0", str(wav))
        wavs.append(str(wav))
    return detect_audio_offset(wavs[0], wavs[1])


@pytest.mark.parametrize("other", ["delayed", "advanced"])
def test_an_audio_out_of_sync_with_the_first_ends_up_aligned_whichever_mux_writes_it(media, monkeypatch, tmp_path, other):
    """The same sound 5 s late, or 5 s early: the offset is found against the first audio and applied the right way round, by join_media and by the pipe."""
    pytest.importorskip("scipy")
    audios = [("ref", "it-it"), (other, "en-us")]
    (tmp_path / "join").mkdir()
    (tmp_path / "pipe").mkdir()

    joined_summary = _join_media(media, monkeypatch, tmp_path / "join", audios)
    piped_summary, cmd = _pipe(media, monkeypatch, tmp_path / "pipe", audios)

    assert piped_summary["audio_starts"] == joined_summary["audio_starts"]
    assert "-itsoffset" in cmd or "-ss" in cmd
    assert abs(_misalignment(tmp_path / "join" / "join.mkv", tmp_path / "join")) < 0.1
    assert abs(_misalignment(tmp_path / "pipe" / "pipe.mkv", tmp_path / "pipe")) < 0.1


@pytest.mark.parametrize(
    "value,expected",
    [(None, False), (0, False), (5, True), ((0, None), False), ((0.0, None), False), ((0, 5), True), ((10, None), True), ((0.0, 90.0), True)],
)
def test_no_limit_is_not_a_limit(value, expected):
    """parse_max_time / parse_max_segments return (0, None) for "no limit": a non-empty tuple, truthy, which used to cut every live-mux download to the manifest's duration."""
    assert limit_requested(value) is expected
