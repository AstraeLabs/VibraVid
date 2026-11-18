# 12.10.26
# ruff: noqa: E402

"""``/api/remote/*``: auth, allowlists, session handling, license/keys/refresh proxying, and the real client over HTTP."""

import base64
import json
import os
import sys
import threading
from pathlib import Path
from wsgiref.simple_server import WSGIRequestHandler, make_server

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
from django.core.wsgi import get_wsgi_application
from django.test import Client

from searchapp import api_remote
from searchapp.api.base import Entries, Episode, Season
from VibraVid.core.downloader import HLS_Downloader
from VibraVid.core.ui.tracker import context_tracker
from VibraVid.remote import cli as remote_cli
from VibraVid.remote.client import RemoteClient, make_license_request_fn, make_manifest_refresh_fn
from VibraVid.remote.config import RemoteServer, RemoteUser, ServeSettings
from VibraVid.remote.models import RemoteError

ALICE = "key-alice"
BOB = "key-bob"
HEADER = "X-VibraVid-Key"
PSSH_ITEM = {"pssh": "AAAA", "kid": "11" * 16, "type": "Widevine", "key_uri": "skd://k"}

SETTINGS = ServeSettings(
    sites=frozenset({"appletv", "disney"}),
    users={
        ALICE: RemoteUser("alice", frozenset({"appletv"}), server_cdm=True),
        BOB: RemoteUser("bob"),
    },
)


class FakeApi:
    """GUI adapter stand-in: search/seasons answer from memory; start_download runs a real (captured) downloader."""

    def __init__(self):
        self.license_calls = []

    def search(self, query):
        return [
            Entries(id="1", name=f"{query} Film", type="movie", year=2020, url="https://s/1"),
            Entries(id="2", name=f"{query} Show", type="tv", year=2021, url="https://s/2"),
        ]

    def get_series_metadata(self, media_item):
        return [
            Season(number=1, episodes=[Episode(number=1, name="Pilot", id=1), Episode(number=2, name="Two", id=2)]),
            Season(number=2, episodes=[Episode(number=1, name="Back", id=3)]),
        ]

    def start_download(self, media_item, season=None, episodes=None):
        self.options = dict(context_tracker.site_options)
        context_tracker.episode_name = "Pilot"

        def license_fn(challenge, item):
            self.license_calls.append((challenge, item))
            return b"LICENSE:" + challenge

        HLS_Downloader(
            m3u8_url="https://cdn.test/master.m3u8",
            m3u8_content="#EXTM3U\n",
            headers={"User-Agent": "ua"},
            license_url="https://lic.test/",
            license_request_fn=license_fn,
            manifest_refresh_fn=lambda: "#EXTM3U\n#REFRESHED",
            output_path="never.mkv",
        ).start()
        return True


@pytest.fixture(scope="module", autouse=True)
def _migrated():
    call_command("migrate", verbosity=0)


@pytest.fixture
def api(monkeypatch):
    fake = FakeApi()
    monkeypatch.setattr(api_remote, "get_api", lambda site: fake)
    monkeypatch.setattr(api_remote, "_fallback_sites", lambda: [])
    monkeypatch.setattr(api_remote, "get_available_sites", lambda: ["appletv", "disney", "pornhub"])
    monkeypatch.setattr(api_remote, "load_serve_settings", lambda: SETTINGS)
    monkeypatch.setattr(api_remote, "_persisted_site_options", lambda site: {"persisted": "yes"})
    monkeypatch.setattr(api_remote, "_store", None)
    monkeypatch.delenv(api_remote.NO_KEY_ENV, raising=False)
    return fake


@pytest.fixture
def http(api):
    return Client(HTTP_HOST="localhost")


def _post(client, url, body, key=ALICE, **extra):
    headers = {HEADER: key} if key else {}
    return client.post(url, data=json.dumps(body), content_type="application/json", headers=headers, **extra)


def _resolve(client, key=ALICE, **extra):
    body = {"site": "appletv", "payload": {"name": "Bunny", "id": "2", "type": "tv"}, "season": "1", "episodes": "1", "site_options": {"drm": "playready"}}
    return _post(client, "/api/remote/resolve/", body, key, **extra)


# ── auth ──────────────────────────────────────────────────────────────────────

def test_health_needs_no_key(http):
    assert http.get("/api/remote/health/").json() == {"status": "ok"}


def test_every_other_endpoint_needs_a_valid_key(http):
    for key in (None, "wrong", ALICE + "x"):
        headers = {HEADER: key} if key else {}
        assert http.get("/api/remote/sites/", headers=headers).status_code == 401
        assert _post(http, "/api/remote/search/", {}, key).status_code == 401
        assert _post(http, "/api/remote/resolve/", {}, key).status_code == 401
        assert _post(http, "/api/remote/session/x/license/", {}, key).status_code == 401
        assert http.delete("/api/remote/session/x/", headers=headers).status_code == 401


def test_fails_closed_when_the_server_has_no_keys(http, monkeypatch):
    monkeypatch.setattr(api_remote, "load_serve_settings", lambda: ServeSettings())

    assert http.get("/api/remote/sites/").status_code == 503
    assert http.get("/api/remote/sites/", headers={HEADER: "anything"}).status_code == 503


def test_no_key_mode_serves_an_unprivileged_anonymous_user(http, monkeypatch):
    monkeypatch.setattr(api_remote, "load_serve_settings", lambda: ServeSettings())
    monkeypatch.setenv(api_remote.NO_KEY_ENV, "1")

    reply = http.get("/api/remote/sites/").json()

    assert reply["server_cdm"] is False
    assert "appletv" in reply["sites"]


def test_wrong_http_method_is_rejected(http):
    assert http.get("/api/remote/resolve/", headers={HEADER: ALICE}).status_code == 405


# ── sites / search / seasons ──────────────────────────────────────────────────

def test_sites_are_filtered_per_key_and_report_the_server_cdm_grant(http):
    alice = http.get("/api/remote/sites/", headers={HEADER: ALICE}).json()
    bob = http.get("/api/remote/sites/", headers={HEADER: BOB}).json()

    assert alice == {"sites": ["appletv"], "server_cdm": True}
    assert bob == {"sites": ["appletv", "disney"], "server_cdm": False}  # pornhub is above the global ceiling


def test_search_returns_payloads_and_enforces_the_allowlist(http):
    ok = _post(http, "/api/remote/search/", {"site": "appletv", "query": "Bunny"}).json()

    assert [r["title"] for r in ok["results"]] == ["Bunny Film", "Bunny Show"]
    assert ok["results"][0]["is_movie"] is True
    assert ok["results"][1]["payload"]["id"] == "2"
    assert _post(http, "/api/remote/search/", {"site": "disney", "query": "x"}).status_code == 403
    assert _post(http, "/api/remote/search/", {"site": "nope", "query": "x"}).status_code == 404
    assert _post(http, "/api/remote/search/", {"site": "appletv"}).status_code == 400


def test_seasons(http):
    reply = _post(http, "/api/remote/seasons/", {"site": "appletv", "payload": {"id": "2", "name": "Show", "type": "tv"}}).json()

    assert [s["number"] for s in reply["seasons"]] == [1, 2]
    assert reply["seasons"][0]["episodes"] == [{"number": 1, "name": "Pilot"}, {"number": 2, "name": "Two"}]
    assert _post(http, "/api/remote/seasons/", {"site": "appletv"}).status_code == 400
    assert _post(http, "/api/remote/seasons/", {"site": "appletv", "payload": {"id": "2"}}).status_code == 400


# ── resolve ───────────────────────────────────────────────────────────────────

def test_resolve_returns_serializable_items_and_hides_callables(http, api):
    reply = _resolve(http).json()

    (item,) = reply["items"]
    assert item["kind"] == "hls"
    assert item["url"] == "https://cdn.test/master.m3u8"
    assert item["license_proxy"] is True and item["manifest_refresh"] is True
    assert item["title"] == "Bunny"
    assert reply["session_id"]
    assert "license_fn" not in json.dumps(reply)
    assert api.options == {"persisted": "yes", "drm": "playready"}  # server defaults, overridden by the client's


def test_resolve_validates_input(http):
    assert _post(http, "/api/remote/resolve/", {"site": "appletv"}).status_code == 400
    assert _post(http, "/api/remote/resolve/", {"site": "appletv", "payload": {"name": "no type"}}).status_code == 400
    assert _post(http, "/api/remote/resolve/", {"site": "appletv", "payload": {"name": "x", "type": "tv"}}).status_code == 400  # series without season
    assert _post(http, "/api/remote/resolve/", {"site": "disney", "payload": {"name": "x", "type": "movie"}}).status_code == 403


# ── license ───────────────────────────────────────────────────────────────────

def _license(client, sid, challenge=b"challenge", key=ALICE, **extra):
    return _post(
        client,
        f"/api/remote/session/{sid}/license/",
        {"item": 0, "drm": "WV", "challenge": base64.b64encode(challenge).decode(), "pssh_item": PSSH_ITEM},
        key,
        **extra,
    )


def test_license_goes_through_the_services_own_callable(http, api):
    sid = _resolve(http).json()["session_id"]

    reply = _license(http, sid, b"abc").json()

    assert base64.b64decode(reply["license"]) == b"LICENSE:abc"
    assert api.license_calls == [(b"abc", PSSH_ITEM)]


def test_license_falls_back_to_a_plain_relay_without_a_service_callable(http, api, monkeypatch):
    sid = _resolve(http).json()["session_id"]
    monkeypatch.setattr(api_remote, "call_license_hook", lambda hooks, challenge, item, identity=None: None)
    seen = {}

    def fake_relay(media, drm, challenge):
        seen["args"] = (media.license_url, drm, challenge)
        return b"relayed"

    monkeypatch.setattr(api_remote, "relay_license", fake_relay)

    reply = _license(http, sid, b"xyz").json()

    assert base64.b64decode(reply["license"]) == b"relayed"
    assert seen["args"] == ("https://lic.test/", "WV", b"xyz")


def test_text_licenses_are_returned_as_text(http, monkeypatch):
    sid = _resolve(http).json()["session_id"]
    monkeypatch.setattr(api_remote, "call_license_hook", lambda hooks, challenge, item, identity=None: "<License/>")

    assert _license(http, sid).json() == {"license_text": "<License/>"}


def test_license_input_errors(http):
    sid = _resolve(http).json()["session_id"]
    body = {"item": 0, "drm": "WV", "challenge": "!!notbase64!!"}

    assert _post(http, f"/api/remote/session/{sid}/license/", body).status_code == 400
    assert _post(http, f"/api/remote/session/{sid}/license/", {"item": 0, "challenge": ""}).status_code == 400
    assert _post(http, f"/api/remote/session/{sid}/license/", {"item": 7, "challenge": "YQ=="}).status_code == 400
    assert _post(http, "/api/remote/session/unknown/license/", {"item": 0, "challenge": "YQ=="}).status_code == 404


def test_sessions_are_private_to_their_key_and_address(http):
    sid = _resolve(http, REMOTE_ADDR="10.0.0.1").json()["session_id"]

    assert _license(http, sid, REMOTE_ADDR="10.0.0.1").status_code == 200
    assert _license(http, sid, REMOTE_ADDR="10.0.0.2").status_code == 403  # another address
    assert _license(http, sid, key=BOB, REMOTE_ADDR="10.0.0.1").status_code == 403  # another key
    assert http.delete(f"/api/remote/session/{sid}/", headers={HEADER: BOB}, REMOTE_ADDR="10.0.0.1").status_code == 403


# ── keys / refresh / close ────────────────────────────────────────────────────

def test_server_cdm_keys_need_the_grant(http, monkeypatch):
    sid_alice = _resolve(http).json()["session_id"]
    sid_bob = _resolve(http, key=BOB).json()["session_id"]
    monkeypatch.setattr(api_remote, "fetch_server_keys", lambda item, site, hooks, identity=None: ["11" * 16 + ":" + "22" * 16])

    assert _post(http, f"/api/remote/session/{sid_alice}/keys/", {"item": 0}).json() == {"keys": ["11" * 16 + ":" + "22" * 16]}
    assert _post(http, f"/api/remote/session/{sid_bob}/keys/", {"item": 0}, key=BOB).status_code == 403


def test_keys_run_with_the_sessions_hooks(http, monkeypatch):
    sid = _resolve(http).json()["session_id"]
    seen = {}

    def fake_fetch(item, site, hooks, identity=None):
        seen.update(site=site, hooks=sorted(hooks), url=item.url)
        return []

    monkeypatch.setattr(api_remote, "fetch_server_keys", fake_fetch)

    _post(http, f"/api/remote/session/{sid}/keys/", {"item": 0})

    assert seen == {"site": "appletv", "hooks": ["license_request_fn", "manifest_refresh_fn"], "url": "https://cdn.test/master.m3u8"}


def test_manifest_refresh(http):
    sid = _resolve(http).json()["session_id"]

    reply = _post(http, f"/api/remote/session/{sid}/refresh/", {"item": 0}).json()

    assert reply["manifest"] == {"z": False, "d": "#EXTM3U\n#REFRESHED"}


def test_close_ends_the_session(http):
    sid = _resolve(http).json()["session_id"]

    assert http.delete(f"/api/remote/session/{sid}/", headers={HEADER: ALICE}).json() == {"closed": True}
    assert http.delete(f"/api/remote/session/{sid}/", headers={HEADER: ALICE}).json() == {"closed": False}
    assert _license(http, sid).status_code == 404


# ── the real client over real HTTP ────────────────────────────────────────────

class _Quiet(WSGIRequestHandler):
    def log_message(self, *args):
        pass


@pytest.fixture
def live(api):
    server = make_server("127.0.0.1", 0, get_wsgi_application(), handler_class=_Quiet)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()
    server.server_close()


def _client(url, key=ALICE, server_cdm=False):
    return RemoteClient(RemoteServer("test", url, key, server_cdm), timeout=30)


def test_client_end_to_end_over_http(live):
    client = _client(live)

    assert client.health() is True
    assert client.sites() == {"sites": ["appletv"], "server_cdm": True}
    results = client.search("appletv", "Bunny")
    assert results[1]["title"] == "Bunny Show"
    assert [s["number"] for s in client.seasons("appletv", results[1]["payload"])] == [1, 2]

    sid, items = client.resolve("appletv", results[1]["payload"], "1", "1", {"drm": "widevine"})
    assert items[0].license_proxy and items[0].manifest_refresh

    license_fn = make_license_request_fn(client, sid, 0)
    assert license_fn(b"challenge-bytes", PSSH_ITEM) == b"LICENSE:challenge-bytes"
    assert make_manifest_refresh_fn(client, sid, 0)() == "#EXTM3U\n#REFRESHED"

    client.close(sid)
    with pytest.raises(RemoteError) as gone:
        client.license(sid, 0, "WV", b"x")
    assert gone.value.status == 404


def test_client_surfaces_server_errors_with_their_message(live):
    with pytest.raises(RemoteError) as wrong_key:
        _client(live, key="nope").sites()
    assert wrong_key.value.status == 401
    assert "API key" in str(wrong_key.value)

    with pytest.raises(RemoteError) as denied:
        _client(live).search("disney", "x")
    assert denied.value.status == 403

    with pytest.raises(RemoteError, match="cannot reach"):
        _client("http://127.0.0.1:9").health()


def test_client_license_function_picks_playready_for_playready_items(live, monkeypatch):
    client = _client(live)
    seen = []
    monkeypatch.setattr(client, "license", lambda sid, index, drm, challenge, item=None: seen.append(drm) or b"x")

    fn = make_license_request_fn(client, "sid", 0)
    fn(b"c", {"type": "Widevine"})
    fn(b"c", {"type": "PlayReady"})
    fn(b"c", {})

    assert seen == ["WV", "PR", "WV"]
    seen.clear()
    make_license_request_fn(client, "sid", 0, default_drm="PR")(b"c", {})
    assert seen == ["PR"]


def test_download_resolved_wires_the_proxy_hooks_into_the_downloader(live, monkeypatch, tmp_path):
    """Proxy mode: the local downloader must receive license/refresh hooks that reach the server."""
    client = _client(live)
    sid, (media,) = client.resolve("appletv", {"name": "Bunny", "id": "2", "type": "tv"}, "1", "1")
    captured = {}

    class StubDownloader:
        def start(self):
            captured["license"] = captured["kwargs"]["license_request_fn"](b"zz", PSSH_ITEM)
            captured["refresh"] = captured["kwargs"]["manifest_refresh_fn"]()
            return (str(tmp_path / "ok.mkv"), False, None)

    def fake_build(media_arg, path, **kwargs):
        captured.update(media=media_arg, path=path, kwargs=kwargs)
        return StubDownloader()

    monkeypatch.setattr(remote_cli, "build_downloader", fake_build)
    monkeypatch.setattr(remote_cli, "output_path_for", lambda m, s: str(tmp_path / "ok.mkv"))

    assert remote_cli.download_resolved(client, False, sid, 0, media, "appletv") is True

    assert captured["kwargs"]["key"] is None
    assert captured["license"] == b"LICENSE:zz"
    assert captured["refresh"] == "#EXTM3U\n#REFRESHED"


def test_download_resolved_uses_server_cdm_keys_when_enabled(live, monkeypatch, tmp_path):
    monkeypatch.setattr(api_remote, "fetch_server_keys", lambda item, site, hooks, identity=None: ["aa" * 16 + ":" + "bb" * 16])
    client = _client(live)
    sid, (media,) = client.resolve("appletv", {"name": "Bunny", "id": "2", "type": "tv"}, "1", "1")
    captured = {}

    class StubDownloader:
        def start(self):
            return (str(tmp_path / "ok.mkv"), False, None)

    def fake_build(media_arg, path, **kwargs):
        captured.update(kwargs)
        return StubDownloader()

    monkeypatch.setattr(remote_cli, "build_downloader", fake_build)
    monkeypatch.setattr(remote_cli, "output_path_for", lambda m, s: str(tmp_path / "ok.mkv"))

    assert remote_cli.download_resolved(client, True, sid, 0, media, "appletv") is True

    assert captured["key"] == ["aa" * 16 + ":" + "bb" * 16]
    assert captured["license_request_fn"] is None  # keys came from the server CDM: no license traffic from here


# ── the --server CLI flow ─────────────────────────────────────────────────────

class _Args:
    def __init__(self, **kw):
        self.server = "test"
        self.site = "appletv"
        self.search = "Bunny"
        self.item = 1  # "Bunny Show"
        self.season = "1"
        self.episode = "*"
        self.server_opt = ["drm=playready", "atmos=true"]
        self.__dict__.update(kw)


@pytest.fixture
def cli_env(live, monkeypatch):
    calls = {"downloads": [], "resolves": []}
    server = RemoteServer("test", live, ALICE, server_cdm=False)
    monkeypatch.setattr(remote_cli, "pick_remote_server", lambda name: server)

    real_resolve = RemoteClient.resolve

    def spy_resolve(self, site, payload, season=None, episodes=None, site_options=None):
        calls["resolves"].append((site, season, episodes, site_options))
        return real_resolve(self, site, payload, season, episodes, site_options)

    monkeypatch.setattr(RemoteClient, "resolve", spy_resolve)
    monkeypatch.setattr(
        remote_cli, "download_resolved",
        lambda client, use_server_cdm, sid, index, media, site: calls["downloads"].append((use_server_cdm, media.url, site)) or True,
    )
    return calls


def test_cli_expands_a_season_into_one_resolve_per_episode(cli_env):
    assert remote_cli.run_remote(_Args()) is True

    assert [(s, e) for _, s, e, _ in cli_env["resolves"]] == [("1", "1"), ("1", "2")]  # season 1 has two episodes
    assert all(opts == {"drm": "playready", "atmos": True} for *_, opts in cli_env["resolves"])
    assert len(cli_env["downloads"]) == 2
    assert all(site == "appletv" for _, _, site in cli_env["downloads"])


def test_cli_all_seasons_means_every_episode_as_in_a_local_run(cli_env):
    assert remote_cli.run_remote(_Args(season="*", episode="1")) is True

    assert [(s, e) for _, s, e, _ in cli_env["resolves"]] == [("1", "1"), ("1", "2"), ("2", "1")]


def test_cli_movie_needs_no_season(cli_env):
    assert remote_cli.run_remote(_Args(item=0, season=None, episode=None)) is True

    assert [(s, e) for _, s, e, _ in cli_env["resolves"]] == [(None, None)]


def test_cli_reports_failures_in_the_return_value(cli_env, monkeypatch):
    monkeypatch.setattr(remote_cli, "download_resolved", lambda *a, **k: False)

    assert remote_cli.run_remote(_Args(episode="1")) is False


def test_cli_errors_are_handled_not_raised(cli_env, capsys):
    assert remote_cli.handle_remote_download(_Args(site="disney")) == (True, False)  # not allowed for this key
    assert remote_cli.handle_remote_download(_Args(site=None)) == (True, False)
    assert remote_cli.handle_remote_download(_Args(search="x", item=9)) == (True, False)
    assert remote_cli.handle_remote_download(_Args(server_opt=["broken"])) == (True, False)
    assert remote_cli.handle_remote_download(_Args(server=None)) == (False, False)  # no --server: not ours


def test_cli_uses_the_server_cdm_only_when_both_sides_agree(live, monkeypatch, cli_env):
    monkeypatch.setattr(remote_cli, "pick_remote_server", lambda name: RemoteServer("test", live, ALICE, server_cdm=True))

    remote_cli.run_remote(_Args(item=0, season=None, episode=None))  # Alice has the grant
    assert cli_env["downloads"][-1][0] is True

    monkeypatch.setattr(remote_cli, "pick_remote_server", lambda name: RemoteServer("test", live, BOB, server_cdm=True))
    monkeypatch.setattr(api_remote, "load_serve_settings", lambda: ServeSettings(users={BOB: RemoteUser("bob")}))
    api_remote._store = None
    remote_cli.run_remote(_Args(item=0, season=None, episode=None, site="appletv"))  # Bob has none: falls back to the local CDM
    assert cli_env["downloads"][-1][0] is False
