# 28.10.26

"""``order_tracks``: the one function that orders audio and subtitle tracks in every mux path, from the select_audio / select_subtitle of the download."""

from types import SimpleNamespace

import pytest

from VibraVid.core.ui.tracker import context_tracker
from VibraVid.core.utils import track_order
from VibraVid.core.utils.selector import filter_language_order
from VibraVid.core.utils.track_order import effective_select_filter, order_tracks


@pytest.fixture
def configured(monkeypatch):
    """Fake DOWNLOAD.select_*, never the user's config; returns the dict to fill."""
    values: dict[str, object] = {}
    config = SimpleNamespace(get=lambda section, key, *args, default="": values.get(f"{section}.{key}", default))
    monkeypatch.setattr(track_order, "config_manager", SimpleNamespace(config=config))
    return values


def _langs(tracks):
    return [t["language"] for t in tracks]


@pytest.mark.parametrize(
    "raw,kind,expected",
    [
        ("ita|eng", "audio", ["ita", "eng"]),
        ("eng|ita", "audio", ["eng", "ita"]),
        ("ita eng", "subtitle", ["ita", "eng"]),
        ("ita|it", "audio", ["ita", "it"]),
        # exclusive-priority slots rank by their number, not by how they were written
        ("1ita|2eng", "subtitle", ["ita", "eng"]),
        ("2eng|1ita", "subtitle", ["ita", "eng"]),
        ("1ita|2eng", "audio", ["ita", "eng"]),
        ("1ita_forced|2ita", "subtitle", ["ita_forced", "ita"]),
        # a slot with several languages keeps their order
        ("1ita|eng|2fra", "audio", ["ita", "eng", "fra"]),
        # native key=value form and fallbacks
        ("l=ita|eng", "audio", ["ita", "eng"]),
        ("ita|best", "audio", ["ita"]),
        ("ita|eng,AAC", "audio", ["ita", "eng"]),
        ("ita_forced|eng", "subtitle", ["ita_forced", "eng"]),
        # filters that name no language
        ("best", "audio", []),
        ("all", "subtitle", []),
        ("false", "subtitle", []),
        ("", "audio", []),
        (None, "audio", []),
    ],
)
def test_filter_language_order(raw, kind, expected):
    assert filter_language_order(raw, kind) == expected


def test_a_filter_orders_audio_the_way_it_was_written(configured):
    # the files come in the alphabetical order of their names
    tracks = [{"language": "en-us"}, {"language": "it-it"}, {"language": "fr-fr"}]

    assert _langs(order_tracks(tracks, "audio", "ita|eng")) == ["it-it", "en-us", "fr-fr"]
    assert _langs(order_tracks(tracks, "audio", "fra|ita|eng")) == ["fr-fr", "it-it", "en-us"]


def test_slots_order_like_the_plain_filter(configured):
    tracks = [{"language": "en-us"}, {"language": "it-it"}]

    assert _langs(order_tracks(tracks, "subtitle", "1ita|2eng")) == _langs(order_tracks(tracks, "subtitle", "ita|eng")) == ["it-it", "en-us"]
    assert _langs(order_tracks(tracks, "subtitle", "2ita|1eng")) == ["en-us", "it-it"]


def test_no_language_in_the_filter_leaves_the_tracks_alone(configured):
    tracks = [{"language": "it-it"}, {"language": "en-us"}]

    assert order_tracks(tracks, "audio") is tracks
    assert order_tracks(tracks, "audio", "best") is tracks
    assert order_tracks([], "audio", "ita|eng") == []


def test_tracks_matching_nothing_go_last_in_their_own_order(configured):
    tracks = [{"language": "de-de"}, {"language": "en-us"}, {"language": "es-es"}, {"language": "it-it"}]

    assert _langs(order_tracks(tracks, "audio", "ita|eng")) == ["it-it", "en-us", "de-de", "es-es"]


def test_subtitle_flags_rank_only_the_tracks_that_carry_them(configured):
    tracks = [{"language": "en-us"}, {"language": "it-it"}, {"language": "it-it_forced"}, {"language": "en-us_sdh"}]

    assert _langs(order_tracks(tracks, "subtitle", "ita_forced|ita|eng")) == ["it-it_forced", "it-it", "en-us", "en-us_sdh"]
    # a plain language ranks all its variants together, in their own order
    assert _langs(order_tracks(tracks, "subtitle", "eng|ita")) == ["en-us", "en-us_sdh", "it-it", "it-it_forced"]
    assert _langs(order_tracks(tracks, "subtitle", "eng_sdh|ita")) == ["en-us_sdh", "it-it", "it-it_forced", "en-us"]


def test_a_region_token_asks_for_that_locale_a_bare_one_for_any(configured):
    tracks = [{"language": "es-es"}, {"language": "es-419"}, {"language": "en-us"}]

    assert _langs(order_tracks(tracks, "audio", "es-419|eng")) == ["es-419", "en-us", "es-es"]
    assert _langs(order_tracks(tracks, "audio", "eng|es")) == ["en-us", "es-es", "es-419"]
    assert _langs(order_tracks(tracks, "audio", "eng|spa")) == ["en-us", "es-es", "es-419"]


def test_hindi_is_matched_as_a_language(configured):
    tracks = [{"language": "en-us"}, {"language": "hi-in"}]

    assert _langs(order_tracks(tracks, "audio", "hin|eng")) == ["hi-in", "en-us"]
    assert _langs(order_tracks(tracks, "audio", "hi")) == ["hi-in", "en-us"]


def test_dicts_of_build_status_and_manifest_streams_rank_the_same(configured):
    status = [{"path": "out.en-us.m4a", "name": "en-us"}, {"path": "out.it-it.m4a", "name": "it-it"}]
    streams = [SimpleNamespace(resolved_language="en-US", language="eng"), SimpleNamespace(resolved_language="it-IT", language="ita")]

    assert [t["name"] for t in order_tracks(status, "audio", "ita|eng")] == ["it-it", "en-us"]
    assert [s.language for s in order_tracks(streams, "audio", "ita|eng")] == ["ita", "eng"]


def test_items_can_be_pairs_of_a_track_and_its_path(configured):
    pairs = [({"language": "en-us"}, "a.m4a"), ({"language": "it-it"}, "b.m4a")]

    assert [path for _, path in order_tracks(pairs, "audio", "ita", track_of=lambda pair: pair[0])] == ["b.m4a", "a.m4a"]


def test_the_filter_in_force_is_the_download_s_own_before_the_configured_one(configured):
    configured["DOWNLOAD.select_audio"] = "ita|eng"
    configured["DOWNLOAD.select_subtitle"] = "1ita|2eng"

    assert effective_select_filter("audio") == "ita|eng"
    assert effective_select_filter("subtitle", {}) == "1ita|2eng"
    assert effective_select_filter("audio", {"audio": "eng"}) == "eng"
    assert effective_select_filter("subtitle", {"audio": "eng"}) == "1ita|2eng"


def test_the_gui_audio_choice_beats_the_download_s_filter_for_audio_only(configured):
    configured["DOWNLOAD.select_audio"] = "ita|eng"
    previous = context_tracker.audio_filter
    context_tracker.audio_filter = "eng"
    try:
        assert effective_select_filter("audio", {"audio": "fra"}) == "eng"
        assert effective_select_filter("subtitle", {"subtitle": "fra"}) == "fra"
    finally:
        context_tracker.audio_filter = previous
