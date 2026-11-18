# 23.10.26

"""Live merge must survive being closed while a decrypt worker is still appending (Netflix 'write to closed file' -> 80s fallback re-merge)."""

import logging
import queue
import threading
import time
from types import SimpleNamespace

from VibraVid.core.velora._decrypt_pipeline.live_merge import _LiveMerger
from VibraVid.core.velora._decrypt_pipeline.pipeline import DecryptPipelineMixin


def _segments(tmp_path, count):
    paths = []
    for i in range(count):
        p = tmp_path / f"seg_{i:05d}.mp4"
        p.write_bytes(bytes([i]) * 64)
        paths.append(p)
    return paths


def test_submit_after_close_is_ignored(tmp_path, caplog):
    segs = _segments(tmp_path, 3)
    merger = _LiveMerger(tmp_path / "out.mp4", [0, 1, 2])
    merger.submit(0, segs[0])
    merger.close()

    with caplog.at_level(logging.WARNING):
        merger.submit(1, segs[1])

    assert not merger.failed
    assert not merger.complete
    assert not [r for r in caplog.records if "live_merge" in r.getMessage()]


def test_close_waits_for_in_flight_submit(tmp_path):
    segs = _segments(tmp_path, 2)
    entered, release = threading.Event(), threading.Event()

    def on_chunk(_chunk):
        entered.set()
        release.wait(5)

    merger = _LiveMerger(tmp_path / "out.mp4", [0, 1], on_chunk=on_chunk)
    worker = threading.Thread(target=merger.submit, args=(0, segs[0]))
    worker.start()
    assert entered.wait(5)  # worker is mid-segment, holding the output open

    closer = threading.Thread(target=merger.close)
    closer.start()
    time.sleep(0.2)
    assert closer.is_alive()  # close() must not pull the file out from under the worker

    release.set()
    worker.join(5)
    closer.join(5)

    assert not merger.failed  # the in-flight segment was written completely, no 'write to closed file'
    assert (tmp_path / "out.mp4").read_bytes() == segs[0].read_bytes()


def test_normal_completion_unchanged(tmp_path):
    segs = _segments(tmp_path, 3)
    merger = _LiveMerger(tmp_path / "out.mp4", [0, 1, 2])
    for i in (2, 0, 1):  # out-of-order arrival
        merger.submit(i, segs[i])

    assert merger.complete
    merger.close()
    assert (tmp_path / "out.mp4").read_bytes() == b"".join(p.read_bytes() for p in segs)


def _ctx(items, stuck=None, per_item=0.0):
    """A context stand-in with one decrypt worker running over *items* (None = stop sentinel)."""
    ctx = SimpleNamespace(protocol="dash", decrypt_queue=queue.Queue(), decrypt_stop=threading.Event(), decrypt_threads=[], done=[])

    def worker():
        while True:
            item = ctx.decrypt_queue.get()
            if item is None:
                return
            if ctx.decrypt_stop.is_set():
                continue
            if stuck is not None and item == 0:
                stuck.wait(5)
            time.sleep(per_item)
            ctx.done.append(item)

    for item in items:
        ctx.decrypt_queue.put(item)
    ctx.decrypt_queue.put(None)
    t = threading.Thread(target=worker, daemon=True)
    ctx.decrypt_threads.append(t)
    t.start()
    return ctx


def _pipeline():
    return object.__new__(DecryptPipelineMixin)


def test_drain_waits_while_queue_keeps_shrinking():
    ctx = _ctx(range(8), per_item=0.15)  # ~1.2s total, longer than the stall timeout
    started = time.monotonic()
    _pipeline()._drain_decrypt_workers(ctx, stall_timeout=0.5)

    assert time.monotonic() - started > 0.5
    assert ctx.done == list(range(8))
    assert not ctx.decrypt_stop.is_set()


def test_drain_stops_stalled_workers():
    stuck = threading.Event()
    ctx = _ctx(range(5), stuck=stuck)
    _pipeline()._drain_decrypt_workers(ctx, stall_timeout=0.3)

    assert ctx.decrypt_stop.is_set()
    stuck.set()
    ctx.decrypt_threads[0].join(5)
    assert not ctx.decrypt_threads[0].is_alive()
    assert ctx.done == [0]  # only the in-flight segment finished, the rest was skipped
