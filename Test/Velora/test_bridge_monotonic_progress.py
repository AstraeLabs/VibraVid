# 19.10.26

"""The downloaded-bytes figure shown for a track never goes backwards when a segment completes."""

import io
import json

from VibraVid.core.velora import bridge

MIB = 1024 * 1024
_UNITS = {"G": 1024**3, "M": MIB, "K": 1024}


def _run(monkeypatch, events, n_tasks):
    payload = "\n".join(json.dumps(e) for e in events) + "\n"

    class FakePopen:
        def __init__(self, *args, **kwargs):
            self.stdout = io.StringIO(payload)
            self.stdin = io.StringIO()
            self.returncode = 0

        def poll(self):
            return self.returncode

        def wait(self, timeout=None):
            return self.returncode

    monkeypatch.setattr(bridge, "get_velora_path", lambda: "fake-velora")
    monkeypatch.setattr(bridge.subprocess, "Popen", FakePopen)

    plan = {"tasks": [{"path": f"/tmp/seg_{i:05d}.mp4", "url": f"https://cdn.example.test/{i}"} for i in range(n_tasks)]}
    cb_bytes, shown = [], []

    def event_cb(event):
        size = event.get("size")
        if size:
            number = size.split("/")[0]
            shown.append(float(number[:-1]) * _UNITS[number[-1]] if number[-1] in _UNITS else float(number))

    bridge.run_download_plan(plan, progress_cb=lambda done, total, total_bytes, speed: cb_bytes.append(total_bytes), event_cb=event_cb)
    return cb_bytes, shown


def _completed(i, size):
    # Like the real Velora: the completed event carries its own "size" (its ledger of finished segments)
    return {"event": "completed", "path": f"/tmp/seg_{i:05d}.mp4", "bytes": size, "size": f"{(i + 1) * size / MIB:.1f}M/192.0M"}


def test_completed_segment_does_not_pull_the_figure_back_below_the_in_flight_bytes(monkeypatch):
    # 50 MiB already received (two ranges still in flight), then the first 16 MiB range completes
    events = [
        {"event": "progress", "total_bytes": 50 * MIB},
        _completed(0, 16 * MIB),
        {"event": "progress", "total_bytes": 60 * MIB},
        _completed(1, 16 * MIB),
        _completed(2, 16 * MIB),
        _completed(3, 16 * MIB),
    ]

    cb_bytes, shown = _run(monkeypatch, events, 4)

    assert cb_bytes == sorted(cb_bytes), cb_bytes
    assert shown == sorted(shown), shown
    assert cb_bytes[-1] == 64 * MIB  # the ledger takes over once it overtakes the in-flight figure


def test_figure_keeps_the_live_high_water_mark_between_progress_ticks(monkeypatch):
    events = [
        {"event": "progress", "total_bytes": 100 * MIB},
        _completed(0, 16 * MIB),
        _completed(1, 16 * MIB),
    ]

    cb_bytes, _ = _run(monkeypatch, events, 2)

    assert min(cb_bytes) == 100 * MIB
