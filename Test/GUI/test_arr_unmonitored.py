# 09.10.26
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

from searchapp.arr import arr_service
from searchapp.arr.arr_service import (
    _is_radarr_movie_monitored,
    _is_sonarr_episode_monitored,
    _reconcile_unmonitored_pending,
    _skip_unmonitored_episode,
    _skip_unmonitored_movie,
)
from searchapp.models import ArrMediaRequest, ArrProcessingQueue


class FakeRadarr:
    def __init__(self, movie=None, error=None):
        self.movie, self.error = movie, error

    def get_movie_by_id(self, movie_id):
        if self.error:
            raise self.error
        return self.movie


class FakeSonarr:
    def __init__(self, series=None, episode=None, error=None, episodes=()):
        self.series, self.episode, self.error, self.episodes = series or {}, episode or {}, error, list(episodes)

    def get_series_by_id(self, series_id):
        if self.error:
            raise self.error
        return self.series

    def get_episode(self, episode_id):
        if self.error:
            raise self.error
        return self.episode

    def get_episodes_for_series(self, series_id):
        return self.episodes


def test_radarr_item_flag_wins_without_lookup():
    assert not _is_radarr_movie_monitored(FakeRadarr(error=RuntimeError("no call")), {"id": 1, "monitored": False})


def test_radarr_live_state_is_checked():
    item = {"id": 1, "monitored": True}
    assert not _is_radarr_movie_monitored(FakeRadarr({"monitored": False}), item)
    assert _is_radarr_movie_monitored(FakeRadarr({"monitored": True}), item)
    assert _is_radarr_movie_monitored(FakeRadarr({}), item)  # key missing -> monitored


def test_radarr_lookup_failure_is_fail_open():
    assert _is_radarr_movie_monitored(FakeRadarr(error=RuntimeError("down")), {"id": 1})


def test_sonarr_item_or_episode_flag_means_unmonitored():
    sonarr = FakeSonarr({"monitored": True}, {"monitored": True})
    assert not _is_sonarr_episode_monitored(sonarr, {"id": 1, "monitored": False}, {"id": 5})
    assert not _is_sonarr_episode_monitored(sonarr, {"id": 1}, {"id": 5, "monitored": False})


def test_sonarr_live_series_and_episode_state_is_checked():
    item, ep = {"id": 1}, {"id": 5}
    assert not _is_sonarr_episode_monitored(FakeSonarr({"monitored": False}, {"monitored": True}), item, ep)
    assert not _is_sonarr_episode_monitored(FakeSonarr({"monitored": True}, {"monitored": False}), item, ep)
    assert _is_sonarr_episode_monitored(FakeSonarr({"monitored": True}, {"monitored": True}), item, ep)


def test_sonarr_lookup_failure_is_fail_open():
    assert _is_sonarr_episode_monitored(FakeSonarr(error=RuntimeError("down")), {"id": 1}, {"id": 5})


def test_skip_helpers_report_reason_only_when_unmonitored(monkeypatch):
    calls = []
    monkeypatch.setattr(arr_service, "_skip_pending_queue", lambda *a: calls.append(a) or True)

    movie = {"id": 1, "title": "M", "monitored": False}
    assert _skip_unmonitored_movie(None, movie, "[t]")
    assert calls[-1] == (movie, "Radarr movie is unmonitored")

    season, ep = {"number": 2}, {"id": 5, "episodeNumber": 3, "monitored": False}
    serie = {"id": 9, "title": "S"}
    assert _skip_unmonitored_episode(None, serie, season, ep, "[t]")
    assert calls[-1] == (serie, "Sonarr episode is unmonitored", 2, 3)

    calls.clear()
    assert not _skip_unmonitored_movie(None, {"id": 1, "monitored": True}, "[t]")
    assert not _skip_unmonitored_episode(None, serie, season, {"id": 5, "episodeNumber": 3}, "[t]")
    assert calls == []


class FakeRequest:
    def __init__(self, source, arr_id, episode_id=None):
        self.arr_source, self.arr_id, self.episode_id = source, arr_id, episode_id
        self.season_number = self.episode_number = None
        self.status = ArrMediaRequest.Status.PENDING

    def save(self, update_fields=None):
        pass


class FakeQueueEntry:
    def __init__(self, request, started=False):
        self.media_request = request
        self.started_at = object() if started else None
        self.completed_at = None
        self.success = None
        self.dedup_key = f"{request.arr_source}_{request.arr_id}"

    def save(self, update_fields=None):
        pass


class FakeQueueManager:
    def __init__(self, entries):
        self.entries = entries

    def filter(self, **kwargs):
        return self

    def select_related(self, *args):
        return self.entries


def test_reconcile_skips_unmonitored_pending_and_keeps_started(monkeypatch):
    pending_movie = FakeQueueEntry(FakeRequest("radarr", 1))
    started_movie = FakeQueueEntry(FakeRequest("radarr", 1), started=True)
    monitored_movie = FakeQueueEntry(FakeRequest("radarr", 2))
    unmonitored_series = FakeQueueEntry(FakeRequest("sonarr", 7, episode_id=70))
    unmonitored_episode = FakeQueueEntry(FakeRequest("sonarr", 8, episode_id=80))
    entries = [pending_movie, started_movie, monitored_movie, unmonitored_series, unmonitored_episode]
    monkeypatch.setattr(ArrProcessingQueue, "objects", FakeQueueManager(entries), raising=False)

    class Radarr:
        def get_movie_by_id(self, movie_id):
            return {"monitored": movie_id == 2}

    class Sonarr:
        def get_series_by_id(self, series_id):
            return {"monitored": series_id != 7}

        def get_episode(self, episode_id):
            return {"monitored": False}

    assert _reconcile_unmonitored_pending(sonarr=Sonarr(), radarr=Radarr()) == 3

    skipped = ArrMediaRequest.Status.SKIPPED
    assert pending_movie.media_request.status == skipped
    assert unmonitored_series.media_request.status == skipped
    assert unmonitored_episode.media_request.status == skipped
    assert started_movie.media_request.status == ArrMediaRequest.Status.PENDING  # in-flight download untouched
    assert monitored_movie.media_request.status == ArrMediaRequest.Status.PENDING
