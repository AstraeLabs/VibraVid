# 24.10.26

"""The streaming mux waits for the other tracks while the scratch folder still grows; a slow disk must not trigger the fallback that re-does all the work."""

import threading
import time

import pytest

from VibraVid.core.velora._decrypt_pipeline import streaming_mux
from VibraVid.core.velora._decrypt_pipeline.streaming_mux import StreamingMuxMixin


class Waiter(StreamingMuxMixin):
    def __init__(self, output_dir):
        self.output_dir = str(output_dir)
        self._events = {}
        self._abort_event = threading.Event()

    def _track_done_event(self, key):
        return self._events.setdefault(key, threading.Event())


@pytest.fixture(autouse=True)
def fast_polling(monkeypatch):
    monkeypatch.setattr(streaming_mux, "STREAMING_MUX_POLL_SECONDS", 0.05)


def test_returns_true_as_soon_as_the_track_is_done(tmp_path):
    w = Waiter(tmp_path)
    threading.Timer(0.15, w._track_done_event("aud").set).start()

    assert w._wait_for_track("aud", idle_timeout=5.0) is True


def test_a_growing_scratch_folder_keeps_the_wait_alive_past_the_idle_timeout(tmp_path):
    w = Waiter(tmp_path)
    done = w._track_done_event("aud")

    def slow_merge():
        with open(tmp_path / "audio.m4a", "ab") as fh:
            for _ in range(20):
                fh.write(b"x" * 1024)
                fh.flush()
                time.sleep(0.05)
        done.set()

    threading.Thread(target=slow_merge, daemon=True).start()

    # the merge lasts ~1 s, five times the idle timeout: only the lack of progress may end the wait
    assert w._wait_for_track("aud", idle_timeout=0.3) is True


def test_gives_up_when_nothing_progresses(tmp_path, caplog):
    w = Waiter(tmp_path)
    (tmp_path / "audio.m4a").write_bytes(b"x")

    started = time.monotonic()
    assert w._wait_for_track("aud", idle_timeout=0.3) is False

    assert time.monotonic() - started < 3
    assert "no progress" in caplog.text


def test_gives_up_at_the_total_cap_even_while_growing(tmp_path, monkeypatch):
    monkeypatch.setattr(streaming_mux, "STREAMING_MUX_MAX_TOTAL_WAIT_SECONDS", 0.4)
    w = Waiter(tmp_path)
    stop = threading.Event()

    def grow():
        with open(tmp_path / "audio.m4a", "ab") as fh:
            while not stop.is_set():
                fh.write(b"x")
                fh.flush()
                time.sleep(0.02)

    threading.Thread(target=grow, daemon=True).start()
    try:
        assert w._wait_for_track("aud", idle_timeout=30.0) is False
    finally:
        stop.set()


def test_an_aborted_download_stops_the_wait(tmp_path):
    w = Waiter(tmp_path)
    w._abort_event.set()

    assert w._wait_for_track("aud", idle_timeout=30.0) is False
