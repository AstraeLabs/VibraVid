# 03.08.26
# ruff: noqa: E402

import sys
from pathlib import Path


workspace_root = Path(__file__).parent.parent.parent.parent
sys.path.insert(0, str(workspace_root))


from mock_streams import (
    create_audio_streams_example1,
    create_audio_streams_example2,
    create_audio_streams_with_regions,
    create_video_streams_with_dv,
    create_video_streams_with_dv_no_match,
)
from VibraVid.core.utils.selector import FilterSpec, StreamSelector


def _selected_video(streams):
    return next(s for s in streams if s.type == "video" and s.selected)


def _selected_audio(streams):
    return [s for s in streams if s.type == "audio" and s.selected]


def _companion(streams):
    return next((s for s in streams if s.type == "video" and getattr(s, "dv_companion", False)), None)


def test_dv_companion_matches_primary_resolution():
    streams = create_video_streams_with_dv()
    StreamSelector("1080|best&dv", "false", "false").apply(streams)

    primary = _selected_video(streams)
    assert primary.height == 1080
    assert primary.codecs != "dvh1"

    companion = _companion(streams)
    assert companion is not None
    assert companion.height == 1080, f"companion should match primary resolution (1080p), got {companion.height}p"
    assert companion.id == "dv2"  # worst (lowest bitrate) DV stream AT 1080p, not globally


def test_dv_companion_best_quality_at_matched_resolution():
    streams = create_video_streams_with_dv()
    StreamSelector("1080|best&dv=best", "false", "false").apply(streams)

    companion = _companion(streams)
    assert companion is not None
    assert companion.height == 1080
    assert companion.id == "dv3"


def test_dv_companion_explicit_height_override_ignores_primary_resolution():
    streams = create_video_streams_with_dv()
    StreamSelector("1080|best&dv=480", "false", "false").apply(streams)

    primary = _selected_video(streams)
    assert primary.height == 1080

    companion = _companion(streams)
    assert companion is not None
    assert companion.height == 480, "explicit numeric &dv override must bypass resolution matching"


def test_dv_companion_falls_back_to_nearest_when_no_exact_resolution_match():
    streams = create_video_streams_with_dv_no_match()
    StreamSelector("1080|best&dv", "false", "false").apply(streams)

    primary = _selected_video(streams)
    assert primary.height == 1080

    companion = _companion(streams)
    assert companion is not None
    assert companion.height == 720, "no DV at 1080p — should fall back to nearest available (720p), not silently worst-globally (480p)"


def test_short_key_video_native_syntax():
    spec = FilterSpec.parse("r=1080:c=hvc1:f=best", "video")
    assert spec.res == "1080"
    assert spec.codec == "hvc1"
    assert spec.explicit_fallback is True


def test_short_key_audio_native_syntax():
    spec = FilterSpec.parse("l=ita:c=aac:f=best", "audio")
    assert spec.langs == "ita"
    assert spec.codec == "aac"


def test_short_key_bitrate_and_id():
    spec = FilterSpec.parse("b=1000-8000:f=best", "video")
    assert (spec.bitrate_min, spec.bitrate_max) == (1000, 8000)

    spec_id = FilterSpec.parse("i=audio_128k_en:f=best", "audio")
    assert spec_id.id == "audio_128k_en"


def test_long_native_keys_are_also_recognized():
    spec = FilterSpec.parse("res=1080:codecs=hvc1:for=best", "video")
    assert spec.res == "1080"
    assert spec.codec == "hvc1"
    assert spec.extra == {}


def test_lang_alias_for_l_native_key():
    spec = FilterSpec.parse("lang='ita|eng|Ita|Eng|it|en'", "subtitle")
    assert spec.langs == "ita|eng|Ita|Eng|it|en"
    assert spec.extra == {}


def test_unrecognized_native_key_still_falls_back_to_extra():
    spec = FilterSpec.parse("bogus=1080", "video")
    assert spec.extra == {"bogus": "1080"}


def test_two_letter_code_matches_three_letter_tagged_stream():
    for video_filter in ("it", "l=it", "l=it:f=best"):
        streams = create_audio_streams_example1()
        StreamSelector("false", video_filter, "false").apply(streams)
        sel = _selected_audio(streams)
        assert len(sel) == 1, f"{video_filter!r} should select exactly one Italian track"
        assert sel[0].resolved_language == "it-IT", f"{video_filter!r} should match the ita/it-IT stream"


def test_pipe_list_still_resolves_short_code():
    streams = create_audio_streams_example1()
    StreamSelector("false", "ita|it", "false").apply(streams)
    sel = _selected_audio(streams)
    assert len(sel) == 1
    assert sel[0].resolved_language == "it-IT"


def test_region_specific_locale_disambiguates_same_base_language():
    streams = create_audio_streams_with_regions()
    StreamSelector("false", "en-au", "false").apply(streams)
    sel = _selected_audio(streams)
    assert len(sel) == 1
    assert sel[0].id == "a_au"

    streams = create_audio_streams_with_regions()
    StreamSelector("false", "l=en-au", "false").apply(streams)
    sel = _selected_audio(streams)
    assert len(sel) == 1
    assert sel[0].id == "a_au"


def test_generic_base_code_matches_any_region_variant():
    streams = create_audio_streams_with_regions()
    StreamSelector("false", "en", "false").apply(streams)
    sel = _selected_audio(streams)
    assert len(sel) == 1
    assert sel[0].id in ("a_us", "a_au")


def test_bare_resolution_unchanged():
    spec = FilterSpec.parse("1920", "video")
    assert spec.res == "1920"
    assert spec.codec is None


def test_pipe_language_list_unchanged():
    spec = FilterSpec.parse("ita|it", "audio")
    assert spec.langs == "ita|it"


def test_bare_comma_is_still_primary_codec_split_not_a_language_list():
    spec = FilterSpec.parse("it,en", "audio")
    assert spec.langs == "it"
    assert spec.codec == "en"

    multi = FilterSpec.parse("it|en", "audio")
    assert multi.langs == "it|en"
    assert multi.codec is None


def test_audio_slot_first_priority_wins_even_if_lower_slot_also_present():
    # example1 has both ita and eng available -- slot 1 (ita) must win, eng untouched.
    streams = create_audio_streams_example1()
    StreamSelector("false", "1ita|2eng", "false").apply(streams)
    sel = _selected_audio(streams)
    assert sel, "expected at least one selected audio stream"
    assert all(s.language == "ita" for s in sel)


def test_audio_slot_falls_through_to_next_slot_when_first_absent():
    # example2 only has eng -- slot 1 (ita) has no match, slot 2 (eng) must win.
    streams = create_audio_streams_example2()
    StreamSelector("false", "1ita|2eng", "false").apply(streams)
    sel = _selected_audio(streams)
    assert sel, "expected at least one selected audio stream"
    assert all(s.language == "eng" for s in sel)


def test_audio_slot_no_match_skips_whole_download():
    # Only "fra" present -- neither slot 1 (ita) nor slot 2 (eng) matches anything.
    streams = [s for s in create_audio_streams_example1() if s.language == "fra"]
    selector = StreamSelector("false", "1ita|2eng", "false")
    selector.apply(streams)
    sel = _selected_audio(streams)
    assert sel == []
    assert selector.no_match is True


def test_audio_slot_multi_select_within_same_slot():
    # ita and fra both listed in slot 1 -- both present in example1, both should be selected together.
    streams = create_audio_streams_example1()
    StreamSelector("false", "1ita|1fra", "false").apply(streams)
    sel = _selected_audio(streams)
    langs = {s.language for s in sel}
    assert langs == {"ita", "fra"}


def test_audio_no_slot_prefix_keeps_legacy_multi_select_behavior():
    # No numeric prefix at all -- must behave exactly like before: all listed
    # languages that are present get selected together, no exclusive priority.
    streams = create_audio_streams_example1()
    StreamSelector("false", "ita|eng", "false").apply(streams)
    sel = _selected_audio(streams)
    langs = {s.language for s in sel}
    assert langs == {"ita", "eng"}
