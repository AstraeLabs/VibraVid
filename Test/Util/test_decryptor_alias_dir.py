# 06.10.26

"""ANSI-unsafe paths get an alias for the decrypt tool; the alias must live on the same disk as the file (a 30 GB copy otherwise)."""

import os
import tempfile

import pytest

from VibraVid.core.decryptor import decryptor


def test_alias_dir_is_a_scratch_folder_on_the_same_drive(monkeypatch, tmp_path):
    made = []
    monkeypatch.setattr(decryptor.os, "name", "nt")
    monkeypatch.setattr(decryptor.os, "makedirs", lambda path, exist_ok=False: made.append(path))
    monkeypatch.setattr(decryptor.os, "access", lambda path, mode: True)
    monkeypatch.setattr(decryptor.os.path, "splitdrive", lambda path: ("D:", path[2:]))

    folder = decryptor._alias_dir(r"D:\Downloads\タイトル.mkv")

    assert folder == "D:" + os.sep + ".vv_tmp" and made == [folder]


@pytest.mark.parametrize("drive", ["", r"\server\share"])
def test_alias_dir_falls_back_to_the_system_temp_without_a_drive_letter(monkeypatch, drive):
    monkeypatch.setattr(decryptor.os, "name", "nt")
    monkeypatch.setattr(decryptor.os.path, "splitdrive", lambda path: (drive, path))

    assert decryptor._alias_dir(r"\server\share\x.mkv") == tempfile.gettempdir()


def test_alias_dir_falls_back_when_the_scratch_folder_cannot_be_created(monkeypatch):
    def refuse(path, exist_ok=False):
        raise PermissionError(path)

    monkeypatch.setattr(decryptor.os, "name", "nt")
    monkeypatch.setattr(decryptor.os, "makedirs", refuse)
    monkeypatch.setattr(decryptor.os.path, "splitdrive", lambda path: ("C:", path[2:]))

    assert decryptor._alias_dir(r"C:\x\y.mkv") == tempfile.gettempdir()


def test_alias_dir_is_the_system_temp_off_windows(monkeypatch):
    monkeypatch.setattr(decryptor.os, "name", "posix")

    assert decryptor._alias_dir("/data/x.mkv") == tempfile.gettempdir()


def test_guard_puts_both_aliases_next_to_the_files_and_moves_the_output_back(monkeypatch, tmp_path):
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    source = tmp_path / "in.mp4"
    source.write_bytes(b"encrypted")
    target = tmp_path / "out.mp4"
    monkeypatch.setattr(decryptor, "_ansi_encodable", lambda path: False)
    monkeypatch.setattr(decryptor, "_alias_dir", lambda path: str(scratch))

    with decryptor._AnsiSafePathGuard(str(source), str(target)) as guard:
        assert os.path.dirname(guard.safe_encrypted_path) == str(scratch)
        assert os.path.dirname(guard.safe_output_path) == str(scratch)
        assert open(guard.safe_encrypted_path, "rb").read() == b"encrypted"
        open(guard.safe_output_path, "wb").write(b"decrypted")
        guard.finalize()

    assert target.read_bytes() == b"decrypted"
    assert list(scratch.iterdir()) == []
