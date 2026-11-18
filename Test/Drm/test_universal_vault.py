# 15.10.26

import sys
from pathlib import Path

import pytest

workspace_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(workspace_root))

from VibraVid.core.drm import manager as drm_manager
from VibraVid.core.drm.manager import DRMManager
from VibraVid.core.ui.tracker import context_tracker
from VibraVid.utils import config_manager, disk_cache
from VibraVid.utils.vault import default as default_module
from VibraVid.utils.vault import default_vault, universal
from VibraVid.utils.vault.universal import UniversalVault, build_universal_vault

KID = "022104ece4f643c7b32d081785659a54"
KEY = "46dc52d5160976ef41c38c95e74e8952"


class FakeResponse:
    def __init__(self, status_code=200, payload=None, headers=None):
        self.status_code = status_code
        self._payload = payload
        self.headers = headers or {}
        self.content = b"x" if payload is not None else b""

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class FakeSession:
    """Routes calls to handler(method, url, params, json) and records them. The custom-REST probe
    (POST {url}/get-keys) gets a 404 unless *rest* is set, so handlers only model their own protocol."""

    def __init__(self, handler, rest=False):
        self.handler = handler
        self.rest = rest
        self.calls = []

    def get(self, url, params=None, timeout=None):  # noqa: ARG002
        self.calls.append(("GET", url, params, None))
        return self.handler("GET", url, params, None)

    def post(self, url, json=None, headers=None, timeout=None):  # noqa: ARG002
        self.calls.append(("POST", url, None, json))
        if url.endswith("/get-keys"):
            return FakeResponse(200, {"keys": []}) if self.rest else FakeResponse(404, None)
        return self.handler("POST", url, None, json)

    def close(self):
        pass


@pytest.fixture(autouse=True)
def isolated_state(tmp_path, monkeypatch):
    monkeypatch.setattr(disk_cache, "_service_dir", lambda service: str(tmp_path / service))
    monkeypatch.setattr(UniversalVault, "_profiles", {})
    monkeypatch.setattr(UniversalVault, "_cooldown_until", {})
    monkeypatch.setattr(universal, "_instances", {})
    monkeypatch.setattr(context_tracker, "site_name", None, raising=False)


def make_vault(handler, name="v", **cfg):
    vault = UniversalVault(name, {"url": "https://vault.example/api", "token": "t", **cfg})
    vault.session = FakeSession(handler)
    return vault


def json_rpc_handler(services=None, keys=None, status=200):
    """JSON-RPC vault: GetServices -> *services* (or a 403 'not supported'), GetKey -> keys[(kid, service)]."""
    keys = keys or {}

    def handler(method, url, params, body):  # noqa: ARG001
        if body["method"] == "GetServices":
            if services is None:
                return FakeResponse(403, {"status_code": 403, "message": "Listing services is not supported."})
            return FakeResponse(200, {"status_code": 200, "services": services})
        p = body["params"]
        key = keys.get((p["kid"], p["service"]))
        if key is None:
            return FakeResponse(404, {"status_code": 404, "message": {"keys": []}})
        return FakeResponse(status, {"status_code": status, "message": {"keys": [{"kid": p["kid"], "key": key}]}})

    return handler


# ── Protocol detection + disk_cache ──────────────────────────────────────────────────────


def test_json_vault_with_service_list_is_cached_and_reused():
    vault = make_vault(json_rpc_handler(services=["GENERIC", "NETFLIX"], keys={(KID, "GENERIC"): KEY}))
    assert vault.get_keys_by_kids("https://lic.example/x", [KID]) == [f"{KID}:{KEY}"]

    cached = disk_cache.load("vault", vault._cache_name())
    assert cached["protocol"] == "json" and cached["services"] == ["GENERIC", "NETFLIX"]

    # A new process (empty in-memory profiles) must reuse the disk profile: no GetServices probe again.
    UniversalVault._profiles.clear()
    again = make_vault(json_rpc_handler(services=["GENERIC"], keys={(KID, "GENERIC"): KEY}))
    again.get_keys_by_kids(None, [KID])
    assert not any(body and body.get("method") == "GetServices" for _, _, _, body in again.session.calls)
    assert not any(url.endswith("/get-keys") for _, url, _, _ in again.session.calls)


def test_json_vault_without_listing_uses_generic_only():
    vault = make_vault(json_rpc_handler(services=None, keys={(KID, "generic"): KEY}))
    assert vault._profile()["protocol"] == "json" and vault._profile()["services"] is None
    assert vault._services_for("https://license.netflix.com/x") == ["generic"]
    assert vault.get_keys_by_kids(None, [KID]) == [f"{KID}:{KEY}"]


def test_api_vault_detected_when_post_has_no_route():
    def handler(method, url, params, body):  # noqa: ARG001
        if method == "POST":
            return FakeResponse(404, None)
        if url.endswith(f"/generic/{KID}"):
            return FakeResponse(200, {"code": 0, "content_key": KEY})
        return FakeResponse(404, None)

    vault = make_vault(handler)
    assert vault._profile()["protocol"] == "api"
    assert vault.get_keys_by_kids(None, [KID]) == [f"{KID}:{KEY}"]


def test_query_vault_from_username_password():
    def handler(method, url, params, body):  # noqa: ARG001
        assert method == "GET" and params["username"] == "u" and params["password"] == "p"
        return FakeResponse(200, {"keys": [{"kid": KID, "key": KEY}]})

    vault = UniversalVault("q", {"url": "https://q.example", "username": "u", "password": "p"})
    vault.session = FakeSession(handler)
    assert vault._profile()["protocol"] == "query"
    assert vault.get_keys_by_kids(None, [KID]) == [f"{KID}:{KEY}"]


def test_explicit_protocol_skips_detection():
    calls = []

    def handler(method, url, params, body):  # noqa: ARG001
        calls.append(method)
        return FakeResponse(200, {"code": 0, "content_key": KEY})

    vault = make_vault(handler, protocol="api")
    assert vault.get_keys_by_kids(None, [KID]) == [f"{KID}:{KEY}"]
    assert calls == ["GET"]


# ── Service selection from the vault's own list (no hardcoded table) ────────────────────
@pytest.mark.parametrize(
    ("site", "license_url", "expected_first"),
    [
        ("primevideo", None, "AMAZON PRIME VIDEO"),
        (None, "https://atv-ps-eu.amazon.com/cdp/catalog", "AMAZON PRIME VIDEO"),
        ("discoveryplus", None, "DISCOVERY PLUS"),
        ("crunchyroll", "https://cr-license-proxy.prd.crunchyrollsvc.com/v1/license/widevine", "CRUNCHYROLL"),
    ],
)
def test_services_matched_from_list(monkeypatch, site, license_url, expected_first):
    monkeypatch.setattr(context_tracker, "site_name", site, raising=False)
    services = ["GENERIC", "AMAZON PRIME VIDEO", "NETFLIX", "DISCOVERY PLUS", "CRUNCHYROLL", "PLUTO TV"]
    vault = make_vault(json_rpc_handler(services=services))
    order = vault._services_for(license_url)
    assert order[0] == expected_first and order[-1] == "GENERIC"


def test_unmatched_site_falls_back_to_generic(monkeypatch):
    monkeypatch.setattr(context_tracker, "site_name", "raiplay", raising=False)
    vault = make_vault(json_rpc_handler(services=["GENERIC", "NETFLIX"]))
    assert vault._services_for("https://license.example.org/wv") == ["GENERIC"]


# ── Rate limiting ────────────────────────────────────────────────────────────────────────


def test_rate_limit_stops_and_cools_down():
    def handler(method, url, params, body):  # noqa: ARG001
        if body["method"] == "GetServices":
            return FakeResponse(200, {"status_code": 200, "services": ["GENERIC", "NETFLIX"]})
        return FakeResponse(429, {"status_code": 429, "retry_after": 600})

    vault = make_vault(handler)
    vault._services_for = lambda license_url: ["NETFLIX", "GENERIC"]
    assert vault.get_keys_by_kids(None, [KID, KID]) == []
    assert sum(1 for c in vault.session.calls if (c[3] or {}).get("method") == "GetKey") == 1  # stopped at the first 429
    assert vault.is_rate_limited()

    before = len(vault.session.calls)
    assert vault.get_keys_by_kids(None, [KID]) == []
    assert len(vault.session.calls) == before  # skipped entirely while cooling down


def test_rate_limited_probe_is_not_cached():
    def handler(method, url, params, body):  # noqa: ARG001
        return FakeResponse(429, {"status_code": 429, "retry_after": 5})

    vault = make_vault(handler)
    assert vault._profile()["probed"] is False
    assert disk_cache.load("vault", vault._cache_name()) is None


def test_api_rate_limit_in_body():
    def handler(method, url, params, body):  # noqa: ARG001
        if method == "POST":
            return FakeResponse(404, None)
        return FakeResponse(200, {"code": 2, "message": "rate limited"})

    vault = make_vault(handler)
    assert vault.get_keys_by_kids(None, [KID]) == []
    assert vault.is_rate_limited()


# ── DRMManager wiring ────────────────────────────────────────────────────────────────────


@pytest.fixture
def vault_config(monkeypatch):
    original = config_manager.config.get_dict

    def _apply(entries):
        monkeypatch.setattr(
            config_manager.config,
            "get_dict",
            lambda section, key, default=None: entries if (section, key) == ("DRM", "vault") else original(section, key, default=default),
        )

    return _apply


@pytest.mark.parametrize("default_keys", [("default",), ("vault_1",), ("default", "vault_1")])
def test_build_vaults_kinds_and_order(vault_config, monkeypatch, default_keys):
    monkeypatch.setattr(default_module, "SAVE_KEYS", True)
    monkeypatch.setattr(default_vault, "base_url", "https://drm-db.example")
    vault_config(
        {
            **{key: {"url": "https://drm-db.example", "token": ""} for key in default_keys},
            "mine": {"url": "https://mine.example", "token": "x", "protocol": "custom"},
            "third": {"url": "https://third.example", "token": "y", "protocol": "json"},
            "dup": {"url": "https://drm-db.example/", "token": "z", "protocol": "api"},
        }
    )
    vaults = DRMManager()._vaults
    assert [v.name for v in vaults] == ["claudio", "mine", "third"]
    assert vaults[0] is default_vault
    assert isinstance(vaults[2], UniversalVault) and not vaults[2].is_writable
    assert vaults[0].is_writable and vaults[1].is_writable


def test_store_keys_skips_read_only_vaults(vault_config):
    vault_config({"third": {"url": "https://third.example", "token": "y", "protocol": "json"}})
    mgr = DRMManager()
    third = mgr._vaults[0]
    third.session = FakeSession(lambda *a: pytest.fail("a read-only vault must not be queried or written on store"))
    mgr._store_keys([f"{KID}:{KEY}"], "widevine", "https://lic.example", None)


def test_universal_instances_are_shared():
    cfg = {"url": "https://third.example", "token": "y", "protocol": "json"}
    assert build_universal_vault("third", cfg) is build_universal_vault("third", cfg)
    assert build_universal_vault("third", {**cfg, "token": "other"}) is not build_universal_vault("third", cfg)


def test_manager_has_no_lab_singleton():
    assert not hasattr(drm_manager, "lab_vault")


# ── Protocol auto-detection without any flag, and save_keys ─────────────────────────────


def test_custom_rest_vault_detected_and_cached():
    vault = make_vault(lambda *a: pytest.fail("a custom vault must not get the JSON-RPC probe"))
    vault.session.rest = True
    assert vault.protocol == "custom"
    assert disk_cache.load("vault", vault._cache_name())["protocol"] == "custom"
    assert vault.get_keys_by_kids(None, [KID]) == []  # custom vaults are served by the default client, not this one


def test_legacy_universal_flag_skips_custom_probe():
    vault = make_vault(json_rpc_handler(services=None), universal=True)
    vault.session.rest = True  # would look like a custom vault if it were probed
    assert vault.protocol == "json"
    assert not any(url.endswith("/get-keys") for _, url, _, _ in vault.session.calls)


def test_build_vaults_autodetects_without_protocol(vault_config, monkeypatch):
    is_rest = {"https://mine.example": True, "https://third.example": False}
    monkeypatch.setattr(universal, "create_client", lambda headers=None, **kwargs: FakeSession(json_rpc_handler(services=None)))
    original_init = UniversalVault.__init__

    def init(self, name, cfg):
        original_init(self, name, cfg)
        self.session.rest = is_rest[self.url]

    monkeypatch.setattr(UniversalVault, "__init__", init)
    vault_config({"mine": {"url": "https://mine.example", "token": "x"}, "third": {"url": "https://third.example", "token": "y"}})
    vaults = DRMManager()._vaults
    assert [(v.name, type(v).__name__) for v in vaults] == [("mine", "Vault_di_default"), ("third", "UniversalVault")]


def test_save_keys_is_off_by_default():
    assert universal._DEFAULT_SAVE_KEYS is False
    vault = make_vault(json_rpc_handler(services=None), protocol="json")
    assert not vault.is_writable and vault.set_keys([f"{KID}:{KEY}"], None) == 0
    assert vault.session.calls == []


@pytest.mark.parametrize("protocol", ["api", "json"])
def test_save_keys_uploads_when_enabled(protocol):
    def handler(method, url, params, body):  # noqa: ARG001
        if protocol == "api":
            assert method == "POST" and url.endswith(f"/generic/{KID}") and body == {"content_key": KEY}
            return FakeResponse(200, {"code": 0})
        assert body["method"] == "InsertKey" and body["params"] == {"kid": KID, "key": KEY, "service": "generic"}
        return FakeResponse(200, {"status_code": 200})

    vault = make_vault(handler, protocol=protocol, save_keys=True)
    assert vault.is_writable
    assert vault.set_keys([f"{KID}:{KEY.upper()}"], None) == 1


def test_query_vault_never_writable():
    vault = UniversalVault("q", {"url": "https://q.example", "username": "u", "password": "p", "save_keys": True})
    assert not vault.is_writable


def test_vault_label_uses_config_position(vault_config, monkeypatch):
    monkeypatch.setattr(default_vault, "base_url", "https://drm-db.example")
    vault_config(
        {
            "default": {"url": "https://drm-db.example", "token": ""},
            "offline": {"url": "", "token": "", "protocol": "api"},  # not connected: skipped, but still counts as position 2
            "third": {"url": "https://third.example", "token": "y", "protocol": "json"},
        }
    )
    mgr = DRMManager()
    assert [v.name for v in mgr._vaults] == ["claudio", "third"]
    assert mgr.vault_label("claudio") == "claudio [1]"
    assert mgr.vault_label("third") == "third [3]"
    assert mgr.vault_label("cdm") == "cdm"
