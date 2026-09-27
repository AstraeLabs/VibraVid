# 19.08.26
# ruff: noqa: E402

import sys
from pathlib import Path

workspace_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(workspace_root))

from VibraVid.core.manifest.stream import Segment, Stream
from VibraVid.core.velora._stream_vod import VodStreamMixin

_results = {"pass": 0, "fail": 0}


def check(name: str, got, expected) -> None:
    ok = got == expected
    _results["pass" if ok else "fail"] += 1
    status = "[PASS]" if ok else "[FAIL]"
    print(f"{status} {name}")
    if not ok:
        print(f"        expected: {expected!r}")
        print(f"        got:      {got!r}")


class FakeDownloader(VodStreamMixin):
    def __init__(self):
        self.max_time = None
        self.max_segments = None
        self.manifest_refresh_fn = None
        self.key = None
        self.multiperiod_calls: list = []
        self.generic_calls: list = []

    def _build_headers(self):
        return {}

    def _assign_segment_durations(self, stream, dl_segs, headers):
        pass

    def _download_dash_multiperiod(self, stream, bar_manager, live_decryption=False):
        self.multiperiod_calls.append(stream)

    def _download_stream_generic(
        self, dl_segs, stream, protocol, default_ext, bar_manager, live_decryption=False, seg_url_refresh_fn=None
    ):
        self.generic_calls.append(dl_segs)


def _two_period_stream(same_init: bool) -> Stream:
    init0 = Segment(url="https://cdn/init0.mp4", number=0, seg_type="init", period_idx=0)
    m0_1 = Segment(url="https://cdn/p0_seg1.mp4", number=1, seg_type="media", period_idx=0)
    m0_2 = Segment(url="https://cdn/p0_seg2.mp4", number=2, seg_type="media", period_idx=0)

    init1_url = "https://cdn/init0.mp4" if same_init else "https://cdn/init1.mp4"
    init1 = Segment(url=init1_url, number=0, seg_type="init", period_idx=1)
    m1_1 = Segment(url="https://cdn/p1_seg1.mp4", number=1, seg_type="media", period_idx=1)
    m1_2 = Segment(url="https://cdn/p1_seg2.mp4", number=2, seg_type="media", period_idx=1)

    return Stream(type="video", format="dash", segments=[init0, m0_1, m0_2, init1, m1_1, m1_2])


def test_shared_init_flattens_into_single_pipeline():
    dl = FakeDownloader()
    stream = _two_period_stream(same_init=True)

    dl._download_dash_stream(stream, bar_manager=None, live_decryption=True)

    check("multiperiod not invoked", dl.multiperiod_calls, [])
    check("single-period pipeline invoked once", len(dl.generic_calls), 1)

    dl_segs = dl.generic_calls[0]
    init_entries = [s for s in dl_segs if s["seg_type"] == "init"]
    media_entries = [s for s in dl_segs if s["seg_type"] == "media"]
    check("exactly one init segment survives the flatten", len(init_entries), 1)
    check("all four media segments survive", len(media_entries), 4)
    check("numbering stays contiguous from 0", [s["number"] for s in dl_segs], list(range(len(dl_segs))))


def test_different_init_falls_back_to_multiperiod():
    dl = FakeDownloader()
    stream = _two_period_stream(same_init=False)

    dl._download_dash_stream(stream, bar_manager=None, live_decryption=True)

    check("multiperiod invoked once", len(dl.multiperiod_calls), 1)
    check("single-period pipeline not invoked", dl.generic_calls, [])


def test_three_periods_sharing_init_still_flattens():
    segs = []
    for period_idx in range(3):
        segs.append(Segment(url="https://cdn/init0.mp4", number=0, seg_type="init", period_idx=period_idx))
        segs.append(
            Segment(url=f"https://cdn/p{period_idx}_seg1.mp4", number=1, seg_type="media", period_idx=period_idx)
        )
    stream = Stream(type="video", format="dash", segments=segs)

    dl = FakeDownloader()
    dl._download_dash_stream(stream, bar_manager=None, live_decryption=True)

    check("multiperiod not invoked (3 periods, shared init)", dl.multiperiod_calls, [])
    dl_segs = dl.generic_calls[0] if dl.generic_calls else []
    init_entries = [s for s in dl_segs if s["seg_type"] == "init"]
    check("exactly one init segment survives across 3 periods", len(init_entries), 1)
    check("all three media segments survive", len([s for s in dl_segs if s["seg_type"] == "media"]), 3)


def test_period_without_dedicated_init_falls_back_to_multiperiod():
    # Period 0 has a real init segment; Period 1 is self-initializing (every
    # media segment carries its own ftyp+moov) and has no seg_type=="init"
    # entry at all -- there's nothing to compare against, so this must NOT
    # be treated as "shares one init" and must stay on the safe per-Period path.
    init0 = Segment(url="https://cdn/init0.mp4", number=0, seg_type="init", period_idx=0)
    m0_1 = Segment(url="https://cdn/p0_seg1.mp4", number=1, seg_type="media", period_idx=0)
    m1_1 = Segment(url="https://cdn/p1_seg1.mp4", number=0, seg_type="media", period_idx=1)
    m1_2 = Segment(url="https://cdn/p1_seg2.mp4", number=1, seg_type="media", period_idx=1)
    stream = Stream(type="video", format="dash", segments=[init0, m0_1, m1_1, m1_2])

    dl = FakeDownloader()
    dl._download_dash_stream(stream, bar_manager=None, live_decryption=True)

    check("multiperiod invoked when a Period has no dedicated init", len(dl.multiperiod_calls), 1)
    check("single-period pipeline not invoked", dl.generic_calls, [])


def test_single_period_stream_is_unaffected():
    # Regression guard: a normal, non-multi-period stream must never trip the
    # new shares_one_init branch at all (media_periods has only one entry).
    init0 = Segment(url="https://cdn/init0.mp4", number=0, seg_type="init", period_idx=0)
    m0_1 = Segment(url="https://cdn/p0_seg1.mp4", number=1, seg_type="media", period_idx=0)
    m0_2 = Segment(url="https://cdn/p0_seg2.mp4", number=2, seg_type="media", period_idx=0)
    stream = Stream(type="video", format="dash", segments=[init0, m0_1, m0_2])

    dl = FakeDownloader()
    dl._download_dash_stream(stream, bar_manager=None, live_decryption=True)

    check("multiperiod never invoked for a single-period stream", dl.multiperiod_calls, [])
    check("single-period pipeline invoked once", len(dl.generic_calls), 1)
    check("no segments dropped", len(dl.generic_calls[0]), 3)


def test_ssai_periods_sharing_exact_same_media_segment_dedup():
    # Pre-existing behavior (not part of this session's change): SSAI-style
    # manifests can signal several ad-break Periods that all reference the
    # exact same SegmentBase resource -- these collapse to a single copy
    # instead of either flattening distinct media or going per-Period.
    init0 = Segment(url="https://cdn/init0.mp4", number=0, seg_type="init", period_idx=0)
    shared_media = Segment(url="https://cdn/shared.mp4", number=1, seg_type="media", period_idx=0)
    shared_media_dup = Segment(url="https://cdn/shared.mp4", number=1, seg_type="media", period_idx=1)
    stream = Stream(type="video", format="dash", segments=[init0, shared_media, shared_media_dup])

    dl = FakeDownloader()
    dl._download_dash_stream(stream, bar_manager=None, live_decryption=True)

    check("multiperiod not invoked for SSAI same-segment dedup", dl.multiperiod_calls, [])
    dl_segs = dl.generic_calls[0] if dl.generic_calls else []
    check("duplicate media segment collapsed to one", len([s for s in dl_segs if s["seg_type"] == "media"]), 1)


if __name__ == "__main__":
    test_shared_init_flattens_into_single_pipeline()
    test_different_init_falls_back_to_multiperiod()
    test_three_periods_sharing_init_still_flattens()
    test_period_without_dedicated_init_falls_back_to_multiperiod()
    test_single_period_stream_is_unaffected()
    test_ssai_periods_sharing_exact_same_media_segment_dedup()
    print(f"\n{_results['pass']} passed, {_results['fail']} failed")
    sys.exit(1 if _results["fail"] else 0)
