# 17.10.26

import importlib
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
    monkeypatch.setattr(importlib.import_module("VibraVid.setup.binary_paths"), "is_genuine_binary", lambda tool, path: True)

    paths = BinaryPaths()
    monkeypatch.setattr(paths, "get_binary_directory", lambda: str(new_dir))
    return paths, old_dir, new_dir


def _touch(path: Path, data: bytes = b"x") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def test_binary_is_only_looked_up_in_the_tools_directory(layout):
    paths, old_dir, new_dir = layout
    _touch(old_dir / "velora.exe")
    _touch(new_dir / "velora.exe")
    assert paths.get_binary_path("velora", "velora.exe") is None

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


def test_tool_version_is_read_from_the_tools_directory_only(layout):
    paths, old_dir, new_dir = layout
    (old_dir / ".versions.json").write_text('{"ffmpeg": "n9.0"}', encoding="utf-8")
    _touch(new_dir / "tools" / ".deno.version", b"2.0")
    assert paths.get_local_tool_version("ffmpeg") is None
    assert paths.get_local_tool_version("deno") is None

    paths.set_local_tool_version("ffmpeg", "n9.1")
    assert paths.get_local_tool_version("ffmpeg") == "n9.1"
    assert (new_dir / "tools" / ".versions.json").is_file()


def test_ffmpeg_below_the_minimum_size_is_not_used_and_not_kept_as_installed(layout, monkeypatch):
    paths, _, new_dir = layout
    _touch(new_dir / "tools" / "ffmpeg.exe", b"version https://git-lfs.github.com/spec/v1")

    assert paths.get_binary_path("ffmpeg", "ffmpeg.exe") is None

    monkeypatch.setattr(paths, "_load_paths_json", lambda: {})
    assert paths.download_binary("ffmpeg", "ffmpeg.exe") is None


def test_small_binary_of_another_tool_is_still_accepted(layout):
    paths, _, new_dir = layout
    small = _touch(new_dir / "tools" / "mp4decrypt.exe", b"x" * 366_080)
    assert paths.get_binary_path("mp4decrypt", "mp4decrypt.exe") == str(small)


def test_auto_update_only_covers_the_tools_directory(layout, monkeypatch):
    from VibraVid.utils.upload import update as updater

    paths, old_dir, new_dir = layout
    monkeypatch.setattr(updater, "binary_paths", paths)
    monkeypatch.setattr(paths, "system", "windows")
    _touch(old_dir / "flux.exe")
    _touch(new_dir / "tools" / "velora.exe")

    assert updater._installed_in_managed_dir(["velora"], "velora")
    assert not updater._installed_in_managed_dir(["flux"], "flux")
    assert not updater._installed_in_managed_dir(["deno"], "deno")
