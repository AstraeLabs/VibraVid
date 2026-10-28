import hashlib
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from VibraVid.core.decryptor import decryptor as decryptor_module
from VibraVid.core.downloader.base import BaseDownloader
from VibraVid.core.ui import tracker
from VibraVid.core.velora._decrypt_pipeline import chunks_pipe as pipe_module
from VibraVid.core.velora._decrypt_pipeline import streaming_mux as mux_module
from VibraVid.core.velora._decrypt_pipeline.pipeline import DecryptPipelineMixin
from VibraVid.core.velora._decrypt_pipeline.streaming_mux import StreamingMuxMixin, StreamingMuxPlan

FLUX = "FAKE_FLUX"
FFMPEG = "FAKE_FFMPEG"

# Stand-ins for the two real binaries, run through the interpreter so the test needs neither flux nor ffmpeg
# (and behaves the same on Windows, Linux and macOS): flux writes a known payload on stdout, ffmpeg hashes its
# stdin into the output file and prints `-progress` lines like the real one.
FLUX_SCRIPT = r"""
import os, sys, time
if os.environ.get("FAKE_FLUX_HANG"):
    time.sleep(120)
size = int(os.environ.get("FAKE_PAYLOAD", "0"))
block = bytes(range(256)) * 4096
sent = 0
while sent < size:
    chunk = block[: min(len(block), size - sent)]
    sys.stdout.buffer.write(chunk)
    sent += len(chunk)
sys.stdout.buffer.flush()
sys.stderr.write("[profile] fake flux\n")
sys.exit(int(os.environ.get("FAKE_FLUX_RC", "0")))
"""
FFMPEG_SCRIPT = r"""
import hashlib, os, sys
digest = hashlib.sha256()
total = 0
while True:
    data = sys.stdin.buffer.read(1 << 20)
    if not data:
        break
    digest.update(data)
    total += len(data)
    print(f"total_size={total}", flush=True)
print("total_size=N/A", flush=True)
print("progress=end", flush=True)
with open(sys.argv[-1], "w") as fh:
    fh.write(digest.hexdigest())
sys.exit(int(os.environ.get("FAKE_FFMPEG_RC", "0")))
"""


def _payload(size: int) -> bytes:
    block = bytes(range(256)) * 4096
    return (block * (size // len(block) + 1))[:size]


@pytest.fixture
def fake_processes(monkeypatch):
    real_popen = subprocess.Popen
    scripts = {FLUX: FLUX_SCRIPT, FFMPEG: FFMPEG_SCRIPT}

    def popen(cmd, **kwargs):
        if cmd[0] in scripts:
            cmd = [sys.executable, "-I", "-c", scripts[cmd[0]], *cmd[1:]]
        return real_popen(cmd, **kwargs)

    monkeypatch.setattr(pipe_module.subprocess, "Popen", popen)
    monkeypatch.setattr(pipe_module, "_tail_decode_check", lambda *a, **k: None)
    monkeypatch.setenv("FAKE_PAYLOAD", str(3 * 1024 * 1024 + 17))


def _host(tmp_path: Path, chapters_injected: bool = True) -> DecryptPipelineMixin:
    out = str(tmp_path / "out.mkv")
    host = object.__new__(DecryptPipelineMixin)
    host.key = "kid:key"
    host.key_fallback = None
    host._streaming_mux_output_path = out
    host.streaming_mux_result = None
    host.streaming_mux_chapters_injected = False
    host._keep_tmp_on_error = lambda: False
    host._resolve_norm_keys = lambda *a, **k: [("a" * 32, "b" * 32)]
    host._flux_path = lambda: FLUX
    plan = StreamingMuxPlan(cmd=[FFMPEG, "-i", "-", out], output_path=out, chapters_injected=chapters_injected)
    host._prepare_streaming_mux = lambda *a, **k: plan
    return host


def _ctx(tmp_path: Path, progress=None) -> tuple[SimpleNamespace, list[Path]]:
    seg = tmp_path / "seg_00000.mp4"
    seg.write_bytes(b"x" * 1024)
    handle = progress if progress is not None else (lambda line: None)
    ctx = SimpleNamespace(
        stream=SimpleNamespace(type="video"),
        stream_dir=tmp_path,
        task_key="vid",
        bar_manager=SimpleNamespace(handle_progress_line=handle),
    )
    return ctx, [seg]


def test_pipe_delivers_flux_stdout_to_ffmpeg_untouched(tmp_path, fake_processes):
    host = _host(tmp_path)
    updates = []
    ctx, paths = _ctx(tmp_path, progress=updates.append)

    assert host._try_chunks_pipe_mux(ctx, paths, tmp_path / "v.mp4")

    out = tmp_path / "out.mkv"
    assert out.read_text() == hashlib.sha256(_payload(3 * 1024 * 1024 + 17)).hexdigest()
    assert host.streaming_mux_result == str(out)
    assert host.streaming_mux_chapters_injected is True
    assert updates and all(u["task_key"] == "vid" and u["pct"] <= 99 for u in updates)


def test_chapters_flag_follows_the_plan(tmp_path, fake_processes):
    host = _host(tmp_path, chapters_injected=False)
    ctx, paths = _ctx(tmp_path)
    assert host._try_chunks_pipe_mux(ctx, paths, tmp_path / "v.mp4")
    assert host.streaming_mux_chapters_injected is False


def test_progress_ui_failure_does_not_fail_or_redo_the_mux(tmp_path, fake_processes):
    host = _host(tmp_path)

    def exploding(_line):
        raise RuntimeError("UI is gone")

    ctx, paths = _ctx(tmp_path, progress=exploding)
    assert host._try_chunks_pipe_mux(ctx, paths, tmp_path / "v.mp4")
    assert host.streaming_mux_result == str(tmp_path / "out.mkv")


def test_flux_failure_falls_back_and_removes_the_partial_mkv(tmp_path, fake_processes, monkeypatch):
    monkeypatch.setenv("FAKE_FLUX_RC", "3")
    host = _host(tmp_path)
    ctx, paths = _ctx(tmp_path)

    assert not host._try_chunks_pipe_mux(ctx, paths, tmp_path / "v.mp4")
    assert not (tmp_path / "out.mkv").exists()
    assert host.streaming_mux_result is None


def test_ffmpeg_failure_falls_back_and_removes_the_partial_mkv(tmp_path, fake_processes, monkeypatch):
    monkeypatch.setenv("FAKE_FFMPEG_RC", "1")
    host = _host(tmp_path)
    ctx, paths = _ctx(tmp_path)

    assert not host._try_chunks_pipe_mux(ctx, paths, tmp_path / "v.mp4")
    assert not (tmp_path / "out.mkv").exists()
    assert host.streaming_mux_result is None


def test_decode_check_failure_discards_the_output(tmp_path, fake_processes, monkeypatch):
    monkeypatch.setattr(pipe_module, "_tail_decode_check", lambda *a, **k: "Invalid data found")
    host = _host(tmp_path)
    ctx, paths = _ctx(tmp_path)

    assert not host._try_chunks_pipe_mux(ctx, paths, tmp_path / "v.mp4")
    assert not (tmp_path / "out.mkv").exists()


def test_a_stuck_pipe_is_killed_instead_of_waited_for(tmp_path, fake_processes, monkeypatch):
    monkeypatch.setenv("FAKE_FLUX_HANG", "1")
    monkeypatch.setattr(pipe_module, "_PIPE_IDLE_TIMEOUT_SECONDS", 1.0)
    host = _host(tmp_path)
    ctx, paths = _ctx(tmp_path)

    started = time.monotonic()
    assert not host._try_chunks_pipe_mux(ctx, paths, tmp_path / "v.mp4")
    assert time.monotonic() - started < 30


def test_unstartable_ffmpeg_stops_flux_and_falls_back(tmp_path, fake_processes):
    host = _host(tmp_path)
    host._prepare_streaming_mux = lambda *a, **k: StreamingMuxPlan(
        cmd=[str(tmp_path / "no-such-ffmpeg")], output_path=str(tmp_path / "out.mkv"), chapters_injected=False
    )
    ctx, paths = _ctx(tmp_path)

    assert not host._try_chunks_pipe_mux(ctx, paths, tmp_path / "v.mp4")


def test_no_usable_key_or_plan_means_no_pipe(tmp_path, fake_processes):
    ctx, paths = _ctx(tmp_path)

    host = _host(tmp_path)
    host._resolve_norm_keys = lambda *a, **k: None
    assert not host._try_chunks_pipe_mux(ctx, paths, tmp_path / "v.mp4")

    host = _host(tmp_path)
    host._prepare_streaming_mux = lambda *a, **k: None
    assert not host._try_chunks_pipe_mux(ctx, paths, tmp_path / "v.mp4")


# --- eligibility -----------------------------------------------------------------------------------------------


def _eligible_host(**attrs) -> DecryptPipelineMixin:
    host = object.__new__(DecryptPipelineMixin)
    host.key = "kid:key"
    host.key_fallback = None
    host.output_path = None
    host._streaming_mux_output_path = "/out.mkv"
    host.flux_path = "flux"
    host.other_tracks = None
    for name, value in attrs.items():
        setattr(host, name, value)
    return host


@pytest.fixture
def pipe_allowed(monkeypatch):
    monkeypatch.setattr(tracker.context_tracker, "no_livemux", False)
    monkeypatch.setattr(tracker.context_tracker, "no_concurrent", False)
    monkeypatch.setattr(decryptor_module, "flux_supports_tfdt_rebase", lambda path: True)


def test_eligible_when_every_condition_holds(pipe_allowed):
    assert _eligible_host()._chunks_pipe_eligible()


def test_not_eligible_with_no_livemux(pipe_allowed, monkeypatch):
    monkeypatch.setattr(tracker.context_tracker, "no_livemux", True)
    assert not _eligible_host()._chunks_pipe_eligible()


def test_not_eligible_with_no_concurrent_because_the_audio_would_never_start(pipe_allowed, monkeypatch):
    monkeypatch.setattr(tracker.context_tracker, "no_concurrent", True)
    assert not _eligible_host()._chunks_pipe_eligible()


def test_not_eligible_when_output_is_not_mkv_or_missing(pipe_allowed):
    assert not _eligible_host(_streaming_mux_output_path="/out.mp4")._chunks_pipe_eligible()
    assert not _eligible_host(_streaming_mux_output_path=None, output_path=None)._chunks_pipe_eligible()


def test_output_path_is_the_fallback_when_streaming_mux_was_not_enabled(pipe_allowed):
    assert _eligible_host(_streaming_mux_output_path=None, output_path="/final.mkv")._chunks_pipe_eligible()


def test_not_eligible_with_other_tracks(pipe_allowed):
    assert not _eligible_host(other_tracks=[SimpleNamespace()])._chunks_pipe_eligible()


def test_not_eligible_when_flux_cannot_rebase_tfdt(pipe_allowed, monkeypatch):
    monkeypatch.setattr(decryptor_module, "flux_supports_tfdt_rebase", lambda path: False)
    assert not _eligible_host()._chunks_pipe_eligible()


def test_flux_053_has_chunks_but_not_the_pipe(monkeypatch, tmp_path):
    fake = tmp_path / "flux.exe"
    fake.write_bytes(b"")
    monkeypatch.setattr(decryptor_module, "_flux_version", lambda path: (0, 5, 3))
    assert decryptor_module.flux_supports_chunks(str(fake))
    assert not decryptor_module.flux_supports_tfdt_rebase(str(fake))
    monkeypatch.setattr(decryptor_module, "_flux_version", lambda path: (0, 5, 4))
    assert decryptor_module.flux_supports_tfdt_rebase(str(fake))


def test_flux_path_prefers_the_configured_attribute():
    assert _eligible_host(flux_path="/opt/flux")._flux_path() == "/opt/flux"


@pytest.mark.parametrize("chunks_pipe, asked", [(False, True), (True, False)])
def test_only_the_in_flight_mux_asks_the_live_mux_switch(monkeypatch, caplog, chunks_pipe, asked):
    """The pipe is on by default and honours --no-livemux in _chunks_pipe_eligible; asking process.live_mux again would silently drop every default run to the file mode."""
    monkeypatch.setattr(mux_module, "_streaming_mux_enabled", lambda *a: False)
    host = SimpleNamespace(site_name=None, _streaming_mux_output_path="out.mp4")  # not .mkv: stops right after the switch either way

    with caplog.at_level("INFO"):
        plan = StreamingMuxMixin._prepare_streaming_mux(host, "mp4", object(), chunks_pipe=chunks_pipe)

    assert plan is None
    assert ("switched off for this run" in caplog.text) is asked


def test_a_video_only_pipe_output_is_media_not_an_empty_download():
    """The pipe consumes the video, so with no audio or subtitle file left the status looks empty although the final MKV exists."""
    empty = {"video": None, "audios": [], "subtitles": [], "external_subtitles": []}
    piped = SimpleNamespace(media_downloader=SimpleNamespace(streaming_mux_result="out.mkv"))
    nothing = SimpleNamespace(media_downloader=SimpleNamespace(streaming_mux_result=None))

    assert BaseDownloader._no_media_downloaded(piped, dict(empty)) is False
    assert BaseDownloader._no_media_downloaded(nothing, dict(empty)) is True
