# 05.10.26
# ruff: noqa: E402

"""ARR integration: the Sonarr/Radarr REST clients (against a local stub server) and the tag/filter logic of ``ArrProcessorService``."""

import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parents[2]
GUI_DIR = ROOT / "GUI"
for path in (ROOT, GUI_DIR):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "webgui.settings")

import django

django.setup()

import pytest

from searchapp.arr.clients import radarr_client, sonarr_client
from searchapp.arr.processor_service import ArrProcessorService

API_KEY = "key-123"


class Arr(BaseHTTPRequestHandler):
    """Minimal Sonarr/Radarr v3 stand-in: checks the API key, serves canned JSON and can fail the first N requests."""
    routes: dict = {}
    fail_first = 0
    calls: list = []

    def _serve(self):
        parsed = urlparse(self.path)
        Arr.calls.append((self.command, parsed.path, parse_qs(parsed.query), self.headers.get("X-Api-Key")))
        if self.headers.get("X-Api-Key") != API_KEY:
            return self._reply(401, {"error": "unauthorized"})
        if Arr.fail_first > 0:
            Arr.fail_first -= 1
            return self._reply(500, {"error": "boom"})
        body = Arr.routes.get(parsed.path)
        if callable(body):
            body = body(parse_qs(parsed.query))
        if body is None:
            return self._reply(404, {"error": "not found"})
        return self._reply(200, body)

    do_GET = do_POST = do_PUT = _serve

    def _reply(self, status, body):
        raw = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def log_message(self, *args):
        pass


@pytest.fixture
def arr():
    Arr.routes, Arr.fail_first, Arr.calls = {}, 0, []
    server = HTTPServer(("127.0.0.1", 0), Arr)
    threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True).start()
    Arr.url = f"http://127.0.0.1:{server.server_port}/"
    yield Arr
    server.shutdown()
    server.server_close()


CLIENTS = [
    pytest.param(sonarr_client.SonarrClient, id="sonarr"),
    pytest.param(radarr_client.RadarrClient, id="radarr"),
]


@pytest.fixture(params=CLIENTS)
def client(request, arr):
    return request.param(arr.url, API_KEY, timeout=5, max_retries=3)


# --- client behaviour shared by Sonarr and Radarr -------------------------------------------------------------------------------
def test_requests_carry_the_api_key_and_hit_the_v3_api(client, arr):
    arr.routes["/api/v3/system/status"] = {"version": "4.0"}

    assert client.system_status() == {"version": "4.0"}
    assert arr.calls[0][1:] == ("/api/v3/system/status", {}, API_KEY)


def test_a_wrong_api_key_is_reported_unavailable(arr):
    arr.routes["/api/v3/system/status"] = {"version": "4.0"}

    assert sonarr_client.SonarrClient(arr.url, "bad-key", timeout=5, max_retries=1).is_available() is False
    assert radarr_client.RadarrClient(arr.url, API_KEY, timeout=5, max_retries=1).is_available() is True


def test_transient_server_errors_are_retried(client, arr):
    arr.routes["/api/v3/system/status"] = {"ok": True}
    arr.fail_first = 2

    assert client.system_status() == {"ok": True}
    assert len(arr.calls) == 3


def test_gives_up_after_max_retries(client, arr):
    arr.routes["/api/v3/system/status"] = {"ok": True}
    arr.fail_first = 3

    assert client.is_available() is False
    assert len(arr.calls) == 3


def test_get_all_missing_walks_every_page_until_it_is_empty(client, arr):
    pages = {"1": [{"id": 1}, {"id": 2}], "2": [{"id": 3}], "3": []}
    arr.routes["/api/v3/wanted/missing"] = lambda query: {"records": pages[query["page"][0]]}

    assert client.get_all_missing() == [{"id": 1}, {"id": 2}, {"id": 3}]
    assert [c[2]["page"] for c in arr.calls] == [["1"], ["2"], ["3"]]


def test_tags_map_lowercases_labels_and_swallows_errors(client, arr):
    arr.routes["/api/v3/tag"] = [{"id": 1, "label": "Provider-StreamingCommunity"}, {"id": 2, "label": "HOLD"}]
    assert client.get_tags_map() == {1: "provider-streamingcommunity", 2: "hold"}

    arr.routes.clear()
    assert client.get_tags_map() == {}


def test_safe_get_returns_an_empty_list_on_errors(client, arr):
    assert client._get_safe("/missing") == []
    arr.routes["/api/v3/manualimport"] = [{"path": "/x"}]
    assert client._get_safe("/manualimport") == [{"path": "/x"}]


@pytest.mark.parametrize("status", ["completed", "failed", "aborted"])
def test_wait_command_returns_the_terminal_status(client, arr, status):
    arr.routes["/api/v3/command/7"] = {"status": status}

    assert client.wait_command(7, timeout=5) == status


def test_wait_command_polls_until_the_command_finishes(client, arr, monkeypatch):
    states = iter(["queued", "started", "completed"])
    arr.routes["/api/v3/command/9"] = lambda query: {"status": next(states)}
    monkeypatch.setattr("time.sleep", lambda seconds: None)

    assert client.wait_command(9, timeout=60) == "completed"


def test_wait_command_reports_unknown_when_polling_fails_and_timeout_when_it_never_ends(client, arr, monkeypatch):
    monkeypatch.setattr("time.sleep", lambda seconds: None)
    assert client.wait_command(1, timeout=5) == "unknown"  # 404

    arr.routes["/api/v3/command/2"] = {"status": "started"}
    assert client.wait_command(2, timeout=0) == "timeout"


def test_queue_membership(arr):
    sonarr = sonarr_client.SonarrClient(arr.url, API_KEY, timeout=5, max_retries=1)
    radarr = radarr_client.RadarrClient(arr.url, API_KEY, timeout=5, max_retries=1)
    arr.routes["/api/v3/queue"] = {"records": [{"episodeId": 5, "movieId": 8}]}

    assert sonarr.is_episode_in_queue(5) is True and sonarr.is_episode_in_queue(6) is False
    assert radarr.is_movie_in_queue(8) is True and radarr.is_movie_in_queue(9) is False


def test_wanted_missing_and_queue_send_each_servers_own_query_params(arr):
    arr.routes["/api/v3/wanted/missing"] = {"records": []}
    arr.routes["/api/v3/queue"] = {"records": []}
    sonarr = sonarr_client.SonarrClient(arr.url, API_KEY, timeout=5, max_retries=1)
    radarr = radarr_client.RadarrClient(arr.url, API_KEY, timeout=5, max_retries=1)

    sonarr.wanted_missing(page=2, page_size=5)
    sonarr.queue()
    radarr.wanted_missing()
    radarr.queue()

    assert [c[2] for c in arr.calls if c[1].endswith("missing")] == [
        {"includeSeries": ["True"], "pageSize": ["5"], "page": ["2"]},
        {"pageSize": ["20"], "page": ["1"]},
    ]
    assert [c[2] for c in arr.calls if c[1].endswith("queue")] == [
        {"includeUnknownSeriesItems": ["False"], "includeSeries": ["False"], "includeEpisode": ["False"]},
        {"includeUnknownMovieItems": ["False"], "includeMovie": ["False"]},
    ]


# --- ArrProcessorService ------------------------------------------------------------------------------------------------------
class FakeClient:
    def __init__(self, tags=None, missing=None, error=None):
        self._tags, self._missing, self._error = tags or {}, missing or [], error

    def get_tags_map(self):
        return self._tags

    def get_all_missing(self):
        if self._error:
            raise self._error
        return self._missing


def _episode(series_id, title, season, number, tags=(), episode_id=None, series_title=None):
    return {
        "id": episode_id or series_id * 100 + season * 10 + number,
        "title": title,
        "seasonNumber": season,
        "episodeNumber": number,
        "series": {"id": series_id, "title": series_title or f"Series {series_id}", "path": f"/tv/{series_id}", "tags": list(tags), "year": 2020, "tmdbId": series_id + 1000},
    }


def _processor(sonarr_missing=(), radarr_missing=(), sonarr_tags=None, radarr_tags=None, **kwargs):
    return ArrProcessorService(
        FakeClient(sonarr_tags, list(sonarr_missing)),
        FakeClient(radarr_tags, list(radarr_missing)),
        **kwargs,
    )


def test_series_are_grouped_by_season_and_sorted():
    processor = _processor([_episode(1, "b", 2, 2), _episode(1, "a", 1, 3), _episode(1, "c", 1, 1), _episode(2, "z", 1, 1)])
    items = processor.get_missing_items()

    show = next(i for i in items if i["id"] == 1)
    assert show["content_type"] == "serie" and show["tmdbId"] == 1001 and show["monitored"] is True
    assert [s["number"] for s in show["seasons"]] == [1, 2]
    assert [e["episodeNumber"] for e in show["seasons"][0]["episodes"]] == [1, 3]
    assert len(items) == 2


def test_season_zero_specials_are_always_skipped():
    items = _processor([_episode(1, "special", 0, 1), _episode(1, "real", 1, 1)]).get_missing_items()

    assert [s["number"] for s in items[0]["seasons"]] == [1]


@pytest.mark.parametrize("hold_tag", ["hold", "pausa"])
def test_hold_tags_pause_a_series_and_a_movie(hold_tag):
    processor = _processor(
        [_episode(1, "e", 1, 1, tags=[5])],
        [{"id": 9, "title": "Movie", "path": "/m", "tags": [5]}],
        sonarr_tags={5: hold_tag},
        radarr_tags={5: hold_tag},
    )

    assert processor.get_missing_items() == []


def test_skip_season_tag_only_skips_that_season():
    processor = _processor([_episode(1, "a", 1, 1, tags=[7]), _episode(1, "b", 2, 1, tags=[7])], sonarr_tags={7: "skip-s1"})
    items = processor.get_missing_items()

    assert [s["number"] for s in items[0]["seasons"]] == [2]


def test_blacklist_mode_drops_items_with_an_active_tag():
    processor = _processor(
        [_episode(1, "a", 1, 1, tags=[1]), _episode(2, "b", 1, 1, tags=[2])],
        tags_mode="blacklist",
        active_tag_ids=[1],
    )

    assert [i["id"] for i in processor.get_missing_items()] == [2]


def test_whitelist_mode_keeps_only_items_with_an_active_tag():
    processor = _processor(
        [_episode(1, "a", 1, 1, tags=[1]), _episode(2, "b", 1, 1, tags=[2])],
        radarr_missing=[{"id": 3, "title": "M1", "path": "/m1", "tags": [1]}, {"id": 4, "title": "M2", "path": "/m2", "tags": []}],
        tags_mode="WHITELIST",
        active_tag_ids=[1],
    )

    assert sorted((i["content_type"], i["id"]) for i in processor.get_missing_items()) == [("movie", 3), ("serie", 1)]


def test_provider_is_taken_from_the_provider_tag():
    processor = _processor(
        [_episode(1, "a", 1, 1, tags=[3, 4])],
        [{"id": 9, "title": "Movie", "path": "/m", "tags": [6], "year": 2001, "tmdbId": 55}],
        sonarr_tags={3: "other", 4: "provider-animeunity"},
        radarr_tags={6: "provider-raiplay "},
    )
    items = {i["content_type"]: i for i in processor.get_missing_items()}

    assert items["serie"]["provider"] == "animeunity"
    assert items["movie"]["provider"] == "raiplay" and items["movie"]["tmdbId"] == 55 and items["movie"]["year"] == 2001


def test_a_failing_client_does_not_hide_the_other_one():
    processor = ArrProcessorService(FakeClient(error=RuntimeError("sonarr down")), FakeClient(missing=[{"id": 3, "title": "M", "path": "/m", "tags": []}]))

    items = processor.get_missing_items()

    assert [(i["content_type"], i["id"]) for i in items] == [("movie", 3)]


def test_missing_clients_are_skipped():
    assert ArrProcessorService(None, None).get_missing_items() == []
