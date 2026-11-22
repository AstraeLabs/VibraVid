# 21.11.26
# ruff: noqa: E402

import sys
from pathlib import Path

workspace_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(workspace_root))

from VibraVid.core.utils.subtitle_flags import SubFlags, canonical_name, classify, flags_satisfy, split_lang_flags


def test_split_lang_flags_handles_every_separator_and_brackets():
    assert split_lang_flags("en-us_cc") == ("en-us", {"cc"})
    assert split_lang_flags("en-us.[CC]") == ("en-us", {"cc"})
    assert split_lang_flags("ita-forced-sdh") == ("ita", {"forced", "sdh"})
    assert split_lang_flags("en-hi") == ("en", {"hi"})


def test_hi_as_first_token_is_hindi_not_a_flag():
    assert split_lang_flags("hi") == ("hi", set())
    assert split_lang_flags("hi-IN") == ("hi-IN", set())
    assert split_lang_flags("hi_forced") == ("hi", {"forced"})


def test_classify_keeps_each_signal_distinct():
    assert classify(name="English SDH").names() == ("sdh",)
    assert classify(name="English CC").names() == ("cc",)
    assert classify(name="English (HI)").names() == ("hi",)
    assert classify(name="English [Forced]").names() == ("forced",)
    assert classify(name="English Closed Captions").names() == ("cc",)
    assert classify(name="English - Hard of Hearing").names() == ("hi",)
    assert classify(name="Italiano").names() == ()


def test_classify_dash_roles_and_accessibility():
    assert classify(roles=["forced-subtitle"]).names() == ("forced",)
    assert classify(roles=["caption"]).names() == ("cc",)
    assert classify(accessibility=[("urn:mpeg:dash:role:2011", "caption")]).names() == ("cc",)
    assert classify(accessibility=[("urn:tva:metadata:cs:AudioPurposeCS:2007", "2")]).names() == ("hi",)
    # 1 = visually impaired: says nothing about hearing
    assert classify(accessibility=[("urn:tva:metadata:cs:AudioPurposeCS:2007", "1")]).names() == ()


def test_classify_hls_characteristics():
    both = "public.accessibility.transcribes-spoken-dialog,public.accessibility.describes-music-and-sound"
    assert classify(characteristics=both).names() == ("sdh",)
    assert classify(characteristics="public.accessibility.describes-music-and-sound").names() == ("hi",)
    assert classify(characteristics="public.accessibility.transcribes-spoken-dialog").names() == ()


def test_classify_ism_subtype():
    assert classify(subtype="FORC").names() == ("forced",)
    assert classify(subtype="CAPT").names() == ("cc",)
    assert classify(subtype="SUBT").names() == ()


def test_generic_hearing_hint_does_not_duplicate_an_explicit_name():
    chars = "public.accessibility.describes-music-and-sound"
    assert classify(name="English SDH", characteristics=chars).names() == ("sdh",)
    assert classify(name="English HI", characteristics=chars).names() == ("hi",)


def test_signals_combine():
    flags = classify(lang="ita-forced", name="Italiano SDH")
    assert flags.names() == ("forced", "sdh")


def test_canonical_name_order_and_idempotence():
    assert canonical_name("ita") == "ita"
    assert canonical_name("ita", ["sdh", "forced"]) == "ita_forced_sdh"
    assert canonical_name("eng", SubFlags(cc=True)) == "eng_cc"
    assert canonical_name("ita-forced", SubFlags(forced=True)) == "ita_forced"
    assert canonical_name("", None) == "und"


def test_flags_satisfy_equivalence_rules():
    assert flags_satisfy({"sdh"}, {"hi"}) and flags_satisfy({"hi"}, {"sdh"})
    assert not flags_satisfy({"cc"}, {"sdh"}) and not flags_satisfy({"sdh"}, {"cc"})
    assert not flags_satisfy({"forced"}, {"sdh"})
    assert flags_satisfy({"forced", "sdh"}, {"forced"})
    assert flags_satisfy(set(), set())


def test_from_track_accepts_both_spellings_of_the_keys():
    assert SubFlags.from_track({"forced": True, "cc": True}).names() == ("forced", "cc")
    assert SubFlags.from_track({"is_sdh": True, "is_hi": True}).names() == ("sdh", "hi")
    assert SubFlags.from_track({}).hearing_impaired is False
    assert SubFlags.from_track({"hi": True}).hearing_impaired is True
