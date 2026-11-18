# 09.10.26
# ruff: noqa: E402

import json
import os
import sys
import time
from datetime import timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
GUI_DIR = ROOT / "GUI"
for path in (ROOT, GUI_DIR):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "webgui.settings")

import django

django.setup()

import pytest
from django.core.management import call_command
from django.test import Client
from django.utils import timezone

from scheduleapp import runner
from scheduleapp.models import ScheduledDownload
from searchapp.views import _shared, download

MOVIE = {"name": "Test movie", "type": "film", "is_movie": True, "poster": "https://img.example/p.jpg"}
SERIES = {"name": "Test show", "type": "tv", "is_movie": False}


@pytest.fixture(scope="module")
def client():
    call_command("migrate", verbosity=0)
    return Client(HTTP_HOST="localhost")


@pytest.fixture(autouse=True)
def _clean(client):
    ScheduledDownload.objects.all().delete()
    yield
    ScheduledDownload.objects.all().delete()


@pytest.fixture
def started(monkeypatch):
    """Record what reaches the download queue instead of downloading."""
    calls = []

    def fake(site, item_payload, season=None, episodes=None, media_type="Film", **kwargs):
        calls.append({"site": site, "name": item_payload["name"], "season": season, "episodes": episodes, **kwargs})

    monkeypatch.setattr(download, "_run_download_in_thread", fake)
    monkeypatch.setattr(_shared, "_run_download_in_thread", fake)
    return calls


def _add(client, item=MOVIE, in_seconds=3600, **fields):
    body = {"source_alias": "mapple", "item_payload": json.dumps(item), "run_at": str(time.time() + in_seconds), **fields}
    return client.post("/schedule/add/", body)


# --- adding ---------------------------------------------------------------------------------------------------------------
def test_add_stores_the_form_without_starting_anything(client, started):
    response = _add(client, quality="720p", ignored_field="x")

    assert response.status_code == 200
    job = ScheduledDownload.objects.get()
    assert (job.title, job.source_alias, job.status, job.poster) == ("Test movie", "mapple", "pending", MOVIE["poster"])
    assert job.params == {"source_alias": "mapple", "item_payload": json.dumps(MOVIE), "quality": "720p"}
    assert started == []


@pytest.mark.parametrize(
    "fields",
    [
        {"run_at": str(time.time() - 3600)},
        {"run_at": "soon"},
        {"run_at": str(time.time() + 86400 * 800)},
        {"item_payload": "not json"},
        {"item_payload": "{}"},
        {"source_alias": ""},
        {"download_type": "episodes", "season_number": "1", "selected_episodes": ""},
        {"download_type": "selected_seasons", "selected_seasons": ""},
    ],
)
def test_add_rejects_bad_input(client, fields):
    assert _add(client, **fields).status_code == 400
    assert not ScheduledDownload.objects.exists()


@pytest.mark.parametrize("url", ["/schedule/add/", "/schedule/update/"])
def test_mutating_endpoints_refuse_get(client, url):
    assert client.get(url).status_code == 405


def test_list_reports_jobs_in_time_order(client):
    _add(client, in_seconds=7200)
    _add(client, item=SERIES, in_seconds=600, download_type="episodes", season_number="2", selected_episodes="1-3")

    items = client.get("/schedule/list/").json()["items"]

    assert [(i["title"], i["summary"]) for i in items] == [("Test show", "S2 · E1-3"), ("Test movie", "")]
    assert items[0]["run_at"] < items[1]["run_at"]


# --- running --------------------------------------------------------------------------------------------------------------
def test_nothing_starts_before_its_time(client, started):
    _add(client)

    assert runner.run_due() == 0
    assert started == []
    assert ScheduledDownload.objects.get().status == "pending"


def test_due_movie_goes_through_the_normal_download_path(client, started):
    _add(client, quality="720p")

    assert runner.run_due(now=timezone.now() + timedelta(hours=1, minutes=1)) == 1

    assert started == [{"site": "mapple", "name": "Test movie", "season": None, "episodes": None,
                        "audio_format": None, "quality": "720p"}]
    assert not ScheduledDownload.objects.exists()


def test_due_episodes_go_through_the_series_download_path(client, started):
    _add(client, item=SERIES, download_type="episodes", season_number="2", selected_episodes="1-3")

    assert runner.run_due(now=timezone.now() + timedelta(hours=1, minutes=1)) == 1

    assert [(c["name"], c["season"], c["episodes"]) for c in started] == [("Test show", "2", "1-3")]


def test_job_overdue_beyond_the_grace_period_is_kept_as_missed(client, started):
    _add(client)

    assert runner.run_due(now=timezone.now() + timedelta(hours=1, seconds=runner.GRACE_SECONDS + 120)) == 0

    assert started == []
    assert ScheduledDownload.objects.get().status == "missed"


def test_a_download_that_cannot_start_is_kept_as_failed(client, monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("provider gone")

    monkeypatch.setattr(download, "_run_download_in_thread", boom)
    _add(client)

    assert runner.run_due(now=timezone.now() + timedelta(hours=1, minutes=1)) == 0

    job = ScheduledDownload.objects.get()
    assert (job.status, job.note) == ("failed", "provider gone")


# --- changing -------------------------------------------------------------------------------------------------------------
def test_remove_deletes_the_job(client):
    _add(client)
    job = ScheduledDownload.objects.get()

    assert client.post("/schedule/update/", {"id": job.pk, "action": "remove"}).status_code == 200
    assert not ScheduledDownload.objects.exists()


def test_move_changes_the_time_and_revives_a_missed_job(client):
    _add(client)
    job = ScheduledDownload.objects.get()
    ScheduledDownload.objects.filter(pk=job.pk).update(status="missed", note="late")
    target = time.time() + 86400

    assert client.post("/schedule/update/", {"id": job.pk, "action": "move", "run_at": str(target)}).status_code == 200

    job.refresh_from_db()
    assert (job.status, job.note) == ("pending", "")
    assert abs(job.run_at.timestamp() - target) < 1


def test_run_now_makes_the_job_due(client, started):
    _add(client, in_seconds=86400)
    job = ScheduledDownload.objects.get()

    assert client.post("/schedule/update/", {"id": job.pk, "action": "run"}).status_code == 200
    assert runner.run_due() == 1
    assert len(started) == 1


@pytest.mark.parametrize("body, status", [({"id": "999999", "action": "remove"}, 404), ({"id": "x", "action": "remove"}, 404),
                                          ({"action": "nope"}, 400), ({"action": "move", "run_at": "0"}, 400)])
def test_update_rejects_bad_input(client, body, status):
    _add(client)
    body.setdefault("id", ScheduledDownload.objects.get().pk)

    assert client.post("/schedule/update/", body).status_code == status
    assert ScheduledDownload.objects.get().status == "pending"
