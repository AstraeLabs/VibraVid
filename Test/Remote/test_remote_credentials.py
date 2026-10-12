# 12.10.26
# ruff: noqa: E402

"""The client's own credentials: forwarded per request, never mixed with the server's accounts or another client's."""

import argparse
import json
import os
import sys
import threading
import shutil
import types
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

from searchapp import api_remote
from searchapp.api.base import Entries, Episode, Season
from VibraVid.core.downloader import HLS_Downloader
from VibraVid.core.drm.manager import DRMManager
from VibraVid.core.ui.tracker import context_tracker
from VibraVid.remote import cli as remote_cli
from VibraVid.remote import isolation
from VibraVid.remote import server as server_module
from VibraVid.remote.client import RemoteClient
from VibraVid.remote.config import RemoteServer, RemoteUser, ServeSettings
from VibraVid.remote.credentials import LocalCache, apply_updates, build_identity, collect_credentials
from VibraVid.remote.server import SessionStore, _in_thread
from VibraVid.remote.models import RemoteError
from VibraVid.services._base import login_status
from VibraVid.services._base.login_status import Login
from VibraVid.utils import config_manager, disk_cache
from VibraVid.utils.scope import CACHE_PATH_PREFIX, current_scope, scoped

KEY = "cred-key"
SERVER_LOGIN = {
    "appletv": {"token": "SERVER-OWN-TOKEN", "cdm": "server-device"},
    "cs": {"netflixid": "SERVER-NETFLIX"},
    "Provider": {"tmdb": "SERVER-TMDB"},
}


def client_login():
    return {
        "appletv": {"token": "CLIENT-TOKEN", "cdm": "my-device"},
        "cs": {"netflixid": "CLIENT-NETFLIX"},
        "other": {"x": "never-needed"},
    }


# ── credentials helpers (no Django) ───────────────────────────────────────────

def test_collect_sends_only_the_services_sections_and_drops_local_only_keys():
    login = {"appletv": {"token": "t", "cdm": "dev"}, "appletvuk": {"token": "u"}, "disney": {"token": "d"}, "empty": {}}

    assert collect_credentials("appletv", login) == {"appletv": {"token": "t"}, "appletvuk": {"token": "u"}}


def test_collect_adds_the_extra_sections_a_service_reads():
    login = {"csi": {"cdm": "dev"}, "cs": {"netflixid": "n"}, "disney": {"token": "d"}}

    assert collect_credentials("csi", login) == {}
    assert collect_credentials("csi", login, extra={"cs"}) == {"cs": {"netflixid": "n"}}


def test_collect_flattens_a_block_pasted_with_an_extra_nesting_level():
    login = {"mediasetinfinity": {"mediasetinfinity": {"rtilogin_acd": {"caToken": "c"}}, "cdm": "dev"}}

    assert collect_credentials("mediasetinfinity", login) == {"mediasetinfinity": {"rtilogin_acd": {"caToken": "c"}}}


def test_apply_updates_merges_flat_and_nested_sections():
    flat = {"appletv": {"token": "old", "other": 1}}
    assert apply_updates(flat, {"appletv": {"token": "new"}}) is True
    assert flat == {"appletv": {"token": "new", "other": 1}}
    assert apply_updates(flat, {"appletv": {"token": "new"}}) is False  # nothing to change

    nested = {"svc": {"svc": {"token": "old"}, "cdm": "dev"}}
    assert apply_updates(nested, {"svc": {"token": "new"}}) is True
    assert nested == {"svc": {"svc": {"token": "new"}, "cdm": "dev"}}

    fresh = {}
    assert apply_updates(fresh, {"svc": {"token": "t"}}) is True
    assert fresh == {"svc": {"token": "t"}}
    assert apply_updates({}, {"svc": {}}) is False


def test_identity_ignores_the_servers_own_accounts_but_keeps_its_cdm():
    identity = build_identity("appletv", {"appletv": {"token": "CLIENT-TOKEN"}}, SERVER_LOGIN)

    assert identity.overlay == {"appletv": {"token": "CLIENT-TOKEN", "cdm": "server-device"}}
    assert "SERVER-OWN-TOKEN" not in json.dumps(identity.overlay)


def test_identity_without_credentials_is_anonymous():
    identity = build_identity("appletv", None, SERVER_LOGIN)

    assert identity.overlay == {"appletv": {"cdm": "server-device"}}
    assert identity.scope == "anonymous"


def test_scope_depends_on_the_credentials_only():
    a1 = build_identity("appletv", {"appletv": {"token": "a"}}, SERVER_LOGIN)
    a2 = build_identity("appletv", {"appletv": {"token": "a"}}, {})
    b = build_identity("appletv", {"appletv": {"token": "b"}}, SERVER_LOGIN)

    assert a1.scope == a2.scope != b.scope
    assert len(a1.scope) == 20


@pytest.mark.parametrize("bad", ["text", ["a"], {"appletv": "not-an-object"}, {"appletv": ["x"]}])
def test_identity_rejects_malformed_credentials(bad):
    with pytest.raises(RemoteError) as error:
        build_identity("appletv", bad, SERVER_LOGIN)

    assert error.value.status == 400


def test_identity_rejects_oversized_credentials():
    with pytest.raises(RemoteError) as error:
        build_identity("appletv", {"appletv": {"blob": "x" * (300 * 1024)}}, SERVER_LOGIN)

    assert error.value.status == 413


def test_identity_collects_wanted_and_changed_sections():
    identity = build_identity("appletv", {"appletv": {"token": "t"}}, SERVER_LOGIN)

    with identity.active():
        assert config_manager.login.get("cs", "netflixid", default=None) is None  # not sent: denied
        config_manager.login.set_key("appletv", "token", "rotated")

    assert identity.take_wanted() == ["cs"]
    assert identity.take_wanted() == []
    assert identity.take_updates() == {"appletv": {"token": "rotated"}}  # the server's cdm key is not handed out
    assert identity.take_updates() == {}


# ── the login overlay ─────────────────────────────────────────────────────────

def test_overlay_replaces_the_stored_login_for_this_thread_only(monkeypatch):
    monkeypatch.setitem(config_manager.login._config_dict, "zz_probe", {"token": "STORED"})
    identity = build_identity("zz_probe", {"zz_probe": {"token": "FORWARDED"}}, {})

    with identity.active():
        assert config_manager.login.get("zz_probe", "token") == "FORWARDED"
        assert config_manager.login.get_section("zz_probe") == {"token": "FORWARDED"}
        assert config_manager.login.has_section("zz_probe") is True
        seen_elsewhere = []
        worker = threading.Thread(target=lambda: seen_elsewhere.append(config_manager.login.get("zz_probe", "token")))
        worker.start()
        worker.join()
    assert seen_elsewhere == ["STORED"]  # another thread is not affected
    assert config_manager.login.get("zz_probe", "token") == "STORED"  # nor is anything after the request


def test_overlay_does_not_leak_through_the_shared_cache(monkeypatch):
    monkeypatch.setitem(config_manager.login._config_dict, "zz_probe", {"token": "STORED"})
    assert config_manager.login.get("zz_probe", "token") == "STORED"  # primes the cache

    with scoped({"zz_probe": {"token": "FORWARDED"}}, "s1"):
        assert config_manager.login.get("zz_probe", "token") == "FORWARDED"
    with scoped({"zz_probe": {}}, "s2"):
        assert config_manager.login.get("zz_probe", "token", default="none") == "none"
    assert config_manager.login.get("zz_probe", "token") == "STORED"


def test_overlay_keeps_the_servers_provider_keys(monkeypatch):
    monkeypatch.setitem(config_manager.login._config_dict, "Provider", {"tmdb": "SERVER-TMDB"})

    with scoped({}, "s"):
        assert config_manager.login.get("Provider", "tmdb") == "SERVER-TMDB"


def test_writes_stay_in_the_overlay_and_never_reach_the_servers_file(monkeypatch, tmp_path):
    login_file = tmp_path / "login.json"
    monkeypatch.setattr(config_manager, "login_file_path", str(login_file))
    monkeypatch.setitem(config_manager.login._config_dict, "zz_probe", {"token": "STORED"})
    overlay = {"zz_probe": {"token": "FORWARDED"}}

    with scoped(overlay, "s"):
        config_manager.login.set_key("zz_probe", "token", "ROTATED")
        config_manager.save_login()

    assert overlay["zz_probe"]["token"] == "ROTATED"
    assert config_manager.login._config_dict["zz_probe"]["token"] == "STORED"
    assert not login_file.exists()




# ── module state of a service ─────────────────────────────────────────────────

class _Client:
    pass


_Client.__module__ = "fakesvc.client"


@pytest.fixture
def fake_service(monkeypatch):
    package = types.ModuleType("fakesvc")
    module = types.ModuleType("fakesvc.client")
    module._client = _Client()
    module._client_by_region = {"it": object()}
    module._api_headers = {"User-Agent": "constant"}  # a constant mapping: must survive
    module._API_URL = "https://api.test"
    module._cache_ttl = 60
    module.helper = lambda: "fn"
    module.unrelated_client_name = _Client()  # no leading underscore: public, left alone
    other = types.ModuleType("fakesvc.other")
    other._csi_client = _Client()
    monkeypatch.setitem(sys.modules, "fakesvc", package)
    monkeypatch.setitem(sys.modules, "fakesvc.client", module)
    monkeypatch.setitem(sys.modules, "fakesvc.other", other)
    monkeypatch.setattr(isolation, "_service_module", lambda site: package)
    monkeypatch.setattr(isolation, "_last_scope", {})
    return module, other


def test_identity_bound_module_state_is_dropped_and_constants_survive(fake_service):
    module, other = fake_service

    assert isolation.reset_identity_state("fakesvc") == 3

    assert module._client is None and other._csi_client is None
    assert module._client_by_region == {}
    assert module._api_headers == {"User-Agent": "constant"}
    assert (module._API_URL, module._cache_ttl, module.helper()) == ("https://api.test", 60, "fn")
    assert module.unrelated_client_name is not None


def test_a_service_can_name_its_own_identity_state(fake_service):
    module, _ = fake_service
    module._identity_state = ("_cache_ttl",)

    isolation.reset_identity_state("fakesvc")

    assert module._cache_ttl is None
    assert module._client is not None  # only what the service declared


def test_state_is_reset_only_when_the_identity_changes(fake_service):
    module, _ = fake_service

    isolation.prepare("fakesvc", "alice")
    assert module._client is None
    module._client = _Client()
    isolation.prepare("fakesvc", "alice")
    assert module._client is not None  # same client again: keep its logged-in object
    isolation.prepare("fakesvc", "bob")
    assert module._client is None


# ── over HTTP ─────────────────────────────────────────────────────────────────

class CredApi:
    """A service that reads its login the way real ones do, and reports what it saw."""

    def __init__(self):
        self.seen = []
        self.rotate = False
        self.read_cs = False
        self.queries = []
        self.cache_paths = []
        self.announce_login = False
        self.use_cache = False
        self.read_cache_service = None
        self.on_disk_while_serving = []
        self.cache_seen = []

    def _peek(self, what):
        if self.announce_login:
            Login.refresh_try()
            Login.refresh_ok(1_800_000_000)
            Login.show(Login.ACCOUNT if config_manager.login.get("appletv", "token", default=None) else Login.ANONYMOUS, "user@example.it")
        self.seen.append({
            "what": what,
            "token": config_manager.login.get("appletv", "token", default=None),
            "cs": config_manager.login.get("cs", "netflixid", default=None) if self.read_cs else "unread",
            "options": dict(context_tracker.site_options),
            "scope": current_scope(),
        })

    def search(self, query):
        self.queries.append(query)
        self._peek("search")
        return [
            Entries(id="1", name="A Film", type="movie", year=2024, url="https://s/1"),
            Entries(id="2", name="A Show", type="tv", year=2023, url="https://s/2"),
        ]

    seasons_override = None

    def get_series_metadata(self, media_item):
        self._peek("seasons")
        if self.seasons_override:
            return [
                Season(number=s["number"], name=s["name"], episodes=[Episode(number=e["number"], name=e["name"], id=e["number"]) for e in s["episodes"]])
                for s in self.seasons_override
            ]
        return [Season(number=1, episodes=[Episode(number=1, name="Pilot", id=1)])]

    def start_download(self, media_item, season=None, episodes=None):
        self._peek("resolve")
        self.cache_paths.append(disk_cache.cache_path("appletv", "session"))
        if self.read_cache_service:
            self.cache_seen.append(disk_cache.load(self.read_cache_service, "esn"))
        if self.use_cache:
            self.cache_seen.append(disk_cache.load("appletv", "session"))
            disk_cache.save("appletv", "session", {"token": "FRESH", "expiry": 4102444800})
            disk_cache.save("appletv", "other", {"x": 1})
            disk_cache.invalidate("appletv", "stale")
            real = os.path.join(config_manager.base_path, ".cache", "services", "appletv")
            self.on_disk_while_serving.append(sorted(os.listdir(real)) if os.path.isdir(real) else [])
        if self.rotate:
            config_manager.login.set_key("appletv", "token", "ROTATED-" + str(len(self.seen)))
            config_manager.save_login()

        def license_fn(challenge, item):
            if self.rotate:
                config_manager.login.set_key("appletv", "token", "ROTATED-BY-LICENSE")
            return b"LIC"

        HLS_Downloader(
            m3u8_url="https://cdn.test/master.m3u8",
            m3u8_content="#EXTM3U\n",
            license_url="https://lic.test/",
            license_request_fn=license_fn,
            output_path="never.mkv",
        ).start()
        return True


class _Quiet(WSGIRequestHandler):
    def log_message(self, *args):
        pass


@pytest.fixture
def api(monkeypatch):
    fake = CredApi()
    settings = ServeSettings(users={KEY: RemoteUser("tester")})
    monkeypatch.setattr(api_remote, "get_api", lambda site: fake)
    monkeypatch.setattr(api_remote, "_fallback_sites", lambda: [])
    monkeypatch.setattr(api_remote, "get_available_sites", lambda: ["appletv"])
    monkeypatch.setattr(api_remote, "load_serve_settings", lambda: settings)
    monkeypatch.setattr(api_remote, "_persisted_site_options", lambda site: {"persisted": "yes"})
    monkeypatch.setattr(api_remote, "_server_login", lambda: SERVER_LOGIN)
    monkeypatch.setattr(api_remote, "_store", None)
    monkeypatch.setattr(isolation, "_last_scope", {})
    monkeypatch.setattr(isolation, "reset_identity_state", lambda site: 0)
    monkeypatch.setattr(server_module, "_login_hints", {})  # what earlier tests taught the server must not leak into this one
    monkeypatch.setattr(server_module, "_cache_hints", {})
    monkeypatch.setattr(DRMManager, "for_site", classmethod(lambda cls, site: object()))  # the fake "server-device" does not exist
    monkeypatch.setattr(api_remote, "get_site_extra_args_schema", lambda site: [
        {"flag": "--url", "dest": "url", "kind": "text", "choices": [], "default": None, "help": "Title URL"},
        {"flag": "--atmos", "dest": "atmos", "kind": "flag", "choices": [], "default": False, "help": "Atmos"},
        {"flag": "--store", "dest": "store", "kind": "choice", "choices": ["US", "GB"], "default": None, "help": "Region"},
        {"flag": "--limit", "dest": "limit", "kind": "int", "choices": [], "default": None, "help": "Limit"},
    ])
    call_command("migrate", verbosity=0)
    return fake


@pytest.fixture
def live(api):
    server = make_server("127.0.0.1", 0, get_wsgi_application(), handler_class=_Quiet)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()
    server.server_close()


_DEFAULT_LOGIN = object()


@pytest.fixture
def make(live):
    updates = []

    def build(login=_DEFAULT_LOGIN, **server_kwargs):
        server = RemoteServer("test", live, KEY, **server_kwargs)
        client = RemoteClient(server, timeout=30, login_data=client_login() if login is _DEFAULT_LOGIN else login, on_login_update=updates.append)
        bodies = []
        original = client._call

        def spy(method, path, body=None):
            bodies.append((path, body))
            return original(method, path, body)

        client._call = spy
        client.bodies = bodies
        return client

    build.updates = updates
    return build


PAYLOAD = {"name": "A Show", "type": "tv", "id": "2"}


def test_the_server_acts_with_the_clients_token_not_its_own(make, api):
    client = make()

    client.search("appletv", "x")
    client.seasons("appletv", PAYLOAD)
    client.resolve("appletv", PAYLOAD, "1", "1")

    assert [s["token"] for s in api.seen] == ["CLIENT-TOKEN"] * 3
    assert "SERVER-OWN-TOKEN" not in json.dumps(api.seen)


def test_a_client_without_credentials_is_anonymous_never_the_servers_account(make, api):
    make(login={}).search("appletv", "x")
    make(login=None).search("appletv", "x")

    assert [s["token"] for s in api.seen] == [None, None]


def test_credentials_can_be_switched_off_per_server(make, api):
    make(send_credentials=False).search("appletv", "x")

    assert api.seen[0]["token"] is None


def test_only_the_services_sections_are_sent(make):
    client = make()

    client.search("appletv", "x")

    (path, body), = client.bodies
    assert body["credentials"] == {"appletv": {"token": "CLIENT-TOKEN"}}  # no cdm, no "other"


def test_a_section_the_service_reads_is_reported_and_sent_on_a_retry(make, api):
    api.read_cs = True
    client = make()

    client.search("appletv", "x")

    assert [b["credentials"].keys() for _, b in client.bodies] == [{"appletv"}, {"appletv", "cs"}]
    assert api.seen[0]["cs"] is None  # first try: the server's own "cs" account was NOT used
    assert api.seen[-1]["cs"] == "CLIENT-NETFLIX"
    assert "SERVER-NETFLIX" not in json.dumps(api.seen)

    client.seasons("appletv", PAYLOAD)  # learned: the next call carries it at once
    assert len(client.bodies) == 3
    assert api.seen[-1]["cs"] == "CLIENT-NETFLIX"


def test_the_server_remembers_sections_and_hands_them_to_new_clients(make, api):
    api.read_cs = True
    make().search("appletv", "x")

    newcomer = make()
    info = newcomer.site_info("appletv")

    assert info["login_sections"] == ["cs"]
    newcomer.search("appletv", "x")
    assert len(newcomer.bodies) == 2  # info + one search: no retry needed this time (the info call is the first body-less call)


def test_a_wanted_section_the_client_does_not_have_is_not_retried(make, api):
    api.read_cs = True
    client = make(login={"appletv": {"token": "CLIENT-TOKEN"}})

    client.search("appletv", "x")

    assert len(client.bodies) == 1


def test_rotated_tokens_come_back_to_the_client_and_never_touch_the_servers_login(make, api):
    api.rotate = True
    client = make()

    session_id, _ = client.resolve("appletv", PAYLOAD, "1", "1")
    client.license(session_id, 0, "WV", b"challenge")
    client.close(session_id)

    assert make.updates[0] == {"appletv": {"token": "ROTATED-1"}}
    assert {"appletv": {"token": "ROTATED-BY-LICENSE"}} in make.updates
    assert SERVER_LOGIN["appletv"]["token"] == "SERVER-OWN-TOKEN"
    assert config_manager.login._config_dict.get("appletv", {}).get("token") != "ROTATED-1"




def test_the_servers_scraper_cache_is_keyed_by_identity(api):
    from searchapp.api.base import BaseStreamingAPI

    class Adapter(BaseStreamingAPI):
        def search(self, query):
            return []

        def get_series_metadata(self, media_item):
            return None

        def start_download(self, media_item, season=None, episodes=None):
            return True

    adapter = Adapter()
    adapter.site_name = "appletv"
    item = Entries(id="1", name="x", type="tv", url="https://s/1")

    plain = adapter._get_cache_key(item)
    with scoped({}, "alice"):
        alice = adapter._get_cache_key(item)
    with scoped({}, "bob"):
        bob = adapter._get_cache_key(item)

    assert len({plain, alice, bob}) == 3


def test_service_options_travel_with_search_seasons_and_resolve(make, api):
    client = make()

    client.search("appletv", "x", {"store": "US"})
    client.seasons("appletv", PAYLOAD, {"store": "US"})
    client.resolve("appletv", PAYLOAD, "1", "1", {"store": "US", "atmos": True})

    assert [s["options"].get("store") for s in api.seen] == ["US"] * 3
    assert all(s["options"]["persisted"] == "yes" for s in api.seen)
    assert api.seen[-1]["options"]["atmos"] is True


def test_site_info_lists_the_services_cli_options(make):
    info = make().site_info("appletv")

    assert [o["flag"] for o in info["options"]] == ["--url", "--atmos", "--store", "--limit"]


def test_plain_http_to_a_remote_host_is_refused_with_credentials(make):
    client = RemoteClient(RemoteServer("far", "http://203.0.113.9:8786", KEY), login_data=client_login())

    with pytest.raises(RemoteError, match="refusing to send your credentials over plain http"):
        client.search("appletv", "x")


@pytest.mark.parametrize(
    "server",
    [
        RemoteServer("far", "https://far.example", KEY),
        RemoteServer("far", "http://203.0.113.9:8786", KEY, allow_insecure=True),
        RemoteServer("far", "http://203.0.113.9:8786", KEY, send_credentials=False),
    ],
)
def test_safe_transports_and_explicit_opt_outs_are_allowed(server):
    client = RemoteClient(server, login_data=client_login())

    if server.send_credentials:
        assert client._credentials("appletv") == {"appletv": {"token": "CLIENT-TOKEN"}}
    else:
        assert client._credentials("appletv") == {}


# ── --url and the services own options on the command line ────────────────────

def _parser():
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("-i", dest="site")
    parser.add_argument("-s", "--search", default=None)
    parser.add_argument("--item", type=int, default=None)
    parser.add_argument("-S", "--season", default=None)
    parser.add_argument("-E", "--episode", default=None)
    parser.add_argument("--server", nargs="?", const="", default=None)
    parser.add_argument("--server-opt", dest="server_opt", action="append")
    return parser


@pytest.fixture
def cli_server(live, monkeypatch):
    server = RemoteServer("test", live, KEY)
    monkeypatch.setattr(remote_cli, "pick_remote_server", lambda name: server)
    monkeypatch.setattr(remote_cli, "_login_data", lambda: client_login())
    monkeypatch.setattr(remote_cli, "_store_login_update", lambda updates: None)
    monkeypatch.setattr(context_tracker, "site_options", {}, raising=False)
    return server


def test_the_services_own_options_become_command_line_flags(cli_server):
    parser = _parser()
    argv = ["--server", "test", "-i", "appletv", "--url", "https://tv.apple.com/x", "--atmos", "--store", "US", "--limit", "3"]

    dests = remote_cli.add_remote_site_options(parser, argv, "appletv")
    args = parser.parse_args(argv)

    assert dests == ["url", "atmos", "store", "limit"]
    assert remote_cli._site_options_from_args(args) == {"url": "https://tv.apple.com/x", "atmos": True, "store": "US", "limit": 3}
    with pytest.raises(SystemExit):
        parser.parse_args(["--store", "ZZ"])  # a choice the service does not offer


def test_unset_flags_do_not_override_what_the_server_persists(cli_server):
    parser = _parser()
    remote_cli.add_remote_site_options(parser, ["--server", "-i", "appletv"], "appletv")

    args = parser.parse_args(["--server", "-i", "appletv"])

    assert remote_cli._site_options_from_args(args) == {}


def test_options_already_known_locally_are_not_registered_twice(cli_server):
    parser = _parser()
    parser.add_argument("--url", dest="url", default=None)

    dests = remote_cli.add_remote_site_options(parser, ["--server", "-i", "appletv"], "appletv")

    assert "url" not in dests


def test_no_server_flag_means_no_remote_options(cli_server):
    assert remote_cli.add_remote_site_options(_parser(), ["-i", "appletv"], "appletv") == []
    assert remote_cli.add_remote_site_options(_parser(), ["--server", "test"], None) == []
    assert remote_cli.add_remote_site_options(_parser(), ["--server", "test", "-i", "5"], "5") == []


def test_server_opt_overrides_a_flag(cli_server):
    parser = _parser()
    remote_cli.add_remote_site_options(parser, ["--server", "-i", "appletv"], "appletv")
    args = parser.parse_args(["--server", "-i", "appletv", "--store", "US", "--server-opt", "store=GB", "--server-opt", "x=1"])

    assert remote_cli._site_options_from_args(args) == {"store": "GB", "x": 1}


def test_url_alone_is_enough_to_find_the_title(cli_server, api, monkeypatch):
    monkeypatch.setattr(remote_cli, "download_resolved", lambda *a, **k: True)
    parser = _parser()
    argv = ["--server", "test", "-i", "appletv", "--url", "https://tv.apple.com/movie/x", "--item", "0"]
    remote_cli.add_remote_site_options(parser, argv, "appletv")

    assert remote_cli.run_remote(parser.parse_args(argv)) is True

    assert api.queries == ["https://tv.apple.com/movie/x"]  # no -s needed
    assert api.seen[0]["options"]["url"] == "https://tv.apple.com/movie/x"
    assert api.seen[0]["token"] == "CLIENT-TOKEN"


def test_search_text_is_used_when_there_is_no_url(cli_server, api, monkeypatch):
    monkeypatch.setattr(remote_cli, "download_resolved", lambda *a, **k: True)
    parser = _parser()
    argv = ["--server", "test", "-i", "appletv", "-s", "a show", "--item", "1", "-S", "1", "-E", "*"]
    remote_cli.add_remote_site_options(parser, argv, "appletv")

    assert remote_cli.run_remote(parser.parse_args(argv)) is True

    assert api.queries == ["a show"]
    assert [s["what"] for s in api.seen] == ["search", "seasons", "resolve"]


def test_rotated_tokens_reach_the_clients_login_file(live, api, monkeypatch):
    api.rotate = True
    stored = []
    monkeypatch.setattr(remote_cli, "pick_remote_server", lambda name: RemoteServer("test", live, KEY))
    monkeypatch.setattr(remote_cli, "_login_data", lambda: client_login())
    monkeypatch.setattr(remote_cli, "_store_login_update", stored.append)

    client = remote_cli.make_client(remote_cli.pick_remote_server("test"))
    client.resolve("appletv", PAYLOAD, "1", "1")

    assert stored == [{"appletv": {"token": "ROTATED-1"}}]


# ── choosing among results: the project's own table and selection ─────────────

def _row(name, type_, year=None, **raw):
    payload = {"name": name, "type": type_.lower(), "raw_data": {"id": name, "name": name, "type": type_.lower(), "year": year or "-", **raw}}
    return {"title": name, "type": type_, "year": year, "is_movie": type_.lower() == "film", "payload": payload}


RESULTS = [_row("One", "Film", "2020"), _row("Two", "Tv"), _row("Three", "Film")]


def test_item_flag_is_zero_based_and_validated():
    assert [r["title"] for r in remote_cli.select_results(RESULTS, 2)] == ["Three"]
    with pytest.raises(RemoteError, match="out of range"):
        remote_cli.select_results(RESULTS, 3)
    assert remote_cli.select_results(RESULTS[:1], None) == RESULTS[:1]  # a single result needs no question


def test_results_use_the_same_table_as_the_local_search(monkeypatch):
    """The services' own get_select_title: Index/Name/Type/Year columns, rows numbered from 0, '1,3' / '1-2' / '*' / 'q'."""
    from VibraVid.utils.console.table import TVShowManager

    shown = {}

    def fake_run(self):
        shown["columns"] = list(self.column_info)
        shown["rows"] = [dict(row) for row in self.tv_shows]
        return "1"

    monkeypatch.setattr(TVShowManager, "run", fake_run)

    chosen = remote_cli.select_results(RESULTS, None)

    assert [r["title"] for r in chosen] == ["Two"]  # Index is 0-based, exactly as in a local run
    assert shown["columns"] == ["Index", "Name", "Type", "Year"]
    assert [r["Index"] for r in shown["rows"]] == ["0", "1", "2"]
    assert shown["rows"][0]["Name"] == "One" and shown["rows"][0]["Year"] == "2020"


@pytest.mark.parametrize(
    "answer, expected",
    [("0", ["One"]), ("0,2", ["One", "Three"]), ("0-1", ["One", "Two"]), ("1-*", ["Two", "Three"]), ("*", ["One", "Two", "Three"])],
)
def test_the_table_accepts_the_usual_multi_selections(monkeypatch, answer, expected):
    from VibraVid.utils.console.table import TVShowManager

    monkeypatch.setattr(TVShowManager, "run", lambda self: answer)

    assert [r["title"] for r in remote_cli.select_results(RESULTS, None)] == expected


def test_a_bad_answer_asks_again(monkeypatch):
    from VibraVid.utils.console.table import TVShowManager

    answers = iter(["abc", "9", "2"])
    monkeypatch.setattr(TVShowManager, "run", lambda self: next(answers))

    assert [r["title"] for r in remote_cli.select_results(RESULTS, None)] == ["Three"]


@pytest.mark.parametrize("quit_key", ["q", "quit", "Q"])
def test_quitting_the_table_cancels(monkeypatch, quit_key):
    from VibraVid.utils.console.table import TVShowManager

    monkeypatch.setattr(TVShowManager, "run", lambda self: quit_key)

    with pytest.raises(RemoteError, match="cancelled"):
        remote_cli.select_results(RESULTS, None)


def test_rows_without_raw_data_still_get_a_table_entry(monkeypatch):
    from VibraVid.utils.console.table import TVShowManager

    monkeypatch.setattr(TVShowManager, "run", lambda self: "1")
    rows = [{"title": "A", "type": "Film", "year": 2001, "payload": {"url": "https://a"}}, {"title": "B", "type": "Tv", "year": None, "payload": {}}]

    assert [r["title"] for r in remote_cli.select_results(rows, None)] == ["B"]


# ── seasons and episodes: the project's own selection ─────────────────────────

SEASONS = [
    {"number": 1, "name": "Stagione 1", "episodes": [{"number": 1, "name": "Pilot"}, {"number": 2, "name": "Two"}]},
    {"number": 3, "name": "Stagione 3", "episodes": [{"number": 1, "name": "Back"}]},
]


def test_remote_series_looks_like_a_service_scraper():
    series = remote_cli.RemoteSeries("Show", 2020, SEASONS)

    assert (series.series_name, series.year, series.getNumberSeason()) == ("Show", 2020, 2)
    assert [s.number for s in series.seasons_manager.seasons] == [1, 3]
    assert series.seasons_manager.get_season_by_number(3).name == "Stagione 3"
    assert [e["name"] for e in series.getEpisodeSeasons(1)] == ["Pilot", "Two"]
    assert series.getEpisodeSeasons(99) == []
    assert (series.position_of_season(1), series.position_of_season(3)) == (1, 2)


@pytest.fixture
def no_artwork(monkeypatch):
    from VibraVid.services._base import tmdb_artwork

    monkeypatch.setattr(tmdb_artwork, "resolve_series_tmdb_id", lambda **k: None)
    monkeypatch.setattr(tmdb_artwork, "resolve_movie_poster_url", lambda **k: None)
    monkeypatch.setattr(tmdb_artwork, "resolve_episode_artwork_url", lambda *a, **k: None)


@pytest.fixture
def resolves(monkeypatch):
    """Records every (season, episodes) the client asks the server to resolve."""
    sent = []
    original = RemoteClient.resolve

    def spy(self, site, payload, season=None, episodes=None, site_options=None):
        sent.append((season, episodes))
        return original(self, site, payload, season, episodes, site_options)

    monkeypatch.setattr(RemoteClient, "resolve", spy)
    return sent


def _remote(cli_server):
    return remote_cli.Remote(remote_cli.make_client(cli_server), "appletv", {}, False)


def test_a_season_pick_resolves_by_position_and_names_files_with_the_real_numbers(cli_server, api, no_artwork, resolves, monkeypatch):
    """Season numbers are 1 and 3: picking the 2nd season (number 3) sends position 2 and names the file S03."""
    api.seasons_override = SEASONS
    downloaded = []
    monkeypatch.setattr(
        remote_cli, "download_resolved",
        lambda client, cdm, sid, index, media, site: downloaded.append((media.title, media.season, media.episode, media.episode_name, media.media_type)) or True,
    )

    ok = remote_cli.download_title(_remote(cli_server), RESULTS[1], types.SimpleNamespace(season="2", episode="*"))

    assert ok is True
    assert resolves == [("2", "1")]
    assert downloaded == [("Two", 3, 1, "Back", "Serie")]


def test_every_season_and_episode_with_star(cli_server, api, no_artwork, resolves, monkeypatch):
    api.seasons_override = SEASONS
    monkeypatch.setattr(remote_cli, "download_resolved", lambda *a, **k: True)

    assert remote_cli.download_title(_remote(cli_server), RESULTS[1], types.SimpleNamespace(season="*", episode=None)) is True

    assert resolves == [("1", "1"), ("1", "2"), ("2", "1")]


def test_a_failed_episode_makes_the_title_fail(cli_server, api, no_artwork, resolves, monkeypatch):
    api.seasons_override = SEASONS
    monkeypatch.setattr(remote_cli, "download_resolved", lambda *a, **k: False)

    assert remote_cli.download_title(_remote(cli_server), RESULTS[1], types.SimpleNamespace(season="1", episode="1")) is False


def test_a_film_needs_no_season(cli_server, api, no_artwork, resolves, monkeypatch):
    seen = []
    monkeypatch.setattr(
        remote_cli, "download_resolved",
        lambda client, cdm, sid, index, media, site: seen.append((media.title, media.media_type, media.year)) or True,
    )

    assert remote_cli.download_title(_remote(cli_server), RESULTS[0], types.SimpleNamespace(season=None, episode=None)) is True

    assert seen == [("One", "Film", "2020")]
    assert resolves == [(None, None)]


# ── the login lines a local run prints ("Try refresh token...", "Login type: Account", "User: ...") ──────────────────

@pytest.fixture
def printed(monkeypatch):
    """Where Login prints: a buffer, with its per-run state reset."""
    import io

    from rich.console import Console

    buffer = io.StringIO()
    monkeypatch.setattr(login_status, "console", Console(file=buffer, highlight=False, force_terminal=False, width=120))
    monkeypatch.setattr(Login, "_open", False)
    monkeypatch.setattr(Login, "_banner_shown", False)
    monkeypatch.setattr(Login, "_cached_seen", set())
    return buffer


def _local_login_run():
    Login.refresh_try()
    Login.refresh_ok(1_800_000_000)
    Login.show(Login.ACCOUNT, "me@example.it", {"Profile": "kids"})


def test_login_calls_are_recorded_instead_of_printed(printed):
    events = []

    with Login.recording(events):
        _local_login_run()

    assert printed.getvalue() == ""
    assert events == [["refresh_try"], ["refresh_ok", 1_800_000_000], ["show", "Account", "me@example.it", {"Profile": "kids"}]]


def test_replayed_login_lines_are_identical_to_a_local_run(printed, monkeypatch):
    import io

    from rich.console import Console

    _local_login_run()
    local = printed.getvalue()

    recorded = []
    with Login.recording(recorded):
        _local_login_run()
    monkeypatch.setattr(Login, "_open", False)
    monkeypatch.setattr(Login, "_banner_shown", False)
    again = io.StringIO()
    monkeypatch.setattr(login_status, "console", Console(file=again, highlight=False, force_terminal=False, width=120))
    Login.replay(json.loads(json.dumps(recorded)))  # as it arrives over HTTP

    assert again.getvalue() == local
    for line in ("Try refresh token...", "Success", "Login type: Account", "User: me@example.it", "Profile: kids"):
        assert line in local




def test_a_failed_refresh_is_replayed(printed):
    with Login.recording(events := []):
        Login.refresh_try()
        Login.refresh_failed("caToken invalid/expired?")

    Login.replay(events)

    assert "Failed" in printed.getvalue() and "caToken invalid/expired?" in printed.getvalue()


def test_the_client_shows_what_the_service_printed_on_the_server(make, api, printed):
    api.announce_login = True

    make().search("appletv", "x")

    text = printed.getvalue()
    assert text.count("Login type: Account") == 1  # once: shown here, not also on the server
    assert "Try refresh token..." in text and "Success" in text and "User: user@example.it" in text


def test_an_anonymous_client_sees_anonymous(make, api, printed):
    api.announce_login = True

    make(login={}).search("appletv", "x")

    assert "Login type: Anonymous" in printed.getvalue()



# ── the server stores nothing: the cache is the client's, carried in memory ──────────────────────────────────────────

@pytest.fixture
def base(tmp_path, monkeypatch):
    """A scratch base path: whatever lands under it was written to disk."""
    monkeypatch.setattr(config_manager, "base_path", str(tmp_path))
    return tmp_path


def _files(root):
    return sorted(str(p.relative_to(root)) for p in Path(root).rglob("*") if p.is_file())


def test_a_remote_request_reads_the_clients_cache_and_never_touches_the_disk(base):
    cache = {"svc": {"session": {"token": "client"}}}

    with scoped({}, "id", cache):
        assert disk_cache.load("svc", "session") == {"token": "client"}
        assert disk_cache.load_fresh("svc", "session") is None  # no expiry in it
        disk_cache.save("svc", "session", {"token": "new", "expiry": 4102444800})
        disk_cache.save("svc", "extra", {"n": 1}, ttl=60)
        disk_cache.invalidate("svc", "session")
        with disk_cache.lock("svc", "extra"):
            assert disk_cache.cache_path("svc", "extra") == f"{CACHE_PATH_PREFIX}svc/extra.json"

    assert _files(base) == []  # not a single file, not a directory marker


def test_what_the_service_changes_is_reported_for_the_client(base):
    identity = build_identity("svc", None, {}, {"svc": {"session": {"t": 1}, "old": {"t": 0}}})

    with identity.active():
        disk_cache.save("svc", "session", {"t": 2})
        disk_cache.invalidate("svc", "old")
        disk_cache.clear_service("nothing-here")

    assert identity.take_cache_updates() == {"svc": {"session": {"t": 2}, "old": None}}
    assert identity.take_cache_updates() == {}
    assert _files(base) == []


def test_a_service_cache_the_client_did_not_send_is_reported_as_wanted(base):
    identity = build_identity("svc", None, {}, {"svc": {}})

    with identity.active():
        assert disk_cache.load("cs", "esn") is None
        assert disk_cache.load("svc", "session") is None  # sent (empty): nothing to ask for

    assert identity.take_cache_wanted() == ["cs"]


def test_clear_service_empties_the_clients_copy_and_reports_each_file(base):
    identity = build_identity("svc", None, {}, {"svc": {"a": {"x": 1}, "b": {"x": 2}}})

    with identity.active():
        assert disk_cache.clear_service("svc") == 2

    assert identity.take_cache_updates() == {"svc": {"a": None, "b": None}}


def test_values_are_copies_like_files_would_be(base):
    cache = {"svc": {"session": {"nested": {"n": 1}}}}

    with scoped({}, "id", cache):
        got = disk_cache.load("svc", "session")
        got["nested"]["n"] = 99

    assert cache["svc"]["session"]["nested"]["n"] == 1


def test_data_that_is_not_json_is_refused_without_breaking_the_service(base):
    with scoped({}, "id", {"svc": {}}):
        disk_cache.save("svc", "bad", {"obj": object()})  # a file could not hold it either

        assert disk_cache.load("svc", "bad") is None


def test_the_site_loader_manifest_is_not_user_data_and_stays_on_disk(base):
    with scoped({}, "id", {}):
        disk_cache.save("_site_loader", "manifest", {"a": 1})

    assert _files(base) == [os.path.join(".cache", "services", "_site_loader", "manifest.json")]


def test_cache_must_be_an_object_of_objects():
    for bad in ("text", ["a"], {"svc": "not-an-object"}):
        with pytest.raises(RemoteError) as error:
            build_identity("svc", None, {}, bad)
        assert error.value.status == 400
    with pytest.raises(RemoteError) as error:
        build_identity("svc", None, {}, {"svc": {"blob": "x" * (5 * 1024 * 1024)}})
    assert error.value.status == 413


def test_local_cache_sends_what_is_on_the_client_and_stores_what_comes_back(base):
    disk_cache.save("svc", "session", {"token": "client"})
    disk_cache.save("svc", "old", {"token": "x"})
    bridge = LocalCache()

    assert bridge.collect({"svc", "empty"}) == {"svc": {"old": {"token": "x"}, "session": {"token": "client"}}, "empty": {}}

    assert bridge.apply({"svc": {"session": {"token": "rotated"}, "old": None}, "other": {"a": 1}}) is True
    assert disk_cache.load("svc", "session") == {"token": "rotated"}
    assert disk_cache.load("svc", "old") is None
    assert disk_cache.load("other", "a") == 1
    assert bridge.apply({}) is False


def test_local_cache_skips_files_that_are_too_big(base):
    disk_cache.save("svc", "small", {"n": 1})
    disk_cache.save("svc", "big", {"blob": "x" * (LocalCache.MAX_FILE_BYTES + 10)})

    assert LocalCache().collect({"svc"}) == {"svc": {"small": {"n": 1}}}


def test_the_service_cache_travels_with_the_request_and_comes_back_in_the_answer(make, api, base):
    api.use_cache = True
    disk_cache.save("appletv", "session", {"token": "CLIENT-SESSION"})
    disk_cache.save("appletv", "stale", {"token": "old"})
    client = make()
    client.local_cache = LocalCache()

    client.resolve("appletv", PAYLOAD, "1", "1")

    assert api.cache_seen == [{"token": "CLIENT-SESSION"}]  # the service found the client's file
    assert api.on_disk_while_serving == [["session.json", "stale.json"]]  # only the client's own files, nothing the server wrote
    assert disk_cache.load("appletv", "session") == {"token": "FRESH", "expiry": 4102444800}  # stored by the client afterwards
    assert disk_cache.load("appletv", "other") == {"x": 1}
    assert disk_cache.load("appletv", "stale") is None
    assert api.cache_paths == [f"{CACHE_PATH_PREFIX}appletv/session.json"]


def test_no_cache_is_sent_when_credentials_are_switched_off(make, api, base):
    api.use_cache = True
    disk_cache.save("appletv", "session", {"token": "CLIENT-SESSION"})
    client = make(send_credentials=False)
    client.local_cache = LocalCache()

    client.resolve("appletv", PAYLOAD, "1", "1")

    assert api.cache_seen == [None]


def test_a_cache_service_the_service_reads_is_requested_and_remembered(make, api, base):
    api.read_cache_service = "cs"
    disk_cache.save("cs", "esn", {"esn": "CLIENT-ESN"})
    client = make()
    client.local_cache = LocalCache()

    client.resolve("appletv", PAYLOAD, "1", "1")

    assert api.cache_seen == [None, {"esn": "CLIENT-ESN"}]  # first try: not sent; the retry carried it
    assert client._extra_cache["appletv"] == {"cs"}
    assert [set(body["cache"]) for _, body in client.bodies] == [{"appletv"}, {"appletv", "cs"}]

    client.resolve("appletv", PAYLOAD, "1", "1")  # learned: the next call carries it at once
    assert len(client.bodies) == 3 and api.cache_seen[-1] == {"esn": "CLIENT-ESN"}


def test_plain_http_is_refused_for_the_cache_too(base):
    disk_cache.save("appletv", "session", {"token": "t"})
    client = RemoteClient(RemoteServer("far", "http://203.0.113.9:8786", KEY), login_data={}, local_cache=LocalCache())

    with pytest.raises(RemoteError, match="refusing to send your credentials"):
        client.search("appletv", "x")


# ── nothing about the client outlives its request or session ──────────────────────────────────────────────────────

def test_a_sessions_secrets_are_wiped_when_it_ends():
    store = SessionStore()
    identity = build_identity("svc", {"svc": {"token": "T"}}, {}, {"svc": {"f": {"t": 1}}})
    session = store.create("alice", "1.1.1.1", "svc", [], [{"license_request_fn": print}], identity)

    store.delete(session.id, "alice", "1.1.1.1")

    assert identity.overlay == {} and identity.cache == {}
    assert session.callables == [] and session.items == []


def test_an_expired_session_is_wiped_too():
    store = SessionStore(ttl=0)
    identity = build_identity("svc", {"svc": {"token": "T"}}, {}, None)
    store.create("alice", "1.1.1.1", "svc", [], [], identity)
    import time

    time.sleep(0.02)
    len(store)  # purges

    assert identity.overlay == {}


def test_the_service_state_and_scrapers_are_dropped_after_every_request(fake_service):
    from searchapp.api.base import BaseStreamingAPI

    module, other = fake_service
    identity = build_identity("fakesvc", {"fakesvc": {"token": "T"}}, {})
    BaseStreamingAPI._scraper_cache[f"{identity.scope}:fakesvc_x"] = ("scraper", 0.0)
    BaseStreamingAPI._scraper_cache["another-scope:fakesvc_x"] = ("kept", 0.0)

    def request():
        assert module._client is None  # a new identity starts from a clean service...
        module._client = _Client()  # ...logs in during the request...
        module._client_by_region["it"] = object()
        return module._client

    assert _in_thread(request, identity=identity) is not None

    assert module._client is None and other._csi_client is None  # ...and nothing of it is left afterwards
    assert module._client_by_region == {}
    assert f"{identity.scope}:fakesvc_x" not in BaseStreamingAPI._scraper_cache
    assert "another-scope:fakesvc_x" in BaseStreamingAPI._scraper_cache  # someone else's is not touched
    BaseStreamingAPI._scraper_cache.pop("another-scope:fakesvc_x", None)


def test_a_cached_token_is_labelled_with_the_clients_own_file(printed, base):
    with Login.recording(events := []):
        Login.refresh_cached(f"{CACHE_PATH_PREFIX}appletv/session.json")

    Login.replay(events)

    assert disk_cache.cache_path("appletv", "session") in printed.getvalue().replace("\n", "")


def test_the_same_login_block_is_not_shown_twice(make, api, printed):
    api.announce_login = True
    client = make()

    client.search("appletv", "x")
    client.seasons("appletv", PAYLOAD)

    assert printed.getvalue().count("Login type: Account") == 1


# ── a server asks nobody and writes nothing for a client ──────────────────────────────────────────────────────────

def test_a_prompt_during_a_remote_request_fails_fast_with_a_clear_message():
    from rich.prompt import Confirm, Prompt

    with scoped({}, "id", {}):
        with pytest.raises(RemoteError, match="asked for input a server cannot give: 'Insert media index'") as error:
            Prompt.ask("[cyan]Insert media [red]index")
        assert error.value.status == 422
        with pytest.raises(RemoteError, match="cannot give"):
            Confirm.ask("Sure?")
        with pytest.raises(RemoteError, match="cannot give"):
            input("name: ")
        with pytest.raises(RemoteError, match="cannot give"):
            Prompt().ask("instance form, as `msg.ask` does")


def test_prompts_work_as_usual_outside_a_remote_request():
    from rich.prompt import Prompt

    import io

    assert Prompt.ask("answer?", stream=io.StringIO("typed\n")) == "typed"


def test_a_remote_request_cannot_write_files_outside_scratch_space(base):
    # inside the repo, i.e. outside the system temp dir that scratch space is allowed to use
    target = ROOT / "Test" / "Remote" / "_must_never_exist.json"

    try:
        with scoped({}, "id", {}):
            with pytest.raises(PermissionError, match="a server stores nothing"):
                open(target, "w").write("secret")
            with pytest.raises(PermissionError):
                Path(target).write_text("secret")
            with pytest.raises(PermissionError):
                open(target, "r+")
            assert open(__file__, "r")  # reading is fine
        assert not target.exists()
    finally:
        target.unlink(missing_ok=True)


def test_scratch_space_logs_and_normal_runs_can_still_write(tmp_path, base):
    import tempfile

    scratch = Path(tempfile.mkdtemp(prefix="vv_guard_"))
    log = base / ".cache" / "logs" / "run.log"
    log.parent.mkdir(parents=True)
    outside = tmp_path / "normal.json"

    with scoped({}, "id", {}):
        (scratch / "a.txt").write_text("fine")  # e.g. the key flow's temporary folder
        log.write_text("fine")
    outside.write_text("a local run writes wherever it likes")  # no remote request: no guard

    assert (scratch / "a.txt").read_text() == "fine" and log.read_text() == "fine" and outside.exists()
    shutil.rmtree(scratch, ignore_errors=True)


# ── services without a GUI adapter are served generically ─────────────────────────────────────────────────────────

class _LazyStub:
    hide = False


def test_loaded_services_without_an_adapter_are_offered(monkeypatch):
    import VibraVid.services._base.site_loader as loader

    monkeypatch.setattr(loader, "load_search_functions", lambda: {"pornhub_search": _LazyStub(), "appletv_search": _LazyStub(), "secret_search": types.SimpleNamespace(hide=True)})
    monkeypatch.setattr(api_remote, "get_available_sites", lambda: ["appletv"])
    monkeypatch.undo()  # the fixtures' fake site list must not hide the real helper under test
    monkeypatch.setattr(loader, "load_search_functions", lambda: {"pornhub_search": _LazyStub(), "appletv_search": _LazyStub(), "secret_search": types.SimpleNamespace(hide=True)})
    monkeypatch.setattr(api_remote, "get_available_sites", lambda: ["appletv"])

    assert api_remote._fallback_sites() == ["pornhub"]  # appletv has its adapter, the hidden one stays hidden


def test_the_generic_adapter_searches_and_has_no_series(monkeypatch):
    seen = {}

    class Module:
        def __call__(self, query, get_onlyDatabase=False, **kwargs):
            seen["query"] = query
            return types.SimpleNamespace(media_list=[types.SimpleNamespace(id="1", name="A video", type="film", url="https://x/1")])

    monkeypatch.setattr(api_remote, "get_available_sites", lambda: [])
    monkeypatch.setattr(api_remote, "_fallback_apis", {})
    api = api_remote._api("pornhub")
    monkeypatch.setattr(type(api), "_get_search_fn", lambda self: Module())

    results = api.search("hello")

    assert seen["query"] == "hello" and [r.name for r in results] == ["A video"]
    assert api.get_series_metadata(Entries(name="x", type="tv", id="1")) is None  # no adapter: no seasons, a clean "no seasons" answer
    assert api_remote._api("pornhub") is api  # one adapter per service


def test_an_id_given_on_the_command_line_replaces_the_search(cli_server, api, monkeypatch):
    monkeypatch.setattr(remote_cli, "download_resolved", lambda *a, **k: True)
    parser = _parser()
    argv = ["--server", "test", "-i", "appletv", "--server-opt", "direct_id=81234", "--item", "0"]

    assert remote_cli.run_remote(parser.parse_args(argv)) is True

    assert api.queries == ["81234"]  # csi / nowtvit --id: the service looks the title up by that id
