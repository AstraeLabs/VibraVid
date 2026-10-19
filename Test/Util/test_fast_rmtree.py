# 15.09.26

import os
import sys
import time

import pytest

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


@pytest.mark.skipif(sys.platform != "win32", reason="UNC paths are a Windows thing")
def test_fast_rmtree_deletes_with_a_unc_working_directory(tmp_path, monkeypatch):
    """A NAS working directory made the detached `cmd /c rd` exit without deleting anything."""
    drive, rest = os.path.splitdrive(str(tmp_path))
    unc = "\\\\localhost\\" + drive[0] + "$" + rest
    if not os.path.isdir(unc):
        pytest.skip("administrative share not available")

    d = _make_dir_with_files(unc, "hls_temp")
    monkeypatch.chdir(unc)
    os_manager.fast_rmtree(os.path.relpath(d, unc))

    assert not os.path.exists(d)
    assert _wait_until_gone(next(os.path.join(unc, p) for p in os.listdir(unc) if p.startswith("hls_temp.trash-")))


def _make_readonly_file(base_dir, name):
    import stat

    d = os.path.join(base_dir, name)
    os.makedirs(d)
    f = os.path.join(d, "locked.mp4")
    with open(f, "wb") as fh:
        fh.write(b"x")
    os.chmod(f, stat.S_IREAD)
    return d, f


@pytest.mark.skipif(sys.platform != "win32", reason="read-only files block rmtree on Windows only")
def test_rmtree_logged_removes_read_only_files(tmp_path):
    d, _ = _make_readonly_file(str(tmp_path), "ro_temp")
    assert os_manager._rmtree_logged(d)
    assert not os.path.exists(d)


def test_rmtree_logged_reports_what_it_could_not_delete(tmp_path, monkeypatch, caplog):
    d, f = _make_readonly_file(str(tmp_path), "locked_temp")

    def _deny(path, *args, **kwargs):
        raise PermissionError(13, "Access is denied", path)

    monkeypatch.setattr(os, "remove", _deny)
    monkeypatch.setattr(os, "unlink", _deny)
    with caplog.at_level("WARNING"):
        assert not os_manager._rmtree_logged(d)

    assert any("locked.mp4" in r.getMessage() and "could not delete" in r.getMessage() for r in caplog.records)


def test_extended_path_only_changes_long_windows_paths(monkeypatch):
    monkeypatch.setattr(os_manager, "system", "windows")
    assert os_manager._extended_path(r"C:\short") == r"C:\short"
    long_drive = "C:" + "\\" + "a" * 300
    assert os_manager._extended_path(long_drive) == "\\\\?\\" + long_drive
    long_unc = "\\\\nas\\share\\" + "a" * 300
    assert os_manager._extended_path(long_unc) == "\\\\?\\UNC\\nas\\share\\" + "a" * 300
    assert os_manager._extended_path("\\\\?\\" + long_drive) == "\\\\?\\" + long_drive

    monkeypatch.setattr(os_manager, "system", "linux")
    assert os_manager._extended_path(long_drive) == long_drive


def test_sweep_trash_removes_only_old_foreign_trash(tmp_path):
    parent = str(tmp_path)
    old = _make_dir_with_files(parent, ".ep1_hls_temp.trash-1-abcdef01", count=3)
    own = _make_dir_with_files(parent, f".ep2_hls_temp.trash-{os.getpid()}-abcdef02", count=3)
    young = _make_dir_with_files(parent, ".ep3_hls_temp.trash-1-abcdef03", count=3)
    plain = _make_dir_with_files(parent, ".ep4_hls_temp", count=3)
    os.utime(old, (0, 0))
    os.utime(own, (0, 0))
    os.utime(plain, (0, 0))

    os_manager.sweep_trash(parent)

    assert not os.path.exists(old)
    assert os.path.exists(own), "this process's own trash belongs to its detached delete"
    assert os.path.exists(young), "a recently touched trash folder may still be in use"
    assert os.path.exists(plain), "only *.trash-* folders are swept"


def test_sweep_trash_missing_or_empty_parent_is_a_noop(tmp_path):
    os_manager.sweep_trash(str(tmp_path / "does-not-exist"))
    os_manager.sweep_trash("")


def test_in_kill_on_close_job_never_raises():
    assert isinstance(os_manager._in_kill_on_close_job(), bool)


@pytest.mark.skipif(sys.platform != "win32", reason="Job objects are a Windows thing")
def test_delete_detached_falls_back_when_breakaway_is_refused(tmp_path, monkeypatch):
    import subprocess

    d = _make_dir_with_files(str(tmp_path), "job_temp", count=5)
    real_popen = subprocess.Popen
    flags_seen = []

    def _popen(cmd, **kwargs):
        flags_seen.append(kwargs["creationflags"])
        if kwargs["creationflags"] & subprocess.CREATE_BREAKAWAY_FROM_JOB:
            raise PermissionError(5, "Access is denied")
        return real_popen(cmd, **kwargs)

    monkeypatch.setattr(subprocess, "Popen", _popen)
    os_manager._delete_detached(d)

    assert len(flags_seen) == 2
    assert _wait_until_gone(d)
