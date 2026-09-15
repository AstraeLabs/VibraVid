# 15.09.26

import os
import time

from VibraVid.utils import os_manager


def _make_dir_with_files(base_dir, name, count=200):
    d = os.path.join(base_dir, name)
    os.makedirs(d)
    for i in range(count):
        with open(os.path.join(d, f"seg_{i:05d}.mp4"), "wb") as f:
            f.write(b"x")
    return d


def _wait_until_gone(path, timeout=10.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if not os.path.exists(path):
            return True
        time.sleep(0.1)
    return not os.path.exists(path)


def test_fast_rmtree_removes_source_path_immediately(tmp_path):
    d = _make_dir_with_files(str(tmp_path), "dash_temp")
    os_manager.fast_rmtree(d)
    assert not os.path.exists(d)


def test_fast_rmtree_eventually_deletes_renamed_copy(tmp_path):
    d = _make_dir_with_files(str(tmp_path), "dash_temp")
    os_manager.fast_rmtree(d)
    trash_entries = [p for p in os.listdir(str(tmp_path)) if p.startswith("dash_temp.trash-")]
    assert trash_entries, "expected a renamed .trash- copy to exist right after fast_rmtree returns"
    assert _wait_until_gone(os.path.join(str(tmp_path), trash_entries[0]))


def test_fast_rmtree_falls_back_to_sync_delete_on_rename_failure(tmp_path, monkeypatch):
    d = _make_dir_with_files(str(tmp_path), "dash_temp")

    def _raise(*args, **kwargs):
        raise OSError("cross-device rename not supported")

    monkeypatch.setattr(os, "rename", _raise)
    os_manager.fast_rmtree(d)
    assert not os.path.exists(d)


def test_fast_rmtree_missing_path_is_a_noop(tmp_path):
    os_manager.fast_rmtree(str(tmp_path / "does-not-exist"))


def test_fast_rmtree_empty_path_is_a_noop():
    os_manager.fast_rmtree("")
