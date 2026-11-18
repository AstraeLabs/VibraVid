# 01.10.26
# ruff: noqa: E402

import random
import sys
from pathlib import Path

workspace_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(workspace_root))

from VibraVid.core.manifest.stream import Stream
from VibraVid.core.velora._stream_vod import VodStreamMixin
from VibraVid.core.velora.util.formatting import estimate_eta, resolve_display_total


def _vbr_sizes(count: int = 400, real_total: int = 1_800_000_000) -> list[int]:
    rnd = random.Random(7)
    weights = [rnd.uniform(0.3, 2.0) for _ in range(count)]
    return [int(w / sum(weights) * real_total) for w in weights]


def _run(sizes: list[int], known: int, exact: bool) -> list[tuple[int, int, int]]:
    done_bytes, trace = 0, []
    for done, size in enumerate(sizes, 1):
        done_bytes += size
        trace.append((done, done_bytes, resolve_display_total(done_bytes, done, len(sizes), known_total=known, known_exact=exact)))
    return trace


def test_exact_total_never_moves():
    sizes = _vbr_sizes()
    real = sum(sizes)
    assert {total for _, _, total in _run(sizes, real, True)} == {real}


def test_nominal_total_converges_downward_to_real_size():
    sizes = _vbr_sizes()
    real = sum(sizes)
    nominal = real * 2  # peak bandwidth overshoots a VBR stream by ~2x
    trace = _run(sizes, nominal, False)

    assert trace[0][2] > real * 1.8  # starts near the nominal guess
    _, _, total_tenth = trace[len(sizes) // 10]
    _, _, total_quarter = trace[len(sizes) // 4]
    assert abs(total_tenth - real) / real < 0.20  # nominal fully replaced from 10%; ~40 random VBR samples are still noisy
    assert abs(total_quarter - real) / real < 0.10
    assert trace[-1][2] == real  # ends on the real size
    assert all(total >= done_bytes for _, done_bytes, total in trace)


def test_nominal_total_may_shrink_exact_total_may_not_regress():
    sizes = _vbr_sizes()
    nominal_totals = [t for _, _, t in _run(sizes, sum(sizes) * 2, False)]
    assert nominal_totals[-1] < nominal_totals[0]


def test_unknown_total_is_never_pinned():
    """HLS audio renditions declare no bitrate: the 1st-segment extrapolation (x3142) must not stick around."""
    count, seg = 3142, 90_000
    first = resolve_display_total(seg * 3, 1, count)  # the first segment happened to be 3x the average
    later = resolve_display_total(seg * 400, 400, count)

    assert first == seg * 3 * count
    assert later == seg * count  # the huge early guess is gone: the total followed the real data


def test_unknown_total_vbr_converges():
    sizes = _vbr_sizes()
    real = sum(sizes)
    trace = _run(sizes, 0, True)
    assert abs(trace[len(sizes) // 4][2] - real) / real < 0.10
    assert trace[-1][2] == real


def test_estimate_eta():
    assert estimate_eta(1_000, 100.0) == 10.0
    assert estimate_eta(0, 100.0) == 0.0
    assert estimate_eta(-5, 100.0) == 0.0
    assert estimate_eta(1_000, 0.0) is None
    assert estimate_eta(1_000, -1.0) is None


def test_eta_follows_speed_changes():
    remaining = 3_000_000_000
    assert estimate_eta(remaining, 1_700_000.0) < estimate_eta(remaining, 850_000.0)


def test_nominal_with_no_progress_returns_nominal():
    assert resolve_display_total(0, 0, 0, known_total=777, known_exact=False) == 777
    assert resolve_display_total(0, 0, 10, known_total=777, known_exact=False) == 777


def test_default_is_exact_so_content_length_callers_are_unchanged():
    assert resolve_display_total(10, 1, 10, known_total=1_000) == 1_000


class _Planner(VodStreamMixin):
    def _stop_check(self):
        return False


def _ranged(number: int, start: int, end: int, seg_type: str = "media") -> dict:
    return {"url": "u", "number": number, "seg_type": seg_type, "headers": {"Range": f"bytes={start}-{end}"}}


def test_ranged_total_sums_media_and_init_ranges():
    plan = [_ranged(0, 0, 99, "init"), _ranged(1, 100, 199), _ranged(2, 200, 399)]
    assert _Planner._ranged_total(plan) == 400


def test_ranged_total_ignores_init_without_range():
    plan = [{"url": "u", "number": 0, "seg_type": "init"}, _ranged(1, 0, 99)]
    assert _Planner._ranged_total(plan) == 100


def test_ranged_total_is_zero_if_any_media_segment_has_no_range():
    plan = [_ranged(1, 0, 99), {"url": "u", "number": 2, "seg_type": "media"}]
    assert _Planner._ranged_total(plan) == 0


def test_sync_follows_trimmed_plan_for_max_segments_or_max_time():
    stream = Stream(type="video", estimated_size=10_000, estimated_size_exact=True)
    trimmed_plan = [_ranged(0, 0, 99, "init"), _ranged(1, 100, 599)]  # only part of the file is planned

    _Planner()._sync_estimated_size(stream, trimmed_plan, {})

    assert stream.estimated_size == 600
    assert stream.estimated_size_exact is True


def test_sync_drops_exactness_when_plan_is_trimmed_and_not_ranged():
    from VibraVid.core.manifest.stream import Segment

    stream = Stream(type="video", estimated_size=10_000, estimated_size_exact=True)
    stream.segments = [Segment("u", i, size=1000) for i in range(10)]
    trimmed_plan = [{"url": "u", "number": i, "seg_type": "media"} for i in range(3)]

    _Planner()._sync_estimated_size(stream, trimmed_plan, {})

    assert stream.estimated_size_exact is False
    assert stream.estimated_size == 10_000  # blended down by progress, not forgotten


def test_sync_leaves_nominal_estimates_alone():
    stream = Stream(type="video", estimated_size=10_000, estimated_size_exact=False)
    _Planner()._sync_estimated_size(stream, [{"url": "u", "number": 1, "seg_type": "media"}], {})
    assert (stream.estimated_size, stream.estimated_size_exact) == (10_000, False)
