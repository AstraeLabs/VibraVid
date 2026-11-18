# 15.09.26
# ruff: noqa: E402

import sys
from pathlib import Path

workspace_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(workspace_root))

from VibraVid.core.velora.util._stream_helpers import build_retry_segments

_results = {"pass": 0, "fail": 0}


def check(name: str, got, expected) -> None:
    ok = got == expected
    _results["pass" if ok else "fail"] += 1
    status = "[PASS]" if ok else "[FAIL]"
    print(f"{status} {name}")
    if not ok:
        print(f"        expected: {expected!r}")
        print(f"        got:      {got!r}")


def _segs() -> dict:
    return {
        1: {"url": "https://cdn/seg1.ts?token=old", "number": 1, "seg_type": "media"},
        2: {"url": "https://cdn/seg2.ts?token=old", "number": 2, "seg_type": "media"},
    }


def test_no_fresh_urls_retries_original_urls():
    # No manifest_refresh_fn (e.g. StreamingCommunity): a transient 503 must still be retried with the original URL
    retry = build_retry_segments([1, 2], _segs(), {})

    check("every failed segment is retried", [s["number"] for s in retry], [1, 2])
    check("original URLs are kept", [s["url"] for s in retry], ["https://cdn/seg1.ts?token=old", "https://cdn/seg2.ts?token=old"])


def test_fresh_urls_replace_original_urls():
    fresh = {1: "https://cdn/seg1.ts?token=new", 2: "https://cdn/seg2.ts?token=new"}
    retry = build_retry_segments([1, 2], _segs(), fresh)

    check("fresh URLs are used", [s["url"] for s in retry], ["https://cdn/seg1.ts?token=new", "https://cdn/seg2.ts?token=new"])


def test_partial_fresh_map_mixes_fresh_and_original():
    retry = build_retry_segments([1, 2], _segs(), {2: "https://cdn/seg2.ts?token=new"})

    check("missing fresh URL falls back to original", [s["url"] for s in retry], ["https://cdn/seg1.ts?token=old", "https://cdn/seg2.ts?token=new"])


def test_unknown_segment_number_is_ignored():
    retry = build_retry_segments([1, 99], _segs(), {})

    check("unknown segment number skipped", [s["number"] for s in retry], [1])


def test_original_segment_dict_is_not_mutated():
    segs = _segs()
    build_retry_segments([1], segs, {1: "https://cdn/seg1.ts?token=new"})

    check("source segment keeps its URL", segs[1]["url"], "https://cdn/seg1.ts?token=old")


if __name__ == "__main__":
    test_no_fresh_urls_retries_original_urls()
    test_fresh_urls_replace_original_urls()
    test_partial_fresh_map_mixes_fresh_and_original()
    test_unknown_segment_number_is_ignored()
    test_original_segment_dict_is_not_mutated()
    print(f"\n{_results['pass']} passed, {_results['fail']} failed")
    sys.exit(1 if _results["fail"] else 0)
