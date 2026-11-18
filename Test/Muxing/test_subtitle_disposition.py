# 15.09.26

from VibraVid.core.muxing.helper.sub.disposition import (
    SubtitleDispositionInfo,
    build_subtitle_disposition_args,
    disposition_lang_matches,
)


def _info(language="", forced=False, sdh=False, cc=False):
    return SubtitleDispositionInfo(language=language, forced=forced, sdh=sdh, cc=cc)


def test_plain_tracks_only_reset_to_zero():
    args = build_subtitle_disposition_args([_info("en-us"), _info("it-it")])
    assert args == ["-disposition:s:0", "0", "-disposition:s:1", "0"]


def test_language_suffix_fallback_sets_forced_and_hi():
    # same 3-track scenario observed in the streaming-mux fast-path bug (log 20260915_104421.log)
    args = build_subtitle_disposition_args(
        [_info("en-us_sdh"), _info("it-it"), _info("it-it_forced")]
    )
    assert args == [
        "-disposition:s:0", "0",
        "-disposition:s:1", "0",
        "-disposition:s:2", "0",
        "-disposition:s:0", "hearing_impaired",
        "-disposition:s:2", "forced",
    ]


def test_explicit_booleans_set_forced_and_hi_without_suffix():
    args = build_subtitle_disposition_args([_info("en-us", forced=True, sdh=True)])
    assert args == ["-disposition:s:0", "0", "-disposition:s:0", "forced+hearing_impaired"]


def test_cc_flag_maps_to_hearing_impaired():
    args = build_subtitle_disposition_args([_info("en-us", cc=True)])
    assert args == ["-disposition:s:0", "0", "-disposition:s:0", "hearing_impaired"]


def test_config_driven_default_language_overrides_first_match():
    args = build_subtitle_disposition_args(
        [_info("en-us_sdh"), _info("it-it"), _info("it-it_forced")],
        "ita_forced",
    )
    assert args[-2:] == ["-disposition:s:2", "default+forced"]


def test_config_driven_default_only_applies_to_first_match():
    args = build_subtitle_disposition_args(
        [_info("it-it"), _info("it-it")],
        "ita",
    )
    assert args.count("default") == 1


def test_no_config_lang_skips_passo_3():
    args = build_subtitle_disposition_args([_info("it-it")], "")
    assert "default" not in args


def test_empty_tracks_returns_no_args():
    assert build_subtitle_disposition_args([]) == []


def test_disposition_lang_matches_requires_both_values():
    assert disposition_lang_matches("", "ita") is False
    assert disposition_lang_matches("it-it", "") is False


def test_disposition_lang_matches_respects_flag_subset():
    assert disposition_lang_matches("it-it_forced", "ita_forced") is True
    assert disposition_lang_matches("it-it", "ita_forced") is False
