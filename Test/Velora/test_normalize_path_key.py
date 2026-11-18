"""normalize_path_key: same key for every spelling of a path, and the directory is resolved once, not once per file."""

import os
from pathlib import Path

import pytest

from VibraVid.core.velora.util import formatting
from VibraVid.core.velora.util.formatting import normalize_path_key


@pytest.fixture(autouse=True)
def _fresh_cache():
    formatting._resolved_dir.cache_clear()
    yield
    formatting._resolved_dir.cache_clear()


def test_empty_path_has_an_empty_key():
    assert normalize_path_key("") == ""


def test_case_and_dotdot_do_not_change_the_key(tmp_path):
    (tmp_path / "other").mkdir()
    plain = normalize_path_key(str(tmp_path / "seg_00001.mp4"))
    assert normalize_path_key(str(tmp_path / "SEG_00001.MP4")) == plain
    assert normalize_path_key(str(tmp_path / "other" / ".." / "seg_00001.mp4")) == plain


def test_relative_path_follows_the_current_directory(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert normalize_path_key("seg_00001.mp4") == normalize_path_key(str(tmp_path / "seg_00001.mp4"))
    other = tmp_path / "sub"
    other.mkdir()
    monkeypatch.chdir(other)
    assert normalize_path_key("seg_00001.mp4") == normalize_path_key(str(other / "seg_00001.mp4"))


def test_dot_names_take_the_full_resolve(tmp_path):
    (tmp_path / "sub").mkdir()
    assert normalize_path_key(str(tmp_path / "sub" / "..")) == normalize_path_key(str(tmp_path))


def test_symlinked_directory_gives_the_key_of_its_target(tmp_path):
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "link"
    try:
        os.symlink(real, link, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks not available")
    assert normalize_path_key(str(link / "seg_00001.mp4")) == normalize_path_key(str(real / "seg_00001.mp4"))


def test_a_directory_is_resolved_once_for_all_its_files(tmp_path, monkeypatch):
    seen = []
    real_resolve = Path.resolve

    def counting(self, *args, **kwargs):
        seen.append(str(self))
        return real_resolve(self, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", counting)
    keys = [normalize_path_key(str(tmp_path / f"seg_{i:05d}.mp4")) for i in range(50)]
    assert len(set(keys)) == 50
    assert [p for p in seen if p.startswith(str(tmp_path))] == [str(tmp_path)]
