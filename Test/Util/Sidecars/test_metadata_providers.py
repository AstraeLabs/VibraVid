# 01.10.26
# ruff: noqa: E402

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

workspace_root = Path(__file__).parent.parent.parent.parent
sys.path.insert(0, str(workspace_root))

import pytest

from VibraVid.core.ui.tracker import context_tracker
from VibraVid.provider.tmdb import tmdb_client
from VibraVid.services._base import metadata, tmdb_artwork
from VibraVid.services._base.metadata import BaseMetadataProvider
from VibraVid.services._base.metadata.imdb import ImdbProvider
from VibraVid.services._base.metadata.tmdb import TmdbProvider
from VibraVid.services._base.metadata.tvdb import TvdbProvider

IMDB_MOVIE = {
    "id": "tt1302006",
    "titleText": {"text": "The Irishman"},
    "originalTitleText": {"text": "The Irishman"},
    "releaseYear": {"year": 2019},
    "releaseDate": {"year": 2019, "month": 11, "day": 27},
    "plot": {"plotText": {"plainText": "Frank Sheeran."}},
    "primaryImage": {"url": "https://img/irishman.jpg"},
    "runtime": {"seconds": 12540},
    "genres": {"genres": [{"text": "Crime"}, {"text": "Drama"}]},
    "ratingsSummary": {"aggregateRating": 7.8},
}


def _title(**fields):
    return SimpleNamespace(**{"name": "The Irishman", "slug": "the-irishman", "year": 2019, "tmdb_id": None, **fields})


# ── registry / ordering ───────────────────────────────────────────────────

def test_provider_names_parse_separators_and_order():
    assert metadata.provider_names("tmdb,imdb") == ["tmdb", "imdb"]
    assert metadata.provider_names("tvdb | tmdb;imdb") == ["tvdb", "tmdb", "imdb"]
    assert metadata.provider_names("IMDB") == ["imdb"]


def test_provider_names_drop_duplicates_and_unknown_names():
    assert metadata.provider_names("imdb,imdb,foo,tvdb") == ["imdb", "tvdb"]


@pytest.mark.parametrize("value", ["", "   ", "nonsense", None])
def test_provider_names_fall_back_to_the_default(value):
    with patch.object(metadata.config_manager.config, "get", return_value=value):
        assert metadata.provider_names() == ["tmdb", "imdb"]


def test_get_provider_returns_the_right_class_or_none():
    assert isinstance(metadata.get_provider("imdb"), ImdbProvider)
    assert isinstance(metadata.get_provider("tvdb"), TvdbProvider)
    assert isinstance(metadata.get_provider("tmdb"), TmdbProvider)
    assert metadata.get_provider("nope") is None


def test_every_provider_extends_the_base_and_names_itself():
    for name, cls in metadata.PROVIDERS.items():
        assert issubclass(cls, BaseMetadataProvider) and cls.NAME == name


def test_base_provider_defaults_are_safe_noops():
    base = BaseMetadataProvider()
    assert base.available() is True
    assert base.find("movie", "x", None, 2000, None) is None
    assert base.movie("1") is None and base.episode("1", 1, 1) is None


# ── shared matching rule ──────────────────────────────────────────────────

def _node(title, year, original=None):
    return {"t": title, "o": original, "y": year}


def _best(results, name="The Irishman", year=2019):
    return BaseMetadataProvider.best_match(results, name, year, lambda n: [n["t"], n["o"]], lambda n: n["y"])


def test_best_match_accepts_exact_and_year_off_by_one():
    assert _best([_node("The Irishman", 2019)]) is not None
    assert _best([_node("The Irishman", 2020)]) is not None
    assert _best([_node("The Irishman", 2018)]) is not None


def test_best_match_rejects_year_two_apart_or_missing():
    assert _best([_node("The Irishman", 2021)]) is None
    assert _best([_node("The Irishman", None)]) is None
    assert _best([_node("The Irishman", 2019)], year=None) is None


def test_best_match_rejects_a_longer_different_title():
    """'Get the Irishman' (2021) scores 0.857 against 'The Irishman': above the search's 0.85, below the 0.92 a .nfo needs."""
    assert _best([_node("Get the Irishman", 2021)], year=2022) is None


def test_best_match_uses_the_original_title_and_prefers_the_closest():
    hit = _best([_node("Il Irlandese", 2019, "The Irishman")])
    assert hit is not None
    closest = _best([_node("The Irishmans", 2019), _node("The Irishman", 2019)])
    assert closest["t"] == "The Irishman"


def test_best_match_tolerates_punctuation_and_case():
    assert _best([_node("SPIDER-MAN: No Way Home", 2021)], name="Spider-Man No Way Home", year=2021) is not None


def test_best_match_accepts_year_ranges_for_series():
    assert BaseMetadataProvider.best_match([_node("Game of Thrones", 2011)], "Game of Thrones", "2011-2019", lambda n: [n["t"]], lambda n: n["y"])


# ── IMDb provider ─────────────────────────────────────────────────────────

def test_imdb_find_picks_the_reliable_search_result(monkeypatch):
    seen = {}

    def fake_search(name, media_type, year=None):
        seen.update(name=name, media_type=media_type, year=year)
        return [{**IMDB_MOVIE, "id": "tt0000001", "titleText": {"text": "Get the Irishman"}, "originalTitleText": {"text": "Get the Irishman"}, "releaseYear": {"year": 2021}}, IMDB_MOVIE]

    monkeypatch.setattr("VibraVid.services._base.metadata.imdb.imdb_client.search_titles", fake_search)

    assert ImdbProvider().find("movie", "The Irishman", None, 2020, None) == "tt1302006"
    assert seen == {"name": "The Irishman", "media_type": "movie", "year": 2020}


def test_imdb_find_without_a_year_asks_nobody(monkeypatch):
    monkeypatch.setattr("VibraVid.services._base.metadata.imdb.imdb_client.search_titles", lambda *a, **k: pytest.fail("no search expected"))
    assert ImdbProvider().find("movie", "The Irishman", None, None, None) is None


def test_imdb_find_maps_a_site_supplied_tmdb_id_without_searching(monkeypatch):
    monkeypatch.setattr(tmdb_client, "_api_key", "k")
    monkeypatch.setattr(tmdb_client, "get_imdb_id", lambda tid, mt, *a, **k: "tt1302006" if (tid, mt) == (398978, "movie") else None)
    monkeypatch.setattr("VibraVid.services._base.metadata.imdb.imdb_client.search_titles", lambda *a, **k: pytest.fail("no search expected"))
    assert ImdbProvider().find("movie", "ignored", None, None, 398978) == "tt1302006"


def test_imdb_site_tmdb_id_needs_a_tmdb_key_else_falls_back_to_search(monkeypatch):
    monkeypatch.setattr(tmdb_client, "_api_key", "")
    with patch("VibraVid.provider.tmdb._configured_api_key", return_value=""):
        monkeypatch.setattr("VibraVid.services._base.metadata.imdb.imdb_client.search_titles", lambda *a, **k: [IMDB_MOVIE])
        assert ImdbProvider().find("movie", "The Irishman", None, 2019, 398978) == "tt1302006"


def test_imdb_movie_mapping(monkeypatch):
    monkeypatch.setattr("VibraVid.services._base.metadata.imdb.imdb_client.get_title", lambda i: IMDB_MOVIE)
    info = ImdbProvider().movie("tt1302006")
    assert (info.title, info.year, info.premiered, info.runtime, info.rating) == ("The Irishman", 2019, "2019-11-27", 209, 7.8)
    assert info.genres == ["Crime", "Drama"] and info.ids == {"imdb": "tt1302006"} and info.image_url == "https://img/irishman.jpg"


def test_imdb_movie_unknown_id_is_none(monkeypatch):
    monkeypatch.setattr("VibraVid.services._base.metadata.imdb.imdb_client.get_title", lambda i: None)
    assert ImdbProvider().movie("tt0") is None


def test_imdb_episode_mapping(monkeypatch):
    node = {
        "id": "tt0959621",
        "titleText": {"text": "Pilot"},
        "plot": {"plotText": {"plainText": "A teacher."}},
        "primaryImage": {"url": "https://img/pilot.jpg"},
        "releaseDate": {"year": 2008, "month": 1, "day": 20},
        "runtime": {"seconds": 3480},
    }
    monkeypatch.setattr("VibraVid.services._base.metadata.imdb.imdb_client.get_episode", lambda s, se, ep: (node, "Breaking Bad"))
    info = ImdbProvider().episode("tt0903747", 1, 1)
    assert (info.title, info.show_title, info.season, info.episode, info.aired, info.runtime) == ("Pilot", "Breaking Bad", 1, 1, "2008-01-20", 58)
    assert info.ids == {"imdb": "tt0959621"}


def test_imdb_missing_episode_is_none(monkeypatch):
    monkeypatch.setattr("VibraVid.services._base.metadata.imdb.imdb_client.get_episode", lambda *a: (None, None))
    assert ImdbProvider().episode("tt0903747", 1, 99) is None


def test_imdb_is_always_available():
    assert ImdbProvider().available() is True


# ── TVDB provider ─────────────────────────────────────────────────────────

TVDB_HIT = {"tvdb_id": "14156", "name": "The Irishman", "year": "2019", "aliases": [], "remote_ids": []}


def test_tvdb_availability_follows_the_key(monkeypatch):
    monkeypatch.setattr("VibraVid.services._base.metadata.tvdb.tvdb_client._api_key", "")
    with patch("VibraVid.provider.tvdb._configured_api_key", return_value=None):
        assert TvdbProvider().available() is False
    monkeypatch.setattr("VibraVid.services._base.metadata.tvdb.tvdb_client._api_key", "key")
    assert TvdbProvider().available() is True


def test_tvdb_find_by_search_with_year_tolerance(monkeypatch):
    monkeypatch.setattr("VibraVid.services._base.metadata.tvdb.tvdb_client.search", lambda name, mt: [{**TVDB_HIT, "tvdb_id": "9", "name": "Get the Irishman", "year": "2021"}, TVDB_HIT])
    assert TvdbProvider().find("movie", "The Irishman", None, 2020, None) == "14156"
    assert TvdbProvider().find("movie", "The Irishman", None, 2022, None) is None


def test_tvdb_find_ignores_hits_without_an_id_and_uses_aliases(monkeypatch):
    hits = [{"name": "The Irishman", "year": "2019"}, {"tvdb_id": "5", "name": "Il Irlandese", "year": "2019", "aliases": ["The Irishman"]}]
    monkeypatch.setattr("VibraVid.services._base.metadata.tvdb.tvdb_client.search", lambda name, mt: hits)
    assert TvdbProvider().find("movie", "The Irishman", None, 2019, None) == "5"


def test_tvdb_find_via_imdb_bridge_for_a_site_tmdb_id(monkeypatch):
    monkeypatch.setattr(tmdb_client, "_api_key", "k")
    monkeypatch.setattr(tmdb_client, "get_imdb_id", lambda *a, **k: "tt0903747")
    monkeypatch.setattr("VibraVid.services._base.metadata.tvdb.tvdb_client.find_by_remote_id", lambda imdb, mt: 81189 if (imdb, mt) == ("tt0903747", "tv") else None)
    monkeypatch.setattr("VibraVid.services._base.metadata.tvdb.tvdb_client.search", lambda *a: pytest.fail("no search expected"))
    assert TvdbProvider().find("tv", "x", None, None, 1396) == "81189"


def test_tvdb_movie_mapping_prefers_the_translation_and_has_no_fake_rating(monkeypatch):
    detail = {
        "name": "The Irishman", "year": "2019", "runtime": 209, "score": 337573, "image": "/banners/p.jpg",
        "genres": [{"name": "Crime"}], "first_release": {"date": "2019-11-01", "country": "global"},
        "remoteIds": [{"sourceName": "IMDB", "id": "tt1302006"}, {"sourceName": "TheMovieDB.com", "id": "398978"}, {"sourceName": "Facebook", "id": "x"}],
    }
    monkeypatch.setattr("VibraVid.services._base.metadata.tvdb.tvdb_client.extended", lambda i, mt, full=False: detail)
    monkeypatch.setattr("VibraVid.services._base.metadata.tvdb.tvdb_client.translation", lambda kind, i, lang="ita": {"name": "L'irlandese", "overview": "Un sicario."})

    info = TvdbProvider().movie("14156")

    assert (info.title, info.original_title, info.plot, info.premiered, info.runtime) == ("L'irlandese", "The Irishman", "Un sicario.", "2019-11-01", 209)
    assert info.rating is None  # TVDB's "score" is a popularity count, not a rating
    assert info.ids == {"tvdb": "14156", "imdb": "tt1302006", "tmdb": "398978"}
    assert info.image_url == "https://artworks.thetvdb.com/banners/p.jpg"


def test_tvdb_image_urls():
    assert TvdbProvider._image("https://x/y.jpg") == "https://x/y.jpg"
    assert TvdbProvider._image("banners/y.jpg") == "https://artworks.thetvdb.com/banners/y.jpg"
    assert TvdbProvider._image(None) is None


def test_tvdb_episode_mapping(monkeypatch):
    listing = [{"id": 1, "seasonNumber": 1, "number": 2, "name": "Cat"}, {"id": 349232, "seasonNumber": 1, "number": 1, "name": "Pilot", "aired": "2008-01-20", "runtime": 58, "image": "https://i/e.jpg", "overview": "EN"}]
    monkeypatch.setattr("VibraVid.services._base.metadata.tvdb.tvdb_client.episodes", lambda sid: listing)
    monkeypatch.setattr("VibraVid.services._base.metadata.tvdb.tvdb_client.translation", lambda kind, i, lang="ita": {"name": "Questione di chimica", "overview": "IT"} if kind == "episodes" else {"name": "Breaking Bad"})

    info = TvdbProvider().episode("81189", 1, 1)

    assert (info.title, info.plot, info.show_title, info.aired, info.runtime) == ("Questione di chimica", "IT", "Breaking Bad", "2008-01-20", 58)
    assert info.ids == {"tvdb": "349232"}


def test_tvdb_episode_falls_back_to_the_english_text_and_misses_cleanly(monkeypatch):
    listing = [{"id": 5, "seasonNumber": 1, "number": 1, "name": "Pilot", "overview": "EN"}]
    monkeypatch.setattr("VibraVid.services._base.metadata.tvdb.tvdb_client.episodes", lambda sid: listing)
    monkeypatch.setattr("VibraVid.services._base.metadata.tvdb.tvdb_client.translation", lambda kind, i, lang="ita": {})
    monkeypatch.setattr("VibraVid.services._base.metadata.tvdb.tvdb_client.extended", lambda i, mt, full=False: {"name": "Breaking Bad"})

    info = TvdbProvider().episode("81189", 1, 1)
    assert (info.title, info.plot, info.show_title) == ("Pilot", "EN", "Breaking Bad")
    assert TvdbProvider().episode("81189", 1, 99) is None


# ── TMDB provider ─────────────────────────────────────────────────────────

def test_tmdb_provider_find_requires_a_trusted_match(monkeypatch):
    monkeypatch.setattr(tmdb_artwork, "resolve_tmdb_id_near_year", lambda *a, **k: 398978)
    monkeypatch.setattr(tmdb_artwork, "is_trusted_match", lambda *a, **k: False)
    assert TmdbProvider().find("movie", "x", None, 2019, None) is None

    monkeypatch.setattr(tmdb_artwork, "is_trusted_match", lambda *a, **k: True)
    assert TmdbProvider().find("movie", "x", None, 2019, None) == "398978"


# ── find_sidecar_target: ordering, fallback, gates ────────────────────────

class _Stub(BaseMetadataProvider):
    def __init__(self, name, found=None, available=True, error=None, by_title=None, title_error=None):
        self.NAME, self._found, self._available, self._error = name, found, available, error
        self._by_title, self._title_error = by_title, title_error
        self.calls = 0
        self.title_calls = 0

    def available(self):
        return self._available

    def find(self, media_type, name, slug, year, site_tmdb_id):
        self.calls += 1
        if self._error:
            raise self._error
        return self._found

    def find_by_title(self, media_type, name):
        self.title_calls += 1
        if self._title_error:
            raise self._title_error
        return self._by_title


def _use(monkeypatch, order, stubs):
    monkeypatch.setattr(metadata, "provider_names", lambda value=None: order)
    monkeypatch.setattr(metadata, "get_provider", lambda name: stubs.get(name))


def test_first_provider_with_a_match_wins(monkeypatch):
    stubs = {"tmdb": _Stub("tmdb", "398978"), "imdb": _Stub("imdb", "tt1")}
    _use(monkeypatch, ["tmdb", "imdb"], stubs)
    assert metadata.find_sidecar_target("movie", _title()) == ("tmdb", "398978", False)
    assert stubs["imdb"].calls == 0


def test_falls_through_to_the_next_provider(monkeypatch):
    stubs = {"tmdb": _Stub("tmdb", None), "imdb": _Stub("imdb", "tt1302006")}
    _use(monkeypatch, ["tmdb", "imdb"], stubs)
    assert metadata.find_sidecar_target("movie", _title()) == ("imdb", "tt1302006", False)


def test_unavailable_providers_are_skipped_without_a_lookup(monkeypatch):
    stubs = {"tvdb": _Stub("tvdb", "81", available=False), "imdb": _Stub("imdb", "tt1")}
    _use(monkeypatch, ["tvdb", "imdb"], stubs)
    assert metadata.find_sidecar_target("movie", _title()) == ("imdb", "tt1", False)
    assert stubs["tvdb"].calls == 0


def test_a_failing_provider_does_not_stop_the_chain(monkeypatch):
    stubs = {"tmdb": _Stub("tmdb", error=RuntimeError("boom")), "imdb": _Stub("imdb", "tt1")}
    _use(monkeypatch, ["tmdb", "imdb"], stubs)
    assert metadata.find_sidecar_target("movie", _title()) == ("imdb", "tt1", False)


def test_no_provider_matching_returns_none(monkeypatch):
    _use(monkeypatch, ["tmdb", "imdb"], {"tmdb": _Stub("tmdb"), "imdb": _Stub("imdb")})
    assert metadata.find_sidecar_target("movie", _title()) is None


def test_the_title_data_is_passed_through(monkeypatch):
    received = {}

    class Spy(_Stub):
        def find(self, media_type, name, slug, year, site_tmdb_id):
            received.update(media_type=media_type, name=name, slug=slug, year=year, site=site_tmdb_id)
            return "1"

    _use(monkeypatch, ["tmdb"], {"tmdb": Spy("tmdb")})
    metadata.find_sidecar_target("movie", _title(tmdb_id=398978))
    assert received == {"media_type": "movie", "name": "The Irishman", "slug": "the-irishman", "year": 2019, "site": 398978}


def test_series_on_an_unlisted_site_are_not_matched_unless_the_site_gave_an_id(monkeypatch):
    stubs = {"imdb": _Stub("imdb", "tt0903747")}
    _use(monkeypatch, ["imdb"], stubs)

    with patch.object(context_tracker.local, "site_name", "sito_sconosciuto", create=True):
        assert metadata.find_sidecar_target("tv", _title(name="Breaking Bad")) is None
        assert stubs["imdb"].calls == 0
        assert metadata.find_sidecar_target("tv", _title(name="Breaking Bad", tmdb_id=1396)) == ("imdb", "tt0903747", False)

    with patch.object(context_tracker.local, "site_name", "streamingcommunity", create=True):
        assert metadata.find_sidecar_target("tv", _title(name="Breaking Bad")) == ("imdb", "tt0903747", False)


def test_films_are_not_subject_to_the_series_gate(monkeypatch):
    _use(monkeypatch, ["imdb"], {"imdb": _Stub("imdb", "tt1")})
    with patch.object(context_tracker.local, "site_name", "sito_sconosciuto", create=True):
        assert metadata.find_sidecar_target("movie", _title()) == ("imdb", "tt1", False)


# ── title-only candidates (series with no year / untrusted site) ──────────

def test_series_without_a_year_fall_back_to_a_title_only_candidate(monkeypatch):
    stubs = {"tmdb": _Stub("tmdb", None, by_title="41956")}
    _use(monkeypatch, ["tmdb"], stubs)
    with patch.object(context_tracker.local, "site_name", "streamingcommunity", create=True):
        assert metadata.find_sidecar_target("tv", _title(name="Delitti in Paradiso", year=None)) == ("tmdb", "41956", True)
    assert stubs["tmdb"].calls == 1  # the reliable pass ran first


def test_series_on_an_unlisted_site_get_a_candidate_without_the_strict_pass(monkeypatch):
    """RaiPlay: not in the numbering whitelist and no year. The episode title confirms numbering later."""
    stubs = {"tmdb": _Stub("tmdb", "should-not-be-asked", by_title="41956")}
    _use(monkeypatch, ["tmdb"], stubs)
    with patch.object(context_tracker.local, "site_name", "raiplay", create=True):
        assert metadata.find_sidecar_target("tv", _title(name="Delitti in Paradiso", year=None)) == ("tmdb", "41956", True)
    assert stubs["tmdb"].calls == 0


def test_a_reliable_match_beats_the_candidate_pass(monkeypatch):
    stubs = {"tmdb": _Stub("tmdb", "1396", by_title="999")}
    _use(monkeypatch, ["tmdb"], stubs)
    with patch.object(context_tracker.local, "site_name", "streamingcommunity", create=True):
        assert metadata.find_sidecar_target("tv", _title(name="Breaking Bad")) == ("tmdb", "1396", False)
    assert stubs["tmdb"].title_calls == 0


def test_the_candidate_pass_tries_providers_in_order_and_skips_failures(monkeypatch):
    stubs = {
        "tmdb": _Stub("tmdb", by_title=None),
        "tvdb": _Stub("tvdb", title_error=RuntimeError("boom")),
        "imdb": _Stub("imdb", by_title="tt0903747"),
    }
    _use(monkeypatch, ["tmdb", "tvdb", "imdb"], stubs)
    with patch.object(context_tracker.local, "site_name", "raiplay", create=True):
        assert metadata.find_sidecar_target("tv", _title(name="X", year=None)) == ("imdb", "tt0903747", True)


def test_films_never_get_a_title_only_candidate(monkeypatch):
    stubs = {"tmdb": _Stub("tmdb", None, by_title="398978")}
    _use(monkeypatch, ["tmdb"], stubs)
    assert metadata.find_sidecar_target("movie", _title(year=None)) is None
    assert stubs["tmdb"].title_calls == 0


def test_no_candidate_means_no_target(monkeypatch):
    _use(monkeypatch, ["tmdb"], {"tmdb": _Stub("tmdb", None, by_title=None)})
    with patch.object(context_tracker.local, "site_name", "raiplay", create=True):
        assert metadata.find_sidecar_target("tv", _title(name="X", year=None)) is None


def test_skip_reasons_are_logged(monkeypatch, caplog):
    _use(monkeypatch, ["tmdb"], {"tmdb": _Stub("tmdb", None)})
    with caplog.at_level("INFO", logger=metadata.logger.name), patch.object(context_tracker.local, "site_name", "raiplay", create=True):
        metadata.find_sidecar_target("tv", _title(name="Delitti in Paradiso", year=None))
    assert any("sidecars skipped for 'Delitti in Paradiso'" in r.getMessage() and "not trusted" in r.getMessage() for r in caplog.records)


def test_no_available_provider_is_logged(monkeypatch, caplog):
    _use(monkeypatch, ["tvdb"], {"tvdb": _Stub("tvdb", "81", available=False)})
    with caplog.at_level("INFO", logger=metadata.logger.name):
        assert metadata.find_sidecar_target("movie", _title()) is None
    assert any("no configured provider is available" in r.getMessage() for r in caplog.records)


# ── find_by_title on the real providers ───────────────────────────────────

def _titled(name, ident, **extra):
    return {"id": ident, "name": name, "original_name": extra.get("original"), **extra}


def test_unique_match_requires_exactly_one_title_match():
    pick = lambda results: BaseMetadataProvider.unique_match(results, "Dune", lambda r: [r["name"]], lambda r: r["id"])  # noqa: E731
    assert pick([_titled("Dune", 1), _titled("Altro", 2)]) == "1"
    assert pick([_titled("Dune", 1), _titled("Dune", 2)]) is None  # remakes / namesakes are ambiguous
    assert pick([_titled("Altro", 2)]) is None
    assert pick([_titled("Dune", 1), _titled("DUNE", 1)]) == "1"  # the same id twice is still one match
    assert pick([_titled("Dune", 0)]) is None  # no usable id


def test_tmdb_find_by_title_for_series_and_films(monkeypatch):
    monkeypatch.setattr(tmdb_client, "_search_tv_with_fallback", lambda q, lang: [{"id": 41956, "name": "Delitti in Paradiso", "original_name": "Death in Paradise"}])
    monkeypatch.setattr(tmdb_client, "_search_movie_with_fallback", lambda q, lang: [{"id": 238, "title": "Il Padrino", "original_title": "The Godfather"}])
    assert TmdbProvider().find_by_title("tv", "Delitti in Paradiso") == "41956"
    assert TmdbProvider().find_by_title("tv", "Death in Paradise") == "41956"  # original title counts
    assert TmdbProvider().find_by_title("movie", "Il Padrino") == "238"
    assert TmdbProvider().find_by_title("tv", "Un'altra serie") is None
    assert TmdbProvider().find_by_title("tv", None) is None


def test_imdb_find_by_title_searches_without_a_year(monkeypatch):
    seen = {}

    def fake(name, media_type, year=None):
        seen.update(name=name, media_type=media_type, year=year)
        return [{"id": "tt0903747", "titleText": {"text": "Breaking Bad"}, "originalTitleText": {"text": "Breaking Bad"}}]

    monkeypatch.setattr("VibraVid.services._base.metadata.imdb.imdb_client.search_titles", fake)
    assert ImdbProvider().find_by_title("tv", "Breaking Bad") == "tt0903747"
    assert seen == {"name": "Breaking Bad", "media_type": "tv", "year": None}


def test_tvdb_find_by_title_uses_aliases_and_rejects_namesakes(monkeypatch):
    hits = [{"tvdb_id": "5", "name": "Il Irlandese", "aliases": ["The Irishman"]}, {"tvdb_id": "6", "name": "Altro"}]
    monkeypatch.setattr("VibraVid.services._base.metadata.tvdb.tvdb_client.search", lambda name, mt: hits)
    assert TvdbProvider().find_by_title("movie", "The Irishman") == "5"
    monkeypatch.setattr("VibraVid.services._base.metadata.tvdb.tvdb_client.search", lambda name, mt: hits + [{"tvdb_id": "7", "name": "The Irishman"}])
    assert TvdbProvider().find_by_title("movie", "The Irishman") is None


def test_base_provider_has_no_title_lookup_by_default():
    assert BaseMetadataProvider().find_by_title("tv", "x") is None
