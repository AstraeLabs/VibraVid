# 28.10.26

"""``track_title_and_iso_lang``: the one function that names an audio or subtitle track in every mux path."""

from types import SimpleNamespace

import pytest

from VibraVid.core.utils.language import extract_lang_and_flags, track_title_and_iso_lang


@pytest.mark.parametrize(
    "track,expected",
    [
        # audio dicts as _build_status lists them: the suffix of the file is the name
        ({"path": "out.it-it.m4a", "name": "it-it", "language_iso3": "ita"}, ("Italian", "ita")),
        ({"name": "en-us"}, ("English", "eng")),
        ({"name": "es-419"}, ("Spanish", "spa")),
        # subtitles: [Forced], then [SDH] or [CC]
        ({"language": "it-it", "name": "it-it"}, ("Italian", "ita")),
        ({"language": "it-it_forced", "forced": True}, ("Italian [Forced]", "ita")),
        ({"language": "it-it_forced"}, ("Italian [Forced]", "ita")),
        ({"language": "en-us_cc", "cc": True}, ("English [CC]", "eng")),
        ({"language": "en-us_sdh", "sdh": True}, ("English [SDH]", "eng")),
        ({"language": "it-it_forced_sdh"}, ("Italian [Forced] [SDH]", "ita")),
        ({"language": "en-us.[CC]"}, ("English [CC]", "eng")),
        ({"language": "en-us_hi"}, ("English [CC]", "eng")),
        # "lang" wins over "language" and "name", as join_media has always read it
        ({"lang": "fr-fr_cc", "language": "it-it"}, ("French [CC]", "fra")),
        # nothing known
        ({}, ("und", "und")),
    ],
)
def test_dict_tracks(track, expected):
    assert track_title_and_iso_lang(track) == expected


def test_a_manifest_stream_gets_the_title_of_the_same_track_as_dict():
    """The streaming mux and the chunks pipe hold streams, join_media the dicts: both must write the same thing."""
    stream = SimpleNamespace(resolved_language="it-IT", language="ita", name="Italiano", forced=True, is_sdh=False, is_cc=False)
    as_dict = {"path": "out.it-it_forced.vtt", "language": "it-it_forced", "name": "it-it_forced", "forced": True}

    assert track_title_and_iso_lang(stream) == track_title_and_iso_lang(as_dict) == ("Italian [Forced]", "ita")


@pytest.mark.parametrize(
    "stream,expected",
    [
        (SimpleNamespace(resolved_language="en-US", language="eng", name="English [CC]", is_cc=True), ("English [CC]", "eng")),
        (SimpleNamespace(resolved_language="en-US", language="eng", is_sdh=True), ("English [SDH]", "eng")),
        (SimpleNamespace(resolved_language=None, language="ita", name=""), ("Italian", "ita")),
    ],
)
def test_stream_tracks(stream, expected):
    assert track_title_and_iso_lang(stream) == expected


def test_the_providers_own_name_is_never_the_title():
    """join_media only ever knows the language, so a name the manifest gave the stream must not leak into the title."""
    stream = SimpleNamespace(resolved_language="it-IT", language="ita", name="Italiano (Audio Description)")

    assert track_title_and_iso_lang(stream) == ("Italian", "ita")


@pytest.mark.parametrize("lang,title", [("hi", "Hindi"), ("hi-in", "Hindi"), ("hi-in_cc", "Hindi [CC]"), ("hi_forced", "Hindi [Forced]")])
def test_hindi_is_a_language_not_a_hearing_impaired_flag(lang, title):
    assert track_title_and_iso_lang({"language": lang}) == (title, "hin")


def test_hearing_impaired_flag_still_works_after_the_language():
    assert extract_lang_and_flags("en-us_hi") == ("en-us", {"hi"})
    assert extract_lang_and_flags("hi-in") == ("hi-in", set())
