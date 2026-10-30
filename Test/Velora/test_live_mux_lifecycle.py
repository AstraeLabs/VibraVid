"""LiveMux lifecycle: rendezvous of the tracks, a track that never has data, abort, and a stop (Ctrl+C) in the middle of a recording."""

import threading
from types import SimpleNamespace

import pytest

from VibraVid.core.velora import _live_mux
from VibraVid.core.velora._live_mux import LiveMux


class _FakeMuxer:
    """Stands in for NamedPipeMuxer: no ffmpeg, no pipes. Records what the tracks wrote."""

    def __init__(self, n_pipes):
        self.n_pipes = n_pipes
        self.pipe_paths = [f"pipe{i}" for i in range(n_pipes)]
        self.writes = [[] for _ in range(n_pipes)]
        self.closed = []
        self.started = 0
        self.aborted = 0
        self.finished = 0
        self.output_to_create = None

    def prepare(self):
        pass

    def start_ffmpeg(self, cmd):
        self.started += 1

    def get_writer(self, index):
        return self.writes[index].append

    def close_writer(self, index):
        self.closed.append(index)

    def abort(self):
        self.aborted += 1

    def finish(self):
        self.finished += 1
        if self.output_to_create:
            self.output_to_create.write_bytes(b"mkv")
        return SimpleNamespace(ok=True, error=None, returncode=0, stderr_tail="")


def _track(kind, ident):
    return SimpleNamespace(type=kind, id=ident)


@pytest.fixture
def live_mux(tmp_path, monkeypatch):
    holder = {}

    def make_muxer(n_pipes):
        holder["muxer"] = _FakeMuxer(n_pipes)
        holder["muxer"].output_to_create = tmp_path / "out.mkv"
        return holder["muxer"]

    monkeypatch.setattr(_live_mux, "NamedPipeMuxer", make_muxer)
    monkeypatch.setattr(_live_mux, "_run_flux_dump", lambda path: {"streams": [{"stream_type": "video"}, {"stream_type": "audio"}]})
    monkeypatch.setattr(LiveMux, "_build_cmd", lambda self: ["ffmpeg"])
    video, audio = _track("video", "v"), _track("audio", "a")
    mux = LiveMux(owner=None, tracks=[video, audio], output_path=str(tmp_path / "out.mkv"))
    init = tmp_path / "init.mp4"
    init.write_bytes(b"INIT")
    return SimpleNamespace(mux=mux, muxer=holder["muxer"], video=video, audio=audio, init=init)


def _run(*targets, timeout=10):
    """Run each target in its own thread; fail the test if one is still alive after *timeout* (a hang)."""
    threads = [threading.Thread(target=target, daemon=True) for target in targets]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout)
    assert not any(thread.is_alive() for thread in threads), "a track thread is stuck"


def test_ffmpeg_starts_when_every_track_joined_and_finishes_when_everyone_left(live_mux):
    sent = {}

    def track(stream):
        def run():
            send = live_mux.mux.join(stream, live_mux.init, lambda: False, rebased=True)
            send(b"seg1")
            send(b"seg2")
            sent[stream.type] = True
            live_mux.mux.leave(stream)

        return run

    _run(track(live_mux.video), track(live_mux.audio))

    assert live_mux.muxer.started == 1
    assert live_mux.muxer.writes[0] == [b"INIT", b"seg1", b"seg2"]
    assert live_mux.muxer.writes[1] == [b"INIT", b"seg1", b"seg2"]
    assert sorted(live_mux.muxer.closed) == [0, 1]
    assert live_mux.muxer.finished == 1
    assert live_mux.mux.result == str(live_mux.init.parent / "out.mkv")


def test_a_track_that_never_has_data_gives_the_live_mux_up_without_hanging(live_mux):
    results = {}

    def video():
        results["video"] = live_mux.mux.join(live_mux.video, live_mux.init, lambda: live_mux.muxer.aborted > 0)
        live_mux.mux.leave(live_mux.video)

    def audio():
        live_mux.mux.leave(live_mux.audio)

    _run(video, audio)

    assert results["video"] is None
    assert live_mux.muxer.started == 0
    assert live_mux.muxer.aborted >= 1
    assert live_mux.mux.result is None


def test_join_after_an_abort_is_refused(live_mux):
    live_mux.mux.abort("test")

    assert live_mux.mux.join(live_mux.video, live_mux.init, lambda: False) is None
    assert not live_mux.mux.is_active()


def test_abort_twice_aborts_the_muxer_once(live_mux):
    live_mux.mux.abort("first")
    live_mux.mux.abort("second")

    assert live_mux.muxer.aborted == 1


def test_an_invalid_init_gives_the_live_mux_up(live_mux, monkeypatch):
    monkeypatch.setattr(_live_mux, "_run_flux_dump", lambda path: None)

    assert live_mux.mux.join(live_mux.video, live_mux.init, lambda: False) is None
    assert live_mux.muxer.aborted == 1


def test_stop_in_the_middle_closes_what_was_recorded_and_produces_the_file(live_mux):
    """Ctrl+C: the tracks stop at different moments; the data already piped is kept and ffmpeg is asked to finish once."""
    video_stopped = threading.Event()

    def video():
        send = live_mux.mux.join(live_mux.video, live_mux.init, lambda: False, rebased=True)
        send(b"v1")
        live_mux.mux.leave(live_mux.video)
        video_stopped.set()

    def audio():
        send = live_mux.mux.join(live_mux.audio, live_mux.init, lambda: False, rebased=True)
        send(b"a1")
        video_stopped.wait(5)
        send(b"a2")
        live_mux.mux.leave(live_mux.audio)

    _run(video, audio)

    assert live_mux.muxer.writes[0] == [b"INIT", b"v1"]
    assert live_mux.muxer.writes[1] == [b"INIT", b"a1", b"a2"]
    assert live_mux.muxer.finished == 1
    assert live_mux.muxer.aborted == 0
    assert live_mux.mux.result is not None


def test_stop_during_the_backlog_falls_back_without_a_result(live_mux):
    """Ctrl+C before the second track had data: the joined track gives up waiting, nothing is piped, the per-track files stay for the normal mux."""
    stop = threading.Event()
    stop.set()
    outcome = {}

    def video():
        outcome["send"] = live_mux.mux.join(live_mux.video, live_mux.init, stop.is_set)
        live_mux.mux.leave(live_mux.video)

    def audio():
        live_mux.mux.leave(live_mux.audio)

    _run(video, audio)

    assert outcome["send"] is None
    assert live_mux.muxer.started == 0
    assert live_mux.mux.result is None
    assert live_mux.muxer.writes == [[], []]
