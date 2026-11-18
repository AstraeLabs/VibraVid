# 26.10.26

import sqlite3
import sys
from pathlib import Path

import pytest

workspace_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(workspace_root))

from VibraVid.core.drm.manager import DRMManager
from VibraVid.core.ui.tracker import context_tracker
from VibraVid.utils import config_manager, disk_cache
from VibraVid.utils.vault import default as default_module
from VibraVid.utils.vault import importer
from VibraVid.utils.vault.local import DB_NAME, LocalVault, local_vault, service_name

KID = "022104ece4f643c7b32d081785659a54"
KEY = "46dc52d5160976ef41c38c95e74e8952"
KID2 = "11111111-1111-1111-1111-111111111111"
KEY2 = "22222222222222222222222222222222"


@pytest.fixture(autouse=True)
def isolated_state(tmp_path, monkeypatch):
    monkeypatch.setattr(disk_cache, "_service_dir", lambda service: str(tmp_path / "services" / service))
    monkeypatch.setattr(context_tracker, "site_name", "primevideo", raising=False)
    yield
    local_vault.close()


def rows(path):
    conn = sqlite3.connect(path)
    try:
        return conn.execute("SELECT kid, key, license_url, pssh, synced_at FROM keys ORDER BY kid").fetchall()
    finally:
        conn.close()


def test_service_name():
    assert service_name(" Prime_Video ") == "primevideo"
    assert service_name("mediaset-infinity") == "mediaset-infinity"
    assert service_name(None) == "generic"
    assert service_name("???") == "generic"


def test_keys_go_in_the_cache_folder_of_the_service(tmp_path):
    vault = LocalVault()
    assert vault.set_keys([f"{KID}:{KEY}"], "https://lic.example/path?x=1", "PSSH") == 1
    db = tmp_path / "services" / "primevideo" / DB_NAME
    assert db.is_file()
    assert rows(db) == [(KID, KEY, "https://lic.example", "PSSH", None)]

    context_tracker.site_name = "raiplay"
    vault.set_keys([f"{KID2}:{KEY2}"], "https://lic.example", "PSSH")
    assert [r[0] for r in rows(tmp_path / "services" / "raiplay" / DB_NAME)] == [KID2.replace("-", "")]


def test_service_missing_falls_back_to_generic(tmp_path):
    context_tracker.site_name = None
    LocalVault().set_keys([f"{KID}:{KEY}"], "https://lic.example", None)
    assert (tmp_path / "services" / "generic" / DB_NAME).is_file()


def test_existing_row_is_never_overwritten_and_lookup_works(tmp_path):
    vault = LocalVault()
    assert vault.set_keys([f"{KID}:{KEY}"], "https://lic.example", "PSSH") == 1
    assert vault.set_keys([f"{KID}:{KEY}", f"{KID}:{KEY2}"], "https://lic.example", "PSSH") == 0  # same (kid, license, pssh)
    assert vault.set_keys([f"{KID}:{KEY2}"], "https://other.example", "PSSH") == 1  # another license is another row

    assert vault.get_keys_by_kids("https://elsewhere.example", [KID.upper(), "f" * 32]) == [f"{KID}:{KEY2}"]
    assert vault.get_keys_by_pssh("https://lic.example", "PSSH") == [f"{KID}:{KEY2}", f"{KID}:{KEY}"]
    assert vault.get_keys_by_kids(None, []) == []


def test_wrong_key_is_dropped_and_replaced():
    vault = LocalVault()
    vault.set_keys([f"{KID}:{KEY}"], "https://lic.example", "PSSH")
    assert vault.replace_key(KID, KEY2, KEY, "https://lic.example", "PSSH")
    assert vault.get_keys_by_kids(None, [KID]) == [f"{KID}:{KEY2}"]
    assert vault.report_wrong_key(KID, KEY2, "https://lic.example", "PSSH")
    assert vault.get_keys_by_kids(None, [KID]) == []


def test_remote_request_does_not_touch_the_disk(tmp_path, monkeypatch):
    monkeypatch.setattr(disk_cache, "_remote", lambda service: True)
    vault = LocalVault()
    assert vault.set_keys([f"{KID}:{KEY}"], "https://lic.example", "PSSH") == 0
    assert vault.get_keys_by_kids(None, [KID]) == []
    assert not (tmp_path / "services").exists()


# ── DRMManager wiring ─────────────────────────────────────────────────────────────────────


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


def test_local_vault_enabled_comes_first(vault_config, monkeypatch):
    monkeypatch.setattr(default_module.default_vault, "base_url", "https://drm-db.example")
    vault_config({"default": {"url": "https://drm-db.example", "token": ""}, "local": {"enabled": True}})
    assert [v.name for v in DRMManager()._vaults] == ["local", "claudio"]


@pytest.mark.parametrize("local_cfg", [{"enabled": False}, {}])
def test_local_vault_disabled_is_not_built(vault_config, monkeypatch, local_cfg):
    monkeypatch.setattr(default_module.default_vault, "base_url", "https://drm-db.example")
    vault_config({"default": {"url": "https://drm-db.example", "token": ""}, "local": local_cfg})
    assert [v.name for v in DRMManager()._vaults] == ["claudio"]


def test_server_vault_is_read_only_unless_save_keys(vault_config, monkeypatch):
    posts = []
    vault = default_module.default_vault
    monkeypatch.setattr(default_module, "SAVE_KEYS", False)
    monkeypatch.setattr(vault, "_post", lambda endpoint, payload: posts.append(endpoint) or {})

    assert not vault.is_writable
    assert not vault.report_wrong_key(KID, KEY, "https://lic.example", "PSSH")
    assert not vault.replace_key(KID, KEY2, KEY, "https://lic.example", "PSSH")
    assert posts == []

    monkeypatch.setattr(default_module, "SAVE_KEYS", True)
    assert vault.is_writable


def test_store_keys_writes_local_only_while_the_server_is_read_only(vault_config, monkeypatch, tmp_path):
    monkeypatch.setattr(default_module, "SAVE_KEYS", False)
    monkeypatch.setattr(default_module.default_vault, "base_url", "https://drm-db.example")
    monkeypatch.setattr(default_module.default_vault, "_post", lambda *a: pytest.fail("the read-only server must not be written"))
    vault_config({"default": {"url": "https://drm-db.example", "token": ""}, "local": {"enabled": True}})

    mgr = DRMManager()
    mgr._vaults[1].get_keys_by_kids = lambda *a, **k: []
    mgr._store_keys([f"{KID}:{KEY}"], "widevine", "https://lic.example", "PSSH")
    assert rows(tmp_path / "services" / "primevideo" / DB_NAME)[0][:2] == (KID, KEY)


# ── import to the server ──────────────────────────────────────────────────────────────────


class FakeClient:
    def __init__(self, replies=None):
        self.requests = []
        self.replies = list(replies or [])

    def post(self, url, json=None, timeout=None):  # noqa: ARG002
        self.requests.append((url, json))
        if self.replies:
            return self.replies.pop(0)
        results = [{"kid": k["kid"], "status": "added"} for g in json["groups"] for k in g["keys"]]
        groups = [{"license_url": g["license_url"], "pssh": g["pssh"], "results": [{"kid": k["kid"], "status": "added"} for k in g["keys"]]} for g in json["groups"]]
        return FakeResponse(200, {"code": 0, "groups": groups, "results": results})

    def close(self):
        pass


class FakeResponse:
    def __init__(self, status_code, payload=None, headers=None):
        self.status_code = status_code
        self._payload = payload
        self.headers = headers or {}
        self.text = str(payload)

    def json(self):
        return self._payload


@pytest.fixture
def server(monkeypatch):
    client = FakeClient()
    monkeypatch.setattr(default_module, "VAULT_URL", "https://drm-db.example/")
    monkeypatch.setattr(importer, "create_client", lambda **kw: client)
    monkeypatch.setattr(importer.time, "sleep", lambda s: None)
    return client


def test_import_sends_the_keys_with_the_service_of_the_folder_and_marks_them(server, tmp_path):
    vault = LocalVault()
    vault.set_keys([f"{KID}:{KEY}", f"{KID2}:{KEY2}"], "https://lic.example", "PSSH")

    totals = importer.import_to_server(str(tmp_path / "services"))
    assert totals["added"] == 2 and totals["error"] == 0

    url, body = server.requests[0]
    assert url == "https://drm-db.example/v1/import"
    assert body["service"] == "primevideo"
    assert body["groups"] == [
        {
            "license_url": "https://lic.example",
            "pssh": "PSSH",
            "keys": [{"kid": KID, "key": KEY}, {"kid": KID2.replace("-", ""), "key": KEY2}],
        }
    ]
    assert all(r[4] for r in rows(tmp_path / "services" / "primevideo" / DB_NAME))

    # a second run has nothing pending, unless asked to send everything again
    server.requests.clear()
    assert importer.import_to_server(str(tmp_path / "services"))["pending"] == 0 and server.requests == []
    importer.import_to_server(str(tmp_path / "services"), resend=True)
    assert len(server.requests) == 1


def test_import_dry_run_sends_nothing(server, tmp_path):
    LocalVault().set_keys([f"{KID}:{KEY}"], "https://lic.example", "PSSH")
    totals = importer.import_to_server(str(tmp_path / "services" / "primevideo" / DB_NAME), dry_run=True)
    assert totals["pending"] == 1 and server.requests == []


def test_import_batches_groups_and_splits_big_groups(server, tmp_path):
    vault = LocalVault()
    for i in range(12):  # 12 groups (one pssh each) -> two requests of 10 and 2
        vault.set_keys([f"{i:032x}:{KEY}"], "https://lic.example", f"PSSH{i}")
    vault.set_keys([f"{1000 + i:032x}:{KEY}" for i in range(120)], "https://big.example", "BIG")  # 120 keys -> groups of 50, 50, 20

    importer.import_to_server(str(tmp_path / "services"))
    sizes = [len(body["groups"]) for _, body in server.requests]
    assert sizes == [10, 5]
    assert max(len(g["keys"]) for _, body in server.requests for g in body["groups"]) == 50


def test_import_marks_nothing_when_the_server_fails(server, tmp_path):
    LocalVault().set_keys([f"{KID}:{KEY}"], "https://lic.example", "PSSH")
    server.replies = [FakeResponse(400, "bad")]
    totals = importer.import_to_server(str(tmp_path / "services"))
    assert totals["error"] == 1
    assert rows(tmp_path / "services" / "primevideo" / DB_NAME)[0][4] is None


def test_import_waits_on_429_then_goes_on(server, tmp_path):
    LocalVault().set_keys([f"{KID}:{KEY}"], "https://lic.example", "PSSH")
    server.replies = [FakeResponse(429, "slow", {"Retry-After": "3"})]
    assert importer.import_to_server(str(tmp_path / "services"))["added"] == 1
    assert len(server.requests) == 2


def test_import_upgrades_a_db_without_synced_at(server, tmp_path):
    db = tmp_path / "old" / "vault.db"
    db.parent.mkdir()
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE keys (kid TEXT, key TEXT, license_url TEXT, pssh TEXT, label TEXT, created_at TEXT, PRIMARY KEY (kid, license_url, pssh))")
    conn.execute("INSERT INTO keys VALUES (?, ?, 'generic', '', 'Ep1', '2026-10-25T00:00:00Z')", (KID, KEY))
    conn.commit()
    conn.close()

    totals = importer.import_to_server(str(db), service="Prime Video")
    assert totals["added"] == 1
    assert server.requests[0][1]["service"] == "primevideo"
