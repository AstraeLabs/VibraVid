# 17.10.26

import os
from pathlib import Path

import pytest

from VibraVid.setup import device_install
from VibraVid.setup.binary_paths import BinaryPaths


@pytest.fixture
def layout(tmp_path, monkeypatch):
    """``<tmp>/binary`` is the flat directory of the previous versions, ``<tmp>/binary/VibraVid`` the new one."""
    monkeypatch.delenv("VIBRAVID_BINARY_DIR", raising=False)
    monkeypatch.delenv("BINARY_DIR", raising=False)
    old_dir = tmp_path / "binary"
    new_dir = old_dir / "VibraVid"
    new_dir.mkdir(parents=True)

    paths = BinaryPaths()
    monkeypatch.setattr(paths, "get_binary_directory", lambda: str(new_dir))
    return paths, old_dir, new_dir


def _touch(path: Path, data: bytes = b"x") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def test_the_previous_flat_directory_is_the_parent(layout):
    paths, old_dir, _ = layout
    assert paths.get_legacy_binary_directory() == str(old_dir)


def test_no_fallback_directory_when_the_binary_dir_is_overridden(layout):
    paths, _, _ = layout
    paths.binary_dir_override = "/somewhere/else"
    assert paths.get_legacy_binary_directory() is None


def test_binary_is_found_in_tools_first_then_in_the_old_flat_directory(layout):
    paths, old_dir, new_dir = layout
    old = _touch(old_dir / "velora.exe")

    assert paths.get_binary_path("velora", "velora.exe") == str(old)

    paths.invalidate_binary("velora.exe")
    new = _touch(new_dir / "tools" / "velora.exe")
    assert paths.get_binary_path("velora", "velora.exe") == str(new)


def test_missing_binary_is_still_none(layout):
    paths, _, _ = layout
    assert paths.get_binary_path("flux", "flux.exe") is None


@pytest.fixture
def searcher(layout, monkeypatch):
    paths, old_dir, new_dir = layout
    monkeypatch.setattr(device_install, "binary_paths", paths)
    monkeypatch.setattr(paths, "ensure_binary_directory", lambda: str(new_dir))
    return device_install.DeviceSearcher(), old_dir, new_dir


def test_cdm_files_are_looked_up_in_wvd_and_prd(searcher):
    finder, _, new_dir = searcher
    wvd = _touch(new_dir / "wvd" / "device.wvd")
    prd = _touch(new_dir / "prd" / "device.prd")

    assert finder.search(".wvd") == str(wvd)
    assert finder.search(".prd") == str(prd)
    assert finder.search(filename="device.wvd") == str(wvd)


def test_cdm_files_left_in_the_old_flat_directory_are_still_used(searcher):
    finder, old_dir, _ = searcher
    wvd = _touch(old_dir / "device.wvd")
    prd = _touch(old_dir / "mine.prd")

    assert finder.search(".wvd") == str(wvd)
    assert finder.search(".prd") == str(prd)
    assert finder.search(filename="mine.prd") == str(prd)


def test_the_dedicated_directory_beats_the_old_one(searcher):
    finder, old_dir, new_dir = searcher
    _touch(old_dir / "device.wvd")
    new = _touch(new_dir / "wvd" / "device.wvd")

    assert os.path.samefile(finder.search(".wvd"), new)
