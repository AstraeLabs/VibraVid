# 06.10.26

"""First Ctrl+C: stop and merge what was downloaded. A further Ctrl+C: abort at once, no merge, no half-written output."""

import subprocess
import sys
import threading
import time

import pytest

from VibraVid.core.muxing import capture
from VibraVid.core.velora.downloader import MediaDownloader


def _downloader():
    """A MediaDownloader with only the state the post-interrupt wait touches (no manifest, no network)."""
    d = object.__new__(MediaDownloader)
    d._abort_event = threading.Event()
    d._stop_event = threading.Event()
    d._active_loops = []
    d._loops_lock = threading.Lock()
    d.download_id = None
    return d


class _SlowThread:
    """Looks alive forever; ``join`` raises KeyboardInterrupt on the n-th call, like a second Ctrl+C."""

    def __init__(self, interrupt_on_call=1):
        self.calls = 0
        self.interrupt_on_call = interrupt_on_call

    def is_alive(self):
        return True

    def join(self, timeout=None):
        self.calls += 1
        if self.calls >= self.interrupt_on_call:
            raise KeyboardInterrupt


def test_waiting_for_finished_threads_returns_true_and_does_not_abort():
    d = _downloader()
    worker = threading.Thread(target=lambda: time.sleep(0.05), daemon=True)
    worker.start()

    assert d._wait_after_interrupt([worker]) is True
    assert not d._abort_event.is_set()


def test_second_ctrl_c_during_the_wait_aborts_and_flags_the_merge_skip():
    d = _downloader()

    assert d._wait_after_interrupt([_SlowThread()]) is False
    assert d._abort_event.is_set()


def test_the_wait_is_polled_in_short_slices_so_ctrl_c_stays_deliverable():
    d = _downloader()
    slow = _SlowThread(interrupt_on_call=3)

    assert d._wait_after_interrupt([slow], max_wait=5.0, poll=0.01) is False
    assert slow.calls == 3


def test_a_slow_track_is_waited_for_without_a_per_thread_cap(capsys):
    """The 25 GB video of a stopped download needs longer than the old 30 s to finalize: the wait goes on and says so."""
    d = _downloader()
    worker = threading.Thread(target=lambda: time.sleep(0.4), daemon=True)
    worker.start()

    assert d._wait_after_interrupt([worker], max_wait=5.0, poll=0.02, notice_every=0.1) is True
    assert not d._abort_event.is_set()
    assert "Finalizing" in capsys.readouterr().out


def test_a_track_that_never_finishes_is_not_muxed_after_the_absolute_ceiling(capsys):
    d = _downloader()
    forever = threading.Thread(target=lambda: time.sleep(5), daemon=True)
    forever.start()

    assert d._wait_after_interrupt([forever], max_wait=0.2, poll=0.02, notice_every=10.0) is False
    assert d._abort_event.is_set()
    assert "still" in capsys.readouterr().out.lower()


def test_ctrl_c_while_ffmpeg_runs_stops_it_removes_the_partial_file_and_propagates(tmp_path, monkeypatch):
    partial = tmp_path / "out.mkv"
    partial.write_bytes(b"half written")
    spawned = []

    class InterruptedPopen(subprocess.Popen):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            spawned.append(self)

        def wait(self, timeout=None):
            if not getattr(self, "_raised", False):
                self._raised = True
                raise KeyboardInterrupt
            return super().wait(timeout)

    monkeypatch.setattr(capture.subprocess, "Popen", InterruptedPopen)

    with pytest.raises(KeyboardInterrupt):
        capture.capture_ffmpeg_real_time(
            [sys.executable, "-c", "import time; time.sleep(60)"], "test", None, output_path=str(partial)
        )

    assert not partial.exists()
    assert spawned and spawned[0].poll() is not None  # the child process is gone
