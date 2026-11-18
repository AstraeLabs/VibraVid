# 06.10.26

"""Helpers that replaced copy-pasted closures: the decrypt-phase bar callback, the live batch progress callback and the MP4 box walker."""

import struct

from VibraVid.core.ui.bar_manager import DownloadBarManager
from VibraVid.core.velora import _ism_postproc
from VibraVid.core.velora.downloader_live import _make_batch_progress_cb
from VibraVid.core.velora.util import _cenc_init


class _Bar:
    def __init__(self):
        self.lines = []

    def handle_progress_line(self, parsed):
        self.lines.append(parsed)


def test_decrypt_progress_cb_continues_the_track_row():
    bar = _Bar()
    callback = DownloadBarManager.decrypt_progress_cb(bar, "vid_0")

    callback({"pct": 40, "status": "CTR"})
    callback({"pct": 80})
    callback(None)
    callback({})

    assert bar.lines == [
        {"task_key": "vid_0", "pct": 40, "speed": "CTR"},
        {"task_key": "vid_0", "pct": 80, "speed": "Decrypt"},
    ]


def test_live_batch_progress_offsets_by_what_previous_batches_downloaded(monkeypatch):
    emitted = []
    monkeypatch.setattr("VibraVid.core.velora.downloader_live._emit_live_progress", lambda *args: emitted.append(args))
    bar = object()

    callback = _make_batch_progress_cb(bar, "aud_0", segs_before=10, bytes_before=1000, elapsed_before=20.0, avg_dur=2.0)
    callback(3, 5, 300, 123.0)
    callback(4, 5, 400, 50.0, speed_label="x")

    assert emitted == [
        (bar, "aud_0", 13, 1300, 123.0, 26.0),
        (bar, "aud_0", 14, 1400, 50.0, 28.0),
    ]


def _box(kind: bytes, payload: bytes = b"", large: bool = False) -> bytes:
    if large:
        return struct.pack(">I", 1) + kind + struct.pack(">Q", 16 + len(payload)) + payload
    return struct.pack(">I", 8 + len(payload)) + kind + payload


def test_box_walker_is_single_sourced_and_handles_32_and_64_bit_sizes():
    assert _ism_postproc._iter_boxes is _cenc_init._iter_boxes

    buf = _box(b"ftyp", b"abcd") + _box(b"moov", b"xy", large=True) + _box(b"free")
    boxes = list(_cenc_init._iter_boxes(memoryview(buf), 0, len(buf)))

    assert [(typ, hdr) for _, _, typ, hdr in boxes] == [(b"ftyp", 8), (b"moov", 16), (b"free", 8)]
    assert [off for off, *_ in boxes] == [0, 12, 30]


def test_box_walker_stops_at_a_truncated_box():
    buf = _box(b"ftyp", b"abcd") + struct.pack(">I", 999) + b"moov"

    assert [typ for _, _, typ, _ in _cenc_init._iter_boxes(memoryview(buf), 0, len(buf))] == [b"ftyp"]

