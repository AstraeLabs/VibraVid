"""_replace_segment_file: the segment must exist at every instant of the swap.

The failed-segment census (collect_failed_segments) runs while the decrypt workers are still replacing the downloaded segments with their decrypted copies. A swap that removes the target first leaves a window where a perfectly good segment looks missing: the census then waits 4 s and downloads it again.
"""

from pathlib import Path

import pytest

from VibraVid.core.velora._decrypt_pipeline import segment_io
from VibraVid.core.velora._decrypt_pipeline.segment_io import SegmentIOMixin


class _Host(SegmentIOMixin):
    pass


def _pair(tmp_path: Path) -> tuple[Path, Path]:
    target, source = tmp_path / "seg_00064.mp4", tmp_path / "seg_00064.mp4.dec"
    target.write_bytes(b"encrypted")
    source.write_bytes(b"clear")
    return source, target


def test_the_target_is_never_removed_before_the_swap(tmp_path, monkeypatch):
    source, target = _pair(tmp_path)
    present_at_swap = []
    real_replace = Path.replace

    def replace(self, destination):
        present_at_swap.append(Path(destination).exists())
        return real_replace(self, destination)

    def no_unlink(self, *args, **kwargs):
        pytest.fail("the target was unlinked before the atomic replace")

    monkeypatch.setattr(Path, "replace", replace)
    monkeypatch.setattr(Path, "unlink", no_unlink)

    _Host()._replace_segment_file(source, target, "test")

    assert present_at_swap == [True]
    assert target.read_bytes() == b"clear"
    assert not source.exists()


def test_a_locked_target_is_retried_after_removing_it(tmp_path, monkeypatch):
    source, target = _pair(tmp_path)
    present_at_attempt = []
    real_replace = Path.replace

    def locked_once(self, destination):
        present_at_attempt.append(Path(destination).exists())
        if len(present_at_attempt) == 1:
            raise PermissionError(13, "locked by an antivirus scan")
        return real_replace(self, destination)

    monkeypatch.setattr(Path, "replace", locked_once)
    monkeypatch.setattr(segment_io.time, "sleep", lambda _s: None)

    _Host()._replace_segment_file(source, target, "test")

    assert present_at_attempt == [True, False]  # second attempt: the old copy was removed first
    assert target.read_bytes() == b"clear"


def test_a_target_that_does_not_exist_yet_is_just_created(tmp_path):
    source = tmp_path / "seg_00001.mp4.dec"
    target = tmp_path / "seg_00001.mp4"
    source.write_bytes(b"clear")

    _Host()._replace_segment_file(source, target, "test")

    assert target.read_bytes() == b"clear"
    assert not source.exists()


def test_a_non_permission_error_is_raised_at_once(tmp_path, monkeypatch):
    source, target = _pair(tmp_path)
    calls = []

    def broken(self, destination):
        calls.append(1)
        raise FileNotFoundError(2, "gone")

    monkeypatch.setattr(Path, "replace", broken)
    monkeypatch.setattr(segment_io.time, "sleep", lambda _s: None)

    with pytest.raises(FileNotFoundError):
        _Host()._replace_segment_file(source, target, "test")
    assert len(calls) == 1


def test_a_rewrite_never_empties_the_segment(tmp_path, monkeypatch):
    target = tmp_path / "seg_00064.mp4"
    target.write_bytes(b"old bytes")
    seen_at_swap = []
    real_replace = Path.replace

    def replace(self, destination):
        seen_at_swap.append((Path(destination).read_bytes(), self.read_bytes()))
        return real_replace(self, destination)

    monkeypatch.setattr(Path, "replace", replace)

    _Host()._write_segment_file(target, b"new bytes", "test")

    assert seen_at_swap == [(b"old bytes", b"new bytes")]  # until the swap the original is whole
    assert target.read_bytes() == b"new bytes"
    assert [p.name for p in tmp_path.iterdir()] == ["seg_00064.mp4"]


def test_a_failed_rewrite_keeps_the_original_and_leaves_no_temp_file(tmp_path, monkeypatch):
    target = tmp_path / "seg_00064.mp4"
    target.write_bytes(b"old bytes")

    def broken_swap(self, source_path, target_path, reason):
        raise OSError(28, "no space left")

    monkeypatch.setattr(SegmentIOMixin, "_replace_segment_file", broken_swap)

    with pytest.raises(OSError):
        _Host()._write_segment_file(target, b"new bytes", "test")

    assert target.read_bytes() == b"old bytes"
    assert [p.name for p in tmp_path.iterdir()] == ["seg_00064.mp4"]
