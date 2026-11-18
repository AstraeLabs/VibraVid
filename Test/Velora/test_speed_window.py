# 05.10.26
# ruff: noqa: E402

"""SpeedWindow replaces the hand-rolled moving-window speed code that bridge.py and curl_bridge.py each carried."""

import sys
from pathlib import Path

workspace_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(workspace_root))

import pytest

from VibraVid.core.velora.util import formatting
from VibraVid.core.velora.util.formatting import SpeedWindow


@pytest.fixture
def clock(monkeypatch):
    now = [100.0]
    monkeypatch.setattr(formatting.time, "monotonic", lambda: now[0])
    return now


def _legacy(samples, window=3.0, start=100.0):
    """The exact algorithm the bridges used before (reference implementation)."""
    from collections import deque

    speed_window = deque([(start, 0)])
    out = []
    for now, total in samples:
        speed_window.append((now, total))
        while len(speed_window) > 1 and now - speed_window[0][0] > window:
            speed_window.popleft()
        window_start_at, window_start_bytes = speed_window[0]
        out.append((total - window_start_bytes) / max(now - window_start_at, 0.001))
    return out


def test_speed_over_the_whole_history_while_inside_the_window(clock):
    meter = SpeedWindow()
    clock[0] = 101.0
    assert meter.update(1000) == pytest.approx(1000.0)
    clock[0] = 102.0
    assert meter.update(3000) == pytest.approx(1500.0)


def test_old_samples_leave_the_window(clock):
    meter = SpeedWindow(window=3.0)
    for t, total in [(101.0, 1000), (102.0, 2000), (103.0, 3000), (104.0, 4000)]:
        clock[0] = t
        speed = meter.update(total)
    # sample at t=100 (0 bytes) is older than 3 s at t=104 -> window starts at t=101 (1000 bytes)
    assert speed == pytest.approx((4000 - 1000) / 3.0)


def test_zero_elapsed_time_does_not_divide_by_zero(clock):
    meter = SpeedWindow()
    assert meter.update(500) == pytest.approx(500 / 0.001)


@pytest.mark.parametrize(
    "samples",
    [
        [(100.5, 10), (101.0, 20), (101.2, 20), (103.9, 400), (104.5, 400), (110.0, 900)],
        [(100.0, 0), (100.0, 5)],
        [(101.0, 1), (105.0, 2), (109.0, 3), (109.1, 3)],
    ],
)
def test_matches_the_legacy_implementation(clock, samples):
    meter = SpeedWindow()
    got = []
    for now, total in samples:
        clock[0] = now
        got.append(meter.update(total))
    assert got == pytest.approx(_legacy(samples))
