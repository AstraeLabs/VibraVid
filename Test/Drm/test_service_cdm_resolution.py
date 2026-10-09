# 09.10.26

import sys
from pathlib import Path

import pytest

workspace_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(workspace_root))

from VibraVid.core.drm import manager as drm_manager
from VibraVid.core.drm.manager import DRMManager
from VibraVid.setup import device_install
from VibraVid.setup.device_install import remote_cdm_system, resolve_service_cdm, resolve_service_cdm_paths
from VibraVid.utils import config_manager

DL_WIDEVINE = {"type": "decrypt_labs", "secret": "k", "device_name": "L3"}
DL_PLAYREADY = {"type": "decrypt_labs", "secret": "k", "device_name": "SL3"}
STOCK_WIDEVINE = {"device_type": "ANDROID", "system_id": 22594, "security_level": 3, "host": "http://h", "secret": "s", "device_name": "d"}
STOCK_PLAYREADY = {"host": "http://h/playready", "secret": "s", "device_name": "d", "security_level": 3000}

REGISTRY = {
    "remote_widevine_1": DL_WIDEVINE,
    "remote_playready_1": DL_PLAYREADY,
    "remote_widevine_2": STOCK_WIDEVINE,
    "remote_playready_2": STOCK_PLAYREADY,
    "tagged": {**STOCK_PLAYREADY, "system": "widevine"},
}


class FakeSearcher:
    def search(self, ext=None, filename=None):  # noqa: ARG002
        return f"/bin/{filename}" if filename in ("mine.wvd", "mine.prd") else None


@pytest.fixture
def setup_cdm(monkeypatch):
    def _apply(cdm_value):
        monkeypatch.setitem(config_manager._login_data, "mysite", {"cdm": cdm_value})
        monkeypatch.setattr(device_install, "get_remote_cdm_registry", lambda: REGISTRY)
        monkeypatch.setattr(device_install, "DeviceSearcher", FakeSearcher)

    return _apply


@pytest.mark.parametrize(
    ("cfg", "expected"),
    [
        (DL_WIDEVINE, "widevine"),
        ({"type": "decrypt_labs", "device_name": "ChromeCDM"}, "widevine"),
        (DL_PLAYREADY, "playready"),
        ({"type": "decrypt_labs", "device_type": "PLAYREADY", "device_name": "x"}, "playready"),
        (STOCK_WIDEVINE, "widevine"),
        (STOCK_PLAYREADY, "playready"),
        (REGISTRY["tagged"], "widevine"),  # the explicit "system" key wins
    ],
)
def test_remote_cdm_system_inference(cfg, expected):
    assert remote_cdm_system(cfg) == expected


def test_id_selects_the_remote_cdm_for_its_system(setup_cdm):
    setup_cdm("remote_widevine_1")
    resolved = resolve_service_cdm("mysite")
    assert resolved.widevine_remote == DL_WIDEVINE
    assert resolved.playready_remote is None and resolved.wvd_path is None and resolved.prd_path is None


def test_files_and_ids_can_be_mixed_and_system_key_is_not_forwarded(setup_cdm):
    setup_cdm(["mine.wvd", "remote_playready_2", "tagged"])
    resolved = resolve_service_cdm("mysite")
    assert resolved.wvd_path == "/bin/mine.wvd"
    assert resolved.playready_remote == STOCK_PLAYREADY
    assert resolved.widevine_remote == STOCK_PLAYREADY and "system" not in resolved.widevine_remote


def test_unknown_id_fails_with_the_known_ids(setup_cdm):
    setup_cdm("remote_widevine_9")
    with pytest.raises(FileNotFoundError, match=r"remote_widevine_9.*known ids: .*remote_widevine_1"):
        resolve_service_cdm("mysite")


def test_missing_device_file_still_fails(setup_cdm):
    setup_cdm("absent.wvd")
    with pytest.raises(FileNotFoundError, match="absent.wvd"):
        resolve_service_cdm("mysite")


def test_old_paths_function_ignores_ids_and_keeps_its_shape(setup_cdm):
    setup_cdm(["mine.prd", "remote_widevine_1"])
    assert resolve_service_cdm_paths("mysite") == (None, "/bin/mine.prd")
    assert resolve_service_cdm_paths(None) == (None, None)


@pytest.fixture
def manager_env(monkeypatch, setup_cdm):
    monkeypatch.setattr(DRMManager, "_build_vaults", staticmethod(lambda: []))
    monkeypatch.setattr(drm_manager, "get_wvd_path", lambda: "/default/default.wvd")
    monkeypatch.setattr(drm_manager, "get_prd_path", lambda: "/default/default.prd")
    monkeypatch.setattr(drm_manager, "resolve_service_cdm", device_install.resolve_service_cdm)
    return setup_cdm


def test_for_site_remote_id_prefers_remote_only_for_its_system(manager_env):
    manager_env("remote_widevine_1")
    mgr = DRMManager.for_site("mysite")
    assert mgr.widevine_remote_cdm_api == DL_WIDEVINE
    assert mgr.prefer_remote_widevine is True
    assert mgr.prefer_remote_playready is None
    assert mgr.playready_device_path == "/default/default.prd"


def test_for_site_device_file_prefers_local_even_if_global_prefers_remote(manager_env):
    manager_env("mine.wvd")
    mgr = DRMManager.for_site("mysite")
    assert mgr.widevine_device_path == "/bin/mine.wvd"
    assert mgr.prefer_remote_widevine is False


def test_for_site_without_cdm_entry_follows_the_global_flag(manager_env):
    manager_env(None)
    mgr = DRMManager.for_site("mysite")
    assert mgr.widevine_device_path == "/default/default.wvd"
    assert mgr.prefer_remote_widevine is None and mgr.prefer_remote_playready is None


@pytest.mark.usefixtures("manager_env")
def test_per_system_preference_reaches_the_key_functions(monkeypatch):
    seen = {}
    monkeypatch.setattr(drm_manager, "get_widevine_keys", lambda *_, **kw: seen.setdefault("wv", kw["prefer_remote_cdm"]) and None)
    monkeypatch.setattr(drm_manager, "get_playready_keys", lambda *_, **kw: seen.setdefault("pr", kw["prefer_remote_cdm"]) and None)
    monkeypatch.setattr(drm_manager, "USE_CDM", True)

    mgr = DRMManager(prefer_remote_cdm=False, prefer_remote_widevine=True)
    mgr.get_wv_keys([{"pssh": "p", "kid": "aa" * 16, "type": "video"}], "https://license.test/wv")
    mgr.get_pr_keys([{"pssh": "p", "kid": "bb" * 16, "type": "video"}], "https://license.test/pr")

    assert seen == {"wv": True, "pr": False}
