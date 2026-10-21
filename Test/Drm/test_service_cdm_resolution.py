# 09.10.26

import sys
from pathlib import Path

import pytest

workspace_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(workspace_root))

from VibraVid.core.drm import manager as drm_manager
from VibraVid.core.drm.manager import DRMManager
from VibraVid.setup import device_install, system
from VibraVid.setup.device_install import remote_cdm_system, resolve_cdm
from VibraVid.utils import config_manager

DL_WIDEVINE = {"type": "custom_api_1", "secret": "k", "device_name": "L3"}
DL_PLAYREADY = {"type": "custom_api_1", "secret": "k", "device_name": "SL3"}
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
    def _apply(service_cdm=None, global_cdm="search", registry=None):
        monkeypatch.setitem(config_manager._login_data, "mysite", {"cdm": service_cdm})
        monkeypatch.setitem(config_manager._config_data, "DRM", {"cdm": global_cdm})
        monkeypatch.setattr(device_install, "get_remote_cdm_registry", lambda: REGISTRY if registry is None else registry)
        monkeypatch.setattr(device_install, "DeviceSearcher", FakeSearcher)
        monkeypatch.setattr(system, "get_wvd_path", lambda: "/default/default.wvd")
        monkeypatch.setattr(system, "get_prd_path", lambda: "/default/default.prd")

    return _apply


@pytest.mark.parametrize(
    ("cfg", "expected"),
    [
        (DL_WIDEVINE, "widevine"),
        ({"type": "custom_api_1", "device_name": "ChromeCDM"}, "widevine"),
        (DL_PLAYREADY, "playready"),
        ({"type": "custom_api_1", "device_type": "PLAYREADY", "device_name": "x"}, "playready"),
        (STOCK_WIDEVINE, "widevine"),
        (STOCK_PLAYREADY, "playready"),
        (REGISTRY["tagged"], "widevine"),  # the explicit "system" key wins
    ],
)
def test_remote_cdm_system_inference(cfg, expected):
    assert remote_cdm_system(cfg) == expected


def test_id_selects_the_remote_cdm_for_its_system(setup_cdm):
    setup_cdm("remote_widevine_1")
    resolved = resolve_cdm("mysite")
    assert resolved.widevine_remote == DL_WIDEVINE
    assert resolved.wvd_path is None  # the remote CDM replaces the local file for that system
    assert resolved.prd_path == "/default/default.prd" and resolved.playready_remote is None


def test_files_and_ids_can_be_mixed_and_system_key_is_not_forwarded(setup_cdm):
    setup_cdm(["mine.wvd", "remote_playready_2", "tagged"])
    resolved = resolve_cdm("mysite")
    assert resolved.wvd_path == "/bin/mine.wvd"
    assert resolved.playready_remote == STOCK_PLAYREADY
    assert resolved.widevine_remote is None  # "tagged" is also Widevine but the file came first
    assert "system" not in resolved.playready_remote


def test_system_tag_is_not_forwarded_to_the_remote_cdm(setup_cdm):
    setup_cdm("tagged")
    resolved = resolve_cdm("mysite")
    assert resolved.widevine_remote == STOCK_PLAYREADY and "system" not in resolved.widevine_remote


def test_unknown_id_fails_with_the_known_ids(setup_cdm):
    setup_cdm("remote_widevine_9")
    with pytest.raises(FileNotFoundError, match=r"remote_widevine_9.*known ids: .*remote_widevine_1"):
        resolve_cdm("mysite")


def test_missing_device_file_still_fails(setup_cdm):
    setup_cdm("absent.wvd")
    with pytest.raises(FileNotFoundError, match="absent.wvd"):
        resolve_cdm("mysite")


def test_empty_and_default_defer_to_the_global_cdm(setup_cdm):
    for service_value in (None, "", "default", ["default"]):
        setup_cdm(service_value, global_cdm="remote_widevine_1")
        assert resolve_cdm("mysite").widevine_remote == DL_WIDEVINE


def test_global_cdm_without_site_and_for_a_system_the_service_did_not_cover(setup_cdm):
    setup_cdm("mine.wvd", global_cdm=["remote_widevine_1", "remote_playready_1"])
    resolved = resolve_cdm("mysite")
    assert resolved.wvd_path == "/bin/mine.wvd" and resolved.widevine_remote is None  # the service wins for Widevine
    assert resolved.playready_remote == DL_PLAYREADY  # PlayReady falls through to [DRM].cdm
    assert resolve_cdm(None).widevine_remote == DL_WIDEVINE


def test_search_is_the_first_file_found_for_both_systems(setup_cdm):
    setup_cdm("search", global_cdm="remote_widevine_1")
    resolved = resolve_cdm("mysite")
    assert (resolved.wvd_path, resolved.prd_path) == ("/default/default.wvd", "/default/default.prd")
    assert resolved.widevine_remote is None and resolved.playready_remote is None


def test_unset_global_cdm_means_search(setup_cdm):
    setup_cdm(None, global_cdm=None)
    resolved = resolve_cdm("mysite")
    assert (resolved.wvd_path, resolved.prd_path) == ("/default/default.wvd", "/default/default.prd")


def test_reserved_remote_ids_are_rejected(setup_cdm):
    setup_cdm("search", registry={"Search": DL_WIDEVINE})
    with pytest.raises(ValueError, match="reserved"):
        resolve_cdm("mysite")


QUALITY_RULES = [{"min_height": 1080, "cdm": "remote_widevine_1"}, {"cdm": "mine.wvd"}]


@pytest.mark.parametrize(
    ("height", "remote", "path"),
    [(2160, DL_WIDEVINE, None), (1080, DL_WIDEVINE, None), (1079, None, "/bin/mine.wvd"), (720, None, "/bin/mine.wvd"), (0, None, "/bin/mine.wvd"), (None, None, "/bin/mine.wvd")],
)
def test_quality_rules_pick_by_the_selected_video_height(setup_cdm, height, remote, path):
    setup_cdm(QUALITY_RULES)
    resolved = resolve_cdm("mysite", height)
    assert resolved.widevine_remote == remote
    assert resolved.wvd_path == path


def test_max_height_is_inclusive_and_uncovered_heights_fall_through(setup_cdm):
    setup_cdm([{"max_height": 720, "cdm": "mine.wvd"}], global_cdm="remote_widevine_1")
    assert resolve_cdm("mysite", 720).wvd_path == "/bin/mine.wvd"
    assert resolve_cdm("mysite", 721).widevine_remote == DL_WIDEVINE


def test_a_rule_can_hold_a_list_for_both_systems(setup_cdm):
    setup_cdm([{"min_height": 1080, "cdm": ["remote_widevine_1", "remote_playready_1"]}, {"cdm": ["mine.wvd", "mine.prd"]}])
    hd = resolve_cdm("mysite", 1080)
    assert (hd.widevine_remote, hd.playready_remote) == (DL_WIDEVINE, DL_PLAYREADY)
    sd = resolve_cdm("mysite", 480)
    assert (sd.wvd_path, sd.prd_path) == ("/bin/mine.wvd", "/bin/mine.prd")


@pytest.fixture
def manager_env(monkeypatch, setup_cdm):
    monkeypatch.setattr(DRMManager, "_build_vaults", staticmethod(lambda: []))
    return setup_cdm


def test_for_site_remote_id_replaces_the_local_device_for_its_system(manager_env):
    manager_env("remote_widevine_1")
    mgr = DRMManager.for_site("mysite")
    assert mgr.widevine_remote_cdm_api == DL_WIDEVINE and mgr.widevine_device_path is None
    assert mgr.playready_device_path == "/default/default.prd"


def test_for_site_device_file(manager_env):
    manager_env("mine.wvd")
    mgr = DRMManager.for_site("mysite")
    assert mgr.widevine_device_path == "/bin/mine.wvd" and not mgr.widevine_remote_cdm_api


def test_select_cdm_swaps_the_cdm_for_the_selected_height(manager_env):
    manager_env(QUALITY_RULES)
    mgr = DRMManager.for_site("mysite")
    assert mgr.widevine_device_path == "/bin/mine.wvd"  # height not known yet
    mgr.select_cdm(1080)
    assert mgr.widevine_remote_cdm_api == DL_WIDEVINE and mgr.widevine_device_path is None
    mgr.select_cdm(480)
    assert mgr.widevine_device_path == "/bin/mine.wvd" and not mgr.widevine_remote_cdm_api


def test_select_cdm_leaves_a_plain_manager_alone(manager_env):
    manager_env(QUALITY_RULES)
    mgr = DRMManager(widevine_device_path="/mine/other.wvd")
    mgr.select_cdm(1080)
    assert mgr.widevine_device_path == "/mine/other.wvd" and mgr.widevine_remote_cdm_api is None


@pytest.mark.usefixtures("manager_env")
def test_cdm_spec_carries_the_device_and_the_remote(monkeypatch):
    mgr = DRMManager(widevine_device_path="/a.wvd", playready_remote_cdm_api=DL_PLAYREADY)
    _, wv = mgr.cdm_spec("widevine")
    _, pr = mgr.cdm_spec("playready")
    assert wv["cdm_device_path"] == "/a.wvd" and "prefer_remote_cdm" not in wv
    assert pr["cdm_remote_api"] == DL_PLAYREADY and "prefer_remote_cdm" not in pr


@pytest.mark.parametrize(
    ("cfg", "expected"),
    [
        (DL_WIDEVINE, {"name": "L3", "type": "ANDROID", "security_level": 3}),
        ({"type": "custom_api_1", "device_name": "L1"}, {"name": "L1", "type": "ANDROID", "security_level": 1}),
        ({"type": "custom_api_1", "device_name": "ChromeCDM"}, {"name": "ChromeCDM", "type": "CHROME", "security_level": None}),
        (STOCK_WIDEVINE, {"name": "d", "type": "ANDROID", "security_level": 3}),
        ({**STOCK_WIDEVINE, "device_type": "chrome"}, {"name": "d", "type": "CHROME", "security_level": 3}),
        (DL_PLAYREADY, {"name": "SL3", "type": None, "security_level": 3000}),
        (STOCK_PLAYREADY, {"name": "d", "type": None, "security_level": 3000}),
    ],
)
def test_describe_remote_cdm(cfg, expected):
    assert device_install.describe_remote_cdm(cfg) == expected


def fake_devices(monkeypatch):
    monkeypatch.setattr(system, "read_device_wvd", lambda path: {"system": "widevine", "path": path, "type": "CHROME", "security_level": 3})
    monkeypatch.setattr(system, "read_device_prd", lambda path: {"system": "playready", "path": path, "security_level": 3000})


def test_service_info_for_local_devices(setup_cdm, monkeypatch):
    setup_cdm(["mine.wvd", "mine.prd"])
    fake_devices(monkeypatch)
    info = system.resolve_service_cdm_info("mysite")
    assert info["widevine"] == {"system": "widevine", "path": "/bin/mine.wvd", "type": "CHROME", "security_level": 3, "remote": False}
    assert info["playready"]["remote"] is False and info["playready"]["path"] == "/bin/mine.prd"


def test_service_info_for_a_remote_cdm(setup_cdm, monkeypatch):
    registry = {"remote_chrome": {"type": "custom_api_1", "secret": "k", "device_name": "ChromeCDM"}}
    setup_cdm("remote_chrome", registry=registry)
    fake_devices(monkeypatch)
    widevine = system.resolve_service_cdm_info("mysite")["widevine"]
    assert widevine == {"system": "widevine", "path": None, "remote": True, "name": "ChromeCDM", "type": "CHROME", "security_level": None}


def test_service_info_follows_the_quality_rules(setup_cdm, monkeypatch):
    setup_cdm([{"min_height": 1080, "cdm": "remote_widevine_1"}, {"cdm": "mine.wvd"}])
    fake_devices(monkeypatch)
    assert system.resolve_service_cdm_info("mysite", 1080)["widevine"]["remote"] is True
    assert system.resolve_service_cdm_info("mysite", 720)["widevine"]["remote"] is False


def test_service_info_reports_an_unreadable_device(setup_cdm, monkeypatch):
    setup_cdm("mine.wvd")

    def broken(path):
        raise ValueError("not a wvd")

    monkeypatch.setattr(system, "read_device_wvd", broken)
    monkeypatch.setattr(system, "read_device_prd", lambda path: None)
    assert system.resolve_service_cdm_info("mysite")["widevine"] == {"path": "/bin/mine.wvd", "remote": False, "error": "not a wvd"}


def make_searcher(tmp_path):
    legacy = tmp_path / "binary"
    managed = legacy / "VibraVid"
    (managed / "wvd").mkdir(parents=True)
    searcher = object.__new__(device_install.DeviceSearcher)
    searcher.base_dir = str(managed)
    searcher.previous_dir = str(legacy)
    searcher.wvd_dir = str(managed / "wvd")
    searcher.prd_dir = str(managed / "prd")
    searcher.tools_dir = str(managed / "tools")
    searcher.cdm_dir = str(managed)
    return searcher, legacy, managed


def test_named_device_is_found_in_any_subfolder_of_the_binary_folder(tmp_path):
    searcher, legacy, _managed = make_searcher(tmp_path)
    nested = legacy / "DEVICE" / "L1"
    nested.mkdir(parents=True)
    (nested / "tv_l1.wvd").write_bytes(b"x")
    assert searcher.search(filename="tv_l1.wvd") == str(nested / "tv_l1.wvd")
    assert searcher.search(filename="missing.wvd") is None


def test_named_device_in_the_managed_folder_wins_over_a_copy_in_a_subfolder(tmp_path):
    searcher, legacy, managed = make_searcher(tmp_path)
    (managed / "wvd" / "phone.wvd").write_bytes(b"x")
    other = legacy / "DEVICE" / "L3"
    other.mkdir(parents=True)
    (other / "phone.wvd").write_bytes(b"x")
    assert searcher.search(filename="phone.wvd") == str(managed / "wvd" / "phone.wvd")
