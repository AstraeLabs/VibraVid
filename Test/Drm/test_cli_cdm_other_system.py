# 24.11.26

import sys
from pathlib import Path
from types import SimpleNamespace

workspace_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(workspace_root))

from VibraVid.core.downloader import drm_keys
from VibraVid.core.drm.manager import DRMManager
from VibraVid.core.drm.system import DRMType
from VibraVid.services.plutotv import downloader as pluto


def _manager(wv_origin, pr_origin, wv_remote=None, prd="/bin/tv_sl3000.prd"):
    manager = DRMManager.__new__(DRMManager)  # skip the vault setup
    manager.widevine_device_path, manager.playready_device_path = None, prd
    manager.widevine_remote_cdm_api, manager.playready_remote_cdm_api = wv_remote, None
    manager.widevine_local_cdm = manager.playready_local_cdm = None
    manager.widevine_cdm_origin, manager.playready_cdm_origin = wv_origin, pr_origin
    return manager


def _printed(monkeypatch):
    lines = []
    monkeypatch.setattr(drm_keys.console, "print", lambda *a, **_k: lines.append(" ".join(map(str, a))))
    return lines


def test_warns_when_cli_cdm_only_covers_the_other_system(monkeypatch):
    lines = _printed(monkeypatch)
    manager = _manager("--cdm", "search", wv_remote={"id": "analchrome"})
    drm_keys.warn_unused_cli_cdm(manager, DRMType.PLAYREADY)
    drm_keys.warn_unused_cli_cdm(manager, DRMType.PLAYREADY)  # once per manager
    assert len(lines) == 1
    assert "analchrome covers Widevine only" in lines[0] and "uses PlayReady" in lines[0] and "tv_sl3000.prd" in lines[0]


def test_no_warning_when_the_cli_cdm_is_used_or_absent(monkeypatch):
    lines = _printed(monkeypatch)
    drm_keys.warn_unused_cli_cdm(_manager("--cdm", "search", wv_remote={"id": "analchrome"}), DRMType.WIDEVINE)
    drm_keys.warn_unused_cli_cdm(_manager("search", "search"), DRMType.PLAYREADY)
    drm_keys.warn_unused_cli_cdm(_manager("login.toml 'x'", "search"), DRMType.PLAYREADY)
    assert lines == []


def test_pluto_picks_widevine_only_when_widevine_cdm_is_chosen_on_purpose(monkeypatch):
    def use(wv, pr):
        monkeypatch.setattr(pluto, "resolve_cdm", lambda _site: SimpleNamespace(widevine_origin=wv, playready_origin=pr))
        return pluto._pick_drm()

    assert use("--cdm", "search") == DRMType.WIDEVINE
    assert use("login.toml 'plutotv'", "search") == DRMType.WIDEVINE
    assert use("search", "search") == DRMType.PLAYREADY
    assert use("--cdm", "--cdm") == DRMType.PLAYREADY
    assert use("search", "config.toml [DRM]") == DRMType.PLAYREADY
