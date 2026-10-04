# 04.10.26
# ruff: noqa: E402

import os
import sys
import time
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

from searchapp.arr.downloader_service import ArrDownloaderService

def test_renumber_downloaded_files(tmp_path):
    started = time.time()
    video = tmp_path / "Matrimonio S14E02 [1080p].mkv"
    subtitle = tmp_path / "Matrimonio S14E02 [1080p].it.srt"
    other = tmp_path / "Matrimonio S14E12 [1080p].mkv"  # different episode: must not match
    for f in (video, subtitle, other):
        f.write_bytes(b"x")

    renamed = ArrDownloaderService._renumber_downloaded_files(str(tmp_path), started, (14, 2), (17, 2))

    assert renamed == 2
    assert sorted(f.name for f in tmp_path.iterdir()) == [
        "Matrimonio S14E12 [1080p].mkv",
        "Matrimonio S17E02 [1080p].it.srt",
        "Matrimonio S17E02 [1080p].mkv",
    ]


def test_renumber_ignores_old_files_and_missing_marker(tmp_path):
    old = tmp_path / "Matrimonio S14E02 [1080p].mkv"
    old.write_bytes(b"x")
    os.utime(old, (time.time() - 3600, time.time() - 3600))
    assert ArrDownloaderService._renumber_downloaded_files(str(tmp_path), time.time(), (14, 2), (17, 2)) == 0
    assert old.exists()


# ── automatic match by episode title (provider lacks the season) ──────────

TMDB_S17 = {
    1: "Le nuove coppie", 2: "Matrimonio", 3: "Luna di miele", 4: "Nuovi equilibri", 5: "Problemi di coppia?",
    6: "Le convivenze", 7: "La reunion", 8: "La scelta finale", 9: "E poi…",
}
PROVIDER_SEASONS = {
    13: {1: "Episodio 1", 2: "Episodio 2", 3: "Episodio 3", 4: "Episodio 4", 5: "Episodio 5", 6: "Episodio 6",
         7: "La reunion", 8: "La scelta finale", 9: "E poi…"},
    14: {1: "Il primo matrimonio", 2: "Matrimonio", 3: "Luna di miele", 4: "Nuovi equilibri",
         5: "Problemi di coppia?", 6: "Le convivenze", 7: "La reunion"},
}


@pytest.mark.parametrize("ep", [2, 3, 4, 5, 6, 7])
def test_auto_match_by_title(ep):
    assert ArrDownloaderService._match_episode_by_names(PROVIDER_SEASONS, TMDB_S17, ep) == (14, ep)


def test_auto_match_first_episode_with_different_title_uses_neighbours():
    assert ArrDownloaderService._match_episode_by_names(PROVIDER_SEASONS, TMDB_S17, 1) == (14, 1)


def test_auto_match_episode_not_yet_on_provider():
    assert ArrDownloaderService._match_episode_by_names(PROVIDER_SEASONS, TMDB_S17, 8) is None


def test_auto_match_applies_episode_offset():
    provider = {3: {1: "Special", 2: "Matrimonio", 3: "Luna di miele", 4: "Nuovi equilibri"}}
    assert ArrDownloaderService._match_episode_by_names(provider, TMDB_S17, 2) == (3, 2)
    provider = {3: {n + 10: name for n, name in TMDB_S17.items()}}
    assert ArrDownloaderService._match_episode_by_names(provider, TMDB_S17, 4) == (3, 14)


def test_auto_match_ambiguous_returns_none():
    twin = {5: dict(PROVIDER_SEASONS[14]), 6: dict(PROVIDER_SEASONS[14])}
    assert ArrDownloaderService._match_episode_by_names(twin, TMDB_S17, 2) is None


def test_auto_match_needs_at_least_two_matching_titles():
    provider = {1: {1: "Matrimonio", 2: "Altro", 3: "Altro ancora"}}
    assert ArrDownloaderService._match_episode_by_names(provider, TMDB_S17, 2) is None


def test_generic_titles_never_match():
    provider = {1: {n: f"Episodio {n}" for n in range(1, 10)}}
    tmdb = {n: f"Episode {n}" for n in range(1, 10)}
    assert ArrDownloaderService._match_episode_by_names(provider, tmdb, 3) is None


def test_auto_map_skipped_when_provider_has_the_season(monkeypatch):
    svc = ArrDownloaderService(None, None)
    monkeypatch.setattr(svc, "_provider_season_names", lambda provider, payload: PROVIDER_SEASONS)
    monkeypatch.setattr(svc, "_tmdb_season_names", lambda tmdb_id, season: TMDB_S17)
    assert svc._auto_map_by_episode_names("streamingcommunity", {}, 238839, 14, 2) is None  # provider has S14
    assert svc._auto_map_by_episode_names("streamingcommunity", {}, 238839, 17, 2) == (14, 2)
    assert svc._auto_map_by_episode_names("streamingcommunity", {}, None, 17, 2) is None  # no TMDB id


def test_titles_plausible_for_providers_that_cannot_attest_an_id():
    plausible = ArrDownloaderService._titles_plausible
    assert plausible("Un posto al sole", "Un posto al sole")
    assert not plausible("Un posto al sole", "Mi Compro Un'Isola")
    assert plausible("Ted", "Ted")  # no word over 3 letters: exact match only
    assert not plausible("Ted", "Tedx Talks")
    assert not plausible("", "Anything")


def _selector(monkeypatch, order, provider="discoveryplus"):
    svc = ArrDownloaderService(None, None)
    monkeypatch.setattr(svc, "_provider_season_names", lambda *_: None)
    svc._provider_season_order[(provider, "None")] = order
    return lambda season: svc._provider_season_selector(provider, {}, season)


def test_season_selector_uses_position_when_numbers_do_not_start_at_one(monkeypatch):
    select = _selector(monkeypatch, ["4", "5", "6", "7", "17"])
    assert select(4) == "1"    # "4" alone would pick the 4th entry, season 7
    assert select(7) == "4"
    assert select(17) == "5"
    assert select(1) == "1"    # not listed: unchanged (the season guard handles it)


def test_season_selector_unchanged_when_numbering_matches_position(monkeypatch):
    select = _selector(monkeypatch, ["1", "2", "3"])
    assert [select(n) for n in (1, 2, 3)] == ["1", "2", "3"]
    assert select(9) == "9"


def test_season_selector_never_touches_anime_or_unknown_metadata(monkeypatch):
    assert _selector(monkeypatch, ["4", "5"], "animeworld")(5) == "5"
    assert _selector(monkeypatch, [])(5) == "5"


def test_provider_lacks_season(monkeypatch):
    svc = ArrDownloaderService(None, None)
    monkeypatch.setattr(svc, "_provider_season_names", lambda provider, payload: PROVIDER_SEASONS)
    assert svc._provider_lacks_season("nove", {}, 17) is True
    assert svc._provider_lacks_season("nove", {}, 14) is False
    # anime entries are one-season-per-entry: never blocked
    assert svc._provider_lacks_season("animeworld", {}, 2) is False
    assert svc._provider_lacks_season("animeunity", {}, 2) is False
    # metadata unavailable: don't block
    monkeypatch.setattr(svc, "_provider_season_names", lambda provider, payload: None)
    assert svc._provider_lacks_season("nove", {}, 17) is False


# ── TMDB id resolved from air dates ───────────────────────────────────────

SONARR_DATES = {1: "2026-09-02", 2: "2026-09-02", 3: "2026-09-09", 4: "2026-09-16"}


def test_air_dates_match_same_season():
    tmdb = {1: "2026-09-02", 2: "2026-09-02", 3: "2026-09-09", 4: "2026-09-16", 5: "2026-09-23"}
    assert ArrDownloaderService._air_dates_match(SONARR_DATES, tmdb) is True


def test_air_dates_match_tolerates_one_day_offset():
    tmdb = {n: d[:8] + f"{int(d[8:]) + 1:02d}" for n, d in SONARR_DATES.items()}
    assert ArrDownloaderService._air_dates_match(SONARR_DATES, tmdb) is True


def test_air_dates_mismatch_or_too_little_overlap():
    assert ArrDownloaderService._air_dates_match(SONARR_DATES, {1: "2026-09-02", 2: "2026-10-30"}) is False
    assert ArrDownloaderService._air_dates_match(SONARR_DATES, {1: "2026-09-02"}) is False  # one overlap, season has 4
    assert ArrDownloaderService._air_dates_match(SONARR_DATES, {}) is False
    assert ArrDownloaderService._air_dates_match({1: "2026-09-02"}, {1: "2026-09-02"}) is True  # single-episode season


class _FakeSonarr:
    def get_episodes_for_series(self, series_id):
        return [{"seasonNumber": 17, "episodeNumber": n, "airDate": d} for n, d in SONARR_DATES.items()]

    def get_series_by_id(self, series_id):
        return {"originalLanguage": {"name": "Italian"}}


class _FakeTmdb:
    api_key = "k"

    def __init__(self, seasons, candidates):
        self.seasons, self.candidates, self.calls = seasons, candidates, []

    def _make_request(self, endpoint, params=None):
        self.calls.append((endpoint, params))
        if endpoint == "discover/tv":
            return {"results": [{"id": c} for c in self.candidates], "total_pages": 1}
        cid = int(endpoint.split("/")[1])
        if cid not in self.seasons:
            raise RuntimeError("404")
        return {"episodes": [{"episode_number": n, "air_date": d} for n, d in self.seasons[cid].items()]}


def _resolver(monkeypatch, tmdb):
    svc = ArrDownloaderService(_FakeSonarr(), None)
    monkeypatch.setattr(ArrDownloaderService, "_tmdb_client", staticmethod(lambda: tmdb))
    return svc


def test_resolves_tmdb_id_only_candidate_with_same_air_dates(monkeypatch):
    tmdb = _FakeTmdb({1: {1: "2020-01-01", 2: "2020-01-02"}, 2: dict(SONARR_DATES), 3: {1: "2026-09-02"}}, [1, 2, 3, 4])
    svc = _resolver(monkeypatch, tmdb)
    assert svc._resolve_tmdb_id_by_air_dates({"id": 1, "title": "X"}, 17) == 2
    discover = next(p for e, p in tmdb.calls if e == "discover/tv")
    assert discover["with_original_language"] == "it" and discover["air_date.gte"] == "2026-09-02"


def test_resolution_refuses_ambiguous_or_missing(monkeypatch):
    twin = _FakeTmdb({1: dict(SONARR_DATES), 2: dict(SONARR_DATES)}, [1, 2])
    assert _resolver(monkeypatch, twin)._resolve_tmdb_id_by_air_dates({"id": 1, "title": "X"}, 17) is None
    none = _FakeTmdb({1: {1: "2020-01-01", 2: "2020-01-02"}}, [1])
    assert _resolver(monkeypatch, none)._resolve_tmdb_id_by_air_dates({"id": 1, "title": "X"}, 17) is None


# ── Sonarr tmdbId vs tvdbId disagree ──────────────────────────────────────

SERIE_S17 = {"id": 1, "title": "X", "seasons": [{"number": 17, "episodes": [{"id": 1, "episodeNumber": 1}]}]}


def test_identity_conflict_resolved_by_the_candidate_with_the_same_air_dates(monkeypatch):
    tmdb = _FakeTmdb({10: {1: "2020-01-01", 2: "2020-01-08"}, 20: dict(SONARR_DATES)}, [])
    svc = _resolver(monkeypatch, tmdb)
    assert svc._resolve_identity_conflict(dict(SERIE_S17), [10, 20]) == 20
    assert svc._resolve_identity_conflict(dict(SERIE_S17), [20, 10]) == 20


def test_identity_conflict_stays_unresolved_when_unclear(monkeypatch):
    svc = _resolver(monkeypatch, _FakeTmdb({10: dict(SONARR_DATES), 20: dict(SONARR_DATES)}, []))
    assert svc._resolve_identity_conflict(dict(SERIE_S17), [10, 20]) is None  # both match
    svc = _resolver(monkeypatch, _FakeTmdb({10: {1: "2020-01-01", 2: "2020-01-08"}}, []))
    assert svc._resolve_identity_conflict(dict(SERIE_S17), [10, 20]) is None  # none matches
    assert svc._resolve_identity_conflict({"id": 1, "seasons": []}, [10, 20]) is None  # no season to compare
