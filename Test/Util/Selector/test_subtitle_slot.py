# 15.09.26
# ruff: noqa: E402

import sys
from pathlib import Path


workspace_root = Path(__file__).parent.parent.parent.parent
sys.path.insert(0, str(workspace_root))


from mock_streams import create_subtitle_streams_example1
from VibraVid.core.utils.selector import StreamSelector


def _selected_subtitle(streams):
    return [s for s in streams if s.type == "subtitle" and s.selected]


def test_subtitle_slot_first_priority_wins_even_if_lower_slot_also_present():
    # example1 has both ita (plain+forced) and eng (plain+cc) -- slot 1 (ita) must win.
    streams = create_subtitle_streams_example1()
    StreamSelector("false", "false", "1ita|2eng").apply(streams)
    sel = _selected_subtitle(streams)
    assert sel, "expected at least one selected subtitle stream"
    assert all(s.language == "ita" for s in sel)
    # Plain-language slot keeps every variant of that language together (plain + forced).
    assert {s.id for s in sel} == {"s0", "s1"}


def test_subtitle_slot_falls_through_to_next_slot_when_first_absent():
    # Only English subtitles present -- slot 1 (ita) has no match, slot 2 (eng) must win.
    streams = [s for s in create_subtitle_streams_example1() if s.language == "eng"]
    StreamSelector("false", "false", "1ita|2eng").apply(streams)
    sel = _selected_subtitle(streams)
    assert sel, "expected at least one selected subtitle stream"
    assert all(s.language == "eng" for s in sel)
    assert {s.id for s in sel} == {"s2", "s3"}


def test_subtitle_slot_no_match_skips_whole_download():
    # Only "fra" present -- neither slot 1 (ita) nor slot 2 (eng) matches anything.
    streams = [s for s in create_subtitle_streams_example1() if s.language == "fra"]
    selector = StreamSelector("false", "false", "1ita|2eng")
    selector.apply(streams)
    assert _selected_subtitle(streams) == []
    assert selector.no_match is True


def test_subtitle_slot_multi_lang_same_slot():
    # ita and fra both listed in slot 1 -- both present, both selected together.
    streams = create_subtitle_streams_example1()
    StreamSelector("false", "false", "1ita|1fra").apply(streams)
    sel = _selected_subtitle(streams)
    langs = {s.language for s in sel}
    assert langs == {"ita", "fra"}


def test_subtitle_slot_with_flag_suffix_is_strict():
    # "1it_forced" must select only the forced Italian track, not the plain one.
    streams = create_subtitle_streams_example1()
    StreamSelector("false", "false", "1it_forced|2en").apply(streams)
    sel = _selected_subtitle(streams)
    assert {s.id for s in sel} == {"s1"}
    assert sel[0].forced is True


def test_subtitle_no_slot_prefix_keeps_legacy_behavior():
    # No numeric prefix -- must behave exactly like before: every matched language
    # gets selected (with its variants), no exclusive priority between them.
    streams = create_subtitle_streams_example1()
    StreamSelector("false", "false", "ita|eng").apply(streams)
    sel = _selected_subtitle(streams)
    langs = {s.language for s in sel}
    assert langs == {"ita", "eng"}


def test_audio_slot_regression_unaffected_by_subtitle_slot_change():
    # Sanity check: the apply() restructure and shared split_audio_slots() reuse
    # must not have altered plain audio-slot behavior.
    from mock_streams import create_audio_streams_example1

    def _selected_audio(streams):
        return [s for s in streams if s.type == "audio" and s.selected]

    streams = create_audio_streams_example1()
    StreamSelector("false", "1ita|2eng", "false").apply(streams)
    sel = _selected_audio(streams)
    assert sel and all(s.language == "ita" for s in sel)
