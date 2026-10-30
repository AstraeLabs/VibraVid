# 19.08.26
# ruff: noqa: E402

import os
import sys
from pathlib import Path

repo_root = Path(__file__).resolve().parents[2]
gui_dir = repo_root / "GUI"
for p in (str(repo_root), str(gui_dir)):
    if p not in sys.path:
        sys.path.insert(0, p)

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "webgui.settings")

import django

django.setup()

import pytest

from searchapp.api.animeunity import AnimeUnityAPI
from searchapp.api.animeworld import AnimeWorldAPI
from searchapp.api.base import Entries
from searchapp.arr.downloader_service import ArrDownloaderService
from VibraVid.utils.media import anime_id_map


@pytest.fixture
def animeunity_api(monkeypatch):
    monkeypatch.setattr(AnimeUnityAPI, "_load_config", lambda self: None)
    api = AnimeUnityAPI()
    api.base_url = "https://www.animeunity.example"
    return api


@pytest.fixture
def animeworld_api(monkeypatch):
    monkeypatch.setattr(AnimeWorldAPI, "_load_config", lambda self: None)
    api = AnimeWorldAPI()
    api.base_url = "https://www.animeworld.example"
    api._external_id_cache = {}
    return api


# ── AnimeUnity ────────────────────────────────────────────────────────────


def test_animeunity_resolve_tmdb_id_uses_direct_id_without_crosswalk(animeunity_api, monkeypatch):
    called = []
    monkeypatch.setattr(anime_id_map, "resolve_tmdb_id", lambda *a, **k: called.append(1) or "999")

    media_item = Entries(name="Naruto", type="TV", tmdb_id="42", raw_data={"mal_id": 306})
    assert animeunity_api.resolve_tmdb_id(media_item) == "42"
    assert called == []  # crosswalk must not be consulted when a direct id is already present


def test_animeunity_resolve_tmdb_id_via_mal_id_crosswalk(animeunity_api, monkeypatch):
    monkeypatch.setattr(
        anime_id_map,
        "resolve_tmdb_id",
        lambda media_type, mal_id=None, anilist_id=None: "12144" if mal_id == 306 else None,
    )

    raw_data = {"mal_id": 306, "anilist_id": 306}
    media_item = Entries(name="Abenobashi", type="TV", tmdb_id=None, raw_data=raw_data)
    resolved = animeunity_api.resolve_tmdb_id(media_item)

    assert resolved == "12144"
    assert media_item.tmdb_id == "12144"  # cached back onto the result for later reuse
    assert raw_data["tmdb_id"] == "12144"


def test_animeunity_resolve_tmdb_id_movie_type_passed_through(animeunity_api, monkeypatch):
    seen_media_types = []

    def fake_resolve(media_type, mal_id=None, anilist_id=None):
        seen_media_types.append(media_type)
        return "555"

    monkeypatch.setattr(anime_id_map, "resolve_tmdb_id", fake_resolve)

    media_item = Entries(name="Some Anime Movie", type="Movie", tmdb_id=None, raw_data={"mal_id": 99999})
    assert animeunity_api.resolve_tmdb_id(media_item) == "555"
    assert seen_media_types == ["movie"]


def test_animeunity_resolve_tmdb_id_no_ids_never_calls_crosswalk(animeunity_api, monkeypatch):
    called = []
    monkeypatch.setattr(anime_id_map, "resolve_tmdb_id", lambda *a, **k: called.append(1) or "999")

    media_item = Entries(name="No Ids Anime", type="TV", tmdb_id=None, raw_data={})
    assert animeunity_api.resolve_tmdb_id(media_item) is None
    assert called == []


def test_animeunity_resolve_tmdb_id_unknown_anime_returns_none(animeunity_api, monkeypatch):
    monkeypatch.setattr(anime_id_map, "resolve_tmdb_id", lambda *a, **k: None)

    media_item = Entries(name="Obscure Anime", type="TV", tmdb_id=None, raw_data={"mal_id": 424242})
    assert animeunity_api.resolve_tmdb_id(media_item) is None


# ── AnimeWorld ────────────────────────────────────────────────────────────


def test_animeworld_resolve_tmdb_id_fetches_detail_page_lazily(animeworld_api, monkeypatch):
    fetch_calls = []

    def fake_fetch(self, url):
        fetch_calls.append(url)
        return ("306", "306")

    monkeypatch.setattr(AnimeWorldAPI, "_fetch_external_ids", fake_fetch)
    monkeypatch.setattr(
        anime_id_map,
        "resolve_tmdb_id",
        lambda media_type, mal_id=None, anilist_id=None: "12144" if mal_id == "306" else None,
    )

    media_item = Entries(
        name="Abenobashi",
        type="TV",
        tmdb_id=None,
        url="https://www.animeworld.example/play/abenobashi",
        raw_data={},
    )
    resolved = animeworld_api.resolve_tmdb_id(media_item)

    assert resolved == "12144"
    assert fetch_calls == ["https://www.animeworld.example/play/abenobashi"]
    assert media_item.tmdb_id == "12144"


def test_animeworld_resolve_tmdb_id_no_url_skips_fetch(animeworld_api, monkeypatch):
    fetch_calls = []
    monkeypatch.setattr(AnimeWorldAPI, "_fetch_external_ids", lambda self, url: fetch_calls.append(url) or (None, None))

    media_item = Entries(name="No Url Anime", type="TV", tmdb_id=None, url=None, raw_data={})
    assert animeworld_api.resolve_tmdb_id(media_item) is None
    assert fetch_calls == []


def test_animeworld_resolve_tmdb_id_no_external_ids_found(animeworld_api, monkeypatch):
    monkeypatch.setattr(AnimeWorldAPI, "_fetch_external_ids", lambda self, url: (None, None))
    called = []
    monkeypatch.setattr(anime_id_map, "resolve_tmdb_id", lambda *a, **k: called.append(1) or "999")

    media_item = Entries(name="No External Ids", type="TV", tmdb_id=None, url="https://x.example/play/y", raw_data={})
    assert animeworld_api.resolve_tmdb_id(media_item) is None
    assert called == []  # nothing to look up -> crosswalk skipped entirely


def test_animeworld_fetch_external_ids_extracts_and_caches(animeworld_api, monkeypatch):
    fake_html = """
    <a class="watchlist-edit-mal" href="https://myanimelist.net/anime/306">MyAnimeList</a>
    <a class="watchlist-edit-anilist" href="https://anilist.co/anime/306">AniList</a>
    """

    http_calls = []

    class _FakeResponse:
        text = fake_html

        def raise_for_status(self):
            pass

    class _FakeClient:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def get(self, url):
            http_calls.append(url)
            return _FakeResponse()

    import searchapp.api.animeworld as animeworld_module

    monkeypatch.setattr(animeworld_module, "create_client", lambda *a, **k: _FakeClient())

    url = "https://www.animeworld.example/play/abenobashi"
    mal_id, anilist_id = animeworld_api._fetch_external_ids(url)
    assert (mal_id, anilist_id) == ("306", "306")
    assert http_calls == [url]

    # Second call within the TTL must be served from cache, not refetch.
    mal_id2, anilist_id2 = animeworld_api._fetch_external_ids(url)
    assert (mal_id2, anilist_id2) == ("306", "306")
    assert http_calls == [url]


def test_animeworld_fetch_external_ids_missing_links_returns_none(animeworld_api, monkeypatch):
    class _FakeResponse:
        text = "<html>no external id links here</html>"

        def raise_for_status(self):
            pass

    class _FakeClient:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def get(self, url):
            return _FakeResponse()

    import searchapp.api.animeworld as animeworld_module

    monkeypatch.setattr(animeworld_module, "create_client", lambda *a, **k: _FakeClient())

    mal_id, anilist_id = animeworld_api._fetch_external_ids("https://www.animeworld.example/play/nothing")
    assert (mal_id, anilist_id) == (None, None)


# ── Split-cour disambiguation ("Part 1" vs "Part 2") ────────────────────────

_AOT_PART1 = Entries(
    name="Attack on Titan 3",
    type="TV",
    raw_data={"mal_id": 35760, "anilist_id": 99147, "episodes_count": 12},
)
_AOT_PART2 = Entries(
    name="Attack on Titan 3 Part 2",
    type="TV",
    raw_data={"mal_id": 38524, "anilist_id": 104578, "episodes_count": 10},
)


@pytest.fixture
def arr_service():
    from types import SimpleNamespace

    return ArrDownloaderService(sonarr=SimpleNamespace(), radarr=SimpleNamespace())


def _fake_crosswalk_resolver(records_by_mal):
    def resolve(candidates, season_num, absolute_episode):
        for index, cand in enumerate(candidates):
            entry = records_by_mal.get(cand.get("mal_id"))
            if not entry:
                continue
            if entry["season"] != season_num:
                continue
            local_episode = absolute_episode - entry["offset"]
            if local_episode < 1 or local_episode > cand.get("episodes_count", float("inf")):
                continue
            return {"candidate_index": index, "local_episode": local_episode}
        return None

    return resolve


@pytest.fixture
def fake_split_cour_resolver(monkeypatch):
    resolver = _fake_crosswalk_resolver(
        {
            35760: {"season": 3, "offset": 0},
            38524: {"season": 3, "offset": 12},
        }
    )
    monkeypatch.setattr(anime_id_map, "resolve_split_cour_episode", resolver)
    return resolver


def test_disambiguate_split_cour_picks_part2_for_absolute_episode_13(arr_service, fake_split_cour_resolver):
    candidates = [_AOT_PART1, _AOT_PART2]
    narrowed, local_episode = arr_service._disambiguate_split_cour("animeunity", candidates, 3, 13)
    assert local_episode == 1
    assert narrowed == [_AOT_PART2]


def test_disambiguate_split_cour_picks_part1_for_absolute_episode_5(arr_service, fake_split_cour_resolver):
    candidates = [_AOT_PART1, _AOT_PART2]
    narrowed, local_episode = arr_service._disambiguate_split_cour("animeunity", candidates, 3, 5)
    assert local_episode == 5
    assert narrowed == [_AOT_PART1]


def test_disambiguate_split_cour_no_match_falls_through_unchanged(arr_service, fake_split_cour_resolver):
    candidates = [_AOT_PART1, _AOT_PART2]
    narrowed, local_episode = arr_service._disambiguate_split_cour("animeunity", candidates, 3, 23)
    assert local_episode is None
    assert narrowed == candidates


def test_disambiguate_split_cour_skips_non_animeunity_providers(arr_service, fake_split_cour_resolver):
    candidates = [_AOT_PART1, _AOT_PART2]
    narrowed, local_episode = arr_service._disambiguate_split_cour("animeworld", candidates, 3, 13)
    assert local_episode is None
    assert narrowed == candidates


def test_disambiguate_split_cour_skips_when_episode_number_missing(arr_service, fake_split_cour_resolver):
    candidates = [_AOT_PART1, _AOT_PART2]
    narrowed, local_episode = arr_service._disambiguate_split_cour("animeunity", candidates, 3, None)
    assert local_episode is None
    assert narrowed == candidates


def test_disambiguate_split_cour_skips_single_candidate(arr_service, fake_split_cour_resolver):
    candidates = [_AOT_PART2]
    narrowed, local_episode = arr_service._disambiguate_split_cour("animeunity", candidates, 3, 13)
    assert local_episode is None
    assert narrowed == candidates
