# 28.09.26
# ruff: noqa: E402

import sys
from pathlib import Path

workspace_root = Path(__file__).parent.parent.parent.parent
sys.path.insert(0, str(workspace_root))

from mock_streams import MockStream

from VibraVid.core.utils.selector import StreamSelector

# These two filters used to live as regex-on-raw-XML functions in
# `services/hbomax/manifest_filter.py` (see git history / the superseded
# `test_hbomax_manifest_filter.py`). They now run on parsed `Stream` objects
# inside `StreamSelector`, via `drop_clear_av`/`dv_top_tier_tolerance`, so any
# service can opt in through `custom_filters` instead of duplicating a
# service-specific text-manipulation module.


def _video(id_, height, codecs="hvc1.2.4.L150.90", bitrate=5_000_000, encrypted=True):
    return MockStream(type="video", id=id_, height=height, codecs=codecs, bitrate=bitrate, encrypted=encrypted)


def _audio(id_, encrypted=True, language="ita"):
    return MockStream(type="audio", id=id_, language=language, resolved_language="it-IT", codecs="ec-3", bitrate=256_000, encrypted=encrypted)


def _subtitle(id_, language="ita"):
    return MockStream(type="subtitle", id=id_, language=language, resolved_language="it-IT")


# ── drop_clear_av ────────────────────────────────────────────────────────────


def test_clear_video_and_audio_are_dropped():
    streams = [
        _video("v1", 1080, encrypted=True),
        _video("v2", 1080, encrypted=False),
        _audio("a1", encrypted=True),
        _audio("a2", encrypted=False),
    ]
    kept = StreamSelector._strip_unencrypted_av(streams)
    assert {s.id for s in kept} == {"v1", "a1"}


def test_subtitles_are_never_removed():
    """Regression: filtering on DRM alone would strip every subtitle track (never encrypted)."""
    streams = [_video("v1", 1080, encrypted=True), _video("v2", 1080, encrypted=False), _subtitle("s1"), _subtitle("s2")]
    kept = StreamSelector._strip_unencrypted_av(streams)
    assert {s.id for s in kept} == {"v1", "s1", "s2"}


def test_manifest_without_any_encryption_is_returned_untouched():
    """A clear-only manifest must stay playable instead of becoming a failed selection."""
    streams = [_video("v1", 720, encrypted=False), _audio("a1", encrypted=False)]
    kept = StreamSelector._strip_unencrypted_av(streams)
    assert kept == streams


def test_already_all_encrypted_is_unchanged():
    streams = [_video("v1", 1080, encrypted=True), _audio("a1", encrypted=True)]
    kept = StreamSelector._strip_unencrypted_av(streams)
    assert kept == streams


def test_drop_clear_av_is_opt_in_via_constructor_flag():
    """The flag must actually reach apply(); off by default so it never affects other services."""
    streams = [_video("v1", 1080, encrypted=True), _video("v2", 1080, encrypted=False), _audio("a1", encrypted=True)]
    StreamSelector("best", "ita", "false", drop_clear_av=True).apply(streams)
    assert not any(s.id == "v2" and s.selected for s in streams)

    streams2 = [_video("v1", 1080, encrypted=True), _video("v2", 1080, encrypted=False), _audio("a1", encrypted=True)]
    StreamSelector("best", "ita", "false", drop_clear_av=False).apply(streams2)
    # Without the flag, the clear variant is still in the pool (may or may not
    # be selected depending on `best`, but it must not have been removed).
    assert any(s.id == "v2" for s in streams2)


# ── dv_top_tier_tolerance ────────────────────────────────────────────────────


def _supergirl_ladder():
    """Real shape this was written for: a BT.709 rung advertised as HDR10 sits above the real ladder."""
    return [
        _video("v22", 1600, codecs="dvh1.05.06"),
        _video("v24", 1600, codecs="dvh1.05.06"),
        _video("v16", 1600, codecs="hvc1.2.4.L150.90"),
        _video("v20", 1600, codecs="hvc1.2.4.L150.90"),
        _video("v8", 1608, codecs="hvc1.2.4.L150.90"),
        _video("v12", 1608, codecs="hvc1.2.4.L150.90"),
    ]


def test_top_tier_above_the_dolby_vision_height_is_removed():
    kept = StreamSelector._drop_rogue_top_tier(_supergirl_ladder(), tolerance=0.02)
    assert {s.id for s in kept} == {"v22", "v24", "v16", "v20"}


def test_dolby_vision_capped_below_a_real_4k_tier_is_left_alone():
    """A large gap means Dolby Vision is capped, not that the top rung is fake."""
    streams = [
        _video("dv", 1080, codecs="dvh1.05.06"),
        _video("u4", 2160, codecs="hvc1.2.4.L150.90"),
        _video("u2", 1080, codecs="hvc1.2.4.L150.90"),
    ]
    kept = StreamSelector._drop_rogue_top_tier(streams, tolerance=0.02)
    assert {s.id for s in kept} == {"dv", "u4", "u2"}


def test_dolby_vision_track_is_never_removed():
    """The companion carries the RPU: dropping it would silently disable the conversion."""
    streams = [
        _video("v22", 1600, codecs="dvh1.05.06"),
        _video("v24", 1600, codecs="dvhe.05.06"),
        _video("v12", 1608, codecs="hvc1.2.4.L150.90"),
    ]
    kept = StreamSelector._drop_rogue_top_tier(streams, tolerance=0.02)
    assert {s.id for s in kept} == {"v22", "v24"}


def test_manifest_without_dolby_vision_is_untouched():
    streams = [_video("v1", 2160, codecs="hvc1.2.4.L150.90"), _video("v2", 1080, codecs="hvc1.2.4.L150.90")]
    kept = StreamSelector._drop_rogue_top_tier(streams, tolerance=0.02)
    assert kept == streams


def test_top_tier_matching_the_dolby_vision_height_is_untouched():
    streams = [_video("v24", 2160, codecs="dvh1.05.06"), _video("v20", 2160, codecs="hvc1.2.4.L150.90")]
    kept = StreamSelector._drop_rogue_top_tier(streams, tolerance=0.02)
    assert kept == streams


def test_only_the_top_tier_is_touched_and_fallbacks_survive():
    streams = [
        _video("v24", 1600, codecs="dvh1.05.06"),
        _video("v20", 1600, codecs="hvc1.2.4.L150.90"),
        _video("v12", 1608, codecs="hvc1.2.4.L150.90"),
        _video("v30", 1080, codecs="hvc1.2.4.L150.90"),
        _video("v40", 720, codecs="hvc1.2.4.L150.90"),
    ]
    kept = StreamSelector._drop_rogue_top_tier(streams, tolerance=0.02)
    assert {s.id for s in kept} == {"v24", "v20", "v30", "v40"}


def test_tolerance_disabled_by_default():
    """dv_top_tier_tolerance=None (the default) must never touch the top tier."""
    streams = _supergirl_ladder()
    StreamSelector("best", "false", "false").apply(streams)
    assert any(s.id in ("v8", "v12") for s in streams)  # still in the pool, untouched


def test_dv_top_tier_tolerance_reaches_select_video_through_apply():
    streams = _supergirl_ladder() + [_audio("a1")]
    StreamSelector("best", "ita", "false", dv_top_tier_tolerance=0.02).apply(streams)
    selected_video = next(s for s in streams if s.type == "video" and s.selected)
    assert selected_video.id not in ("v8", "v12")
