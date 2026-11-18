"""``wvd_dir`` / ``prd_dir`` in [DEFAULT]: empty keeps the layout derived from the binary folder."""

import os

import pytest

from VibraVid.setup import device_install
from VibraVid.setup.binary_paths import BinaryPaths


@pytest.fixture
def paths(tmp_path, monkeypatch):
    monkeypatch.delenv("VIBRAVID_BINARY_DIR", raising=False)
    monkeypatch.delenv("BINARY_DIR", raising=False)
    base = tmp_path / "binary" / "VibraVid"
    base.mkdir(parents=True)
    instance = BinaryPaths()
    monkeypatch.setattr(instance, "get_binary_directory", lambda: str(base))

    settings = {}
    from VibraVid.utils import config_manager

    monkeypatch.setattr(config_manager.config, "get", lambda section, key, data_type=str, default=None: settings.get(key, default))
    monkeypatch.setattr(config_manager, "base_path", str(tmp_path / "app"))
    instance.base, instance.settings = base, settings
    return instance


def test_empty_keeps_the_default_layout(paths):
    paths.settings.update(wvd_dir="  ")
    assert paths.get_tools_directory() == str(paths.base / "tools")
    assert paths.get_wvd_directory() == str(paths.base / "wvd")
    assert paths.get_prd_directory() == str(paths.base / "prd")


def test_each_folder_is_set_on_its_own(paths, tmp_path):
    paths.settings.update(prd_dir=str(tmp_path / "p"))
    assert paths.get_prd_directory() == str(tmp_path / "p")
    assert paths.get_wvd_directory() == str(paths.base / "wvd")


def test_relative_path_starts_from_the_program_folder_and_vars_are_expanded(paths, tmp_path, monkeypatch):
    monkeypatch.setenv("VV_TEST_ROOT", str(tmp_path / "root"))
    paths.settings.update(prd_dir="bin/prd", wvd_dir="$VV_TEST_ROOT/wvd")
    assert paths.get_prd_directory() == os.path.normpath(str(tmp_path / "app" / "bin" / "prd"))
    assert paths.get_wvd_directory() == os.path.normpath(str(tmp_path / "root" / "wvd"))


def test_devices_are_searched_in_the_configured_folders_with_sub_folders(paths, tmp_path, monkeypatch):
    wvd_root, prd_root = tmp_path / "devices" / "w", tmp_path / "devices" / "p"
    (wvd_root / "L3").mkdir(parents=True)
    (prd_root / "SL3000").mkdir(parents=True)
    wvd = wvd_root / "L3" / "a.wvd"
    prd = prd_root / "SL3000" / "b.prd"
    wvd.write_bytes(b"x")
    prd.write_bytes(b"x")
    paths.settings.update(wvd_dir=str(wvd_root), prd_dir=str(prd_root))

    monkeypatch.setattr(device_install, "binary_paths", paths)
    finder = device_install.DeviceSearcher()
    assert finder.search(".wvd") == str(wvd)
    assert finder.search(".prd") == str(prd)
