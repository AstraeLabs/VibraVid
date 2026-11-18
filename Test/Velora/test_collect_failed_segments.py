"""collect_failed_segments: a segment is failed when its file is missing or empty, whatever way the downloaded paths are spelled."""

from pathlib import Path

import pytest

from VibraVid.core.velora.util._stream_helpers import collect_failed_segments


def _segments(count: int, ext: str = "m4s") -> list[dict]:
    return [{"number": n, "url": f"http://cdn.example/v/{n}.{ext}"} for n in range(count)]


def _write(directory: Path, number: int, size: int) -> Path:
    path = directory / f"seg_{number:05d}.mp4"
    path.write_bytes(b"x" * size)
    return path


def _old_formula(dl_segs, downloaded_paths, stream_dir, ext):
    """The census as it was before the cached directory resolve."""
    downloaded = {str(p.resolve()).casefold() for p in downloaded_paths if p.exists() and p.stat().st_size > 0}
    return [
        (s["number"], s["url"])
        for s in dl_segs
        if str((Path(stream_dir) / f"seg_{s['number']:05d}.{ext}").resolve()).casefold() not in downloaded
    ]


def test_missing_and_empty_files_are_failed_and_good_ones_are_not(tmp_path):
    paths = [_write(tmp_path, 0, 10), _write(tmp_path, 1, 0), _write(tmp_path, 3, 10)]  # 2 never written

    failed = collect_failed_segments(_segments(4), paths, tmp_path, "mp4")

    assert [n for n, _ in failed] == [1, 2]


def _filesystem_ignores_case(directory: Path) -> bool:
    probe = directory / "CaseProbe.tmp"
    probe.write_bytes(b"")
    try:
        return (directory / "caseprobe.tmp").exists()
    finally:
        probe.unlink()


def test_a_dotdot_spelling_still_counts_as_downloaded(tmp_path):
    (tmp_path / "other").mkdir()
    spelled = tmp_path / "other" / ".." / "seg_00000.mp4"
    _write(tmp_path, 0, 10)

    assert collect_failed_segments(_segments(1), [spelled], tmp_path, "mp4") == []


def test_a_case_only_difference_counts_as_downloaded_where_the_filesystem_ignores_case(tmp_path):
    if not _filesystem_ignores_case(tmp_path):
        pytest.skip("this filesystem tells SEG_00001.MP4 from seg_00001.mp4")
    _write(tmp_path, 1, 10)
    shouting = tmp_path / "SEG_00001.MP4"

    assert collect_failed_segments(_segments(2)[1:], [shouting], tmp_path, "mp4") == []


def test_m4s_urls_are_looked_up_as_mp4_files(tmp_path):
    paths = [_write(tmp_path, n, 10) for n in range(3)]

    assert collect_failed_segments(_segments(3, "m4s"), paths, tmp_path, "mp4") == []


def test_paths_that_belong_to_no_segment_are_ignored(tmp_path):
    stray = tmp_path / "unrelated.bin"
    stray.write_bytes(b"x")

    assert [n for n, _ in collect_failed_segments(_segments(2), [stray], tmp_path, "mp4")] == [0, 1]


def test_same_answer_as_the_uncached_formula(tmp_path):
    sizes = {0: 5, 1: 0, 2: 5, 4: 5, 5: 0, 7: 5}  # 3 and 6 missing
    paths = [_write(tmp_path, n, size) for n, size in sizes.items()]
    segments = _segments(8)

    assert collect_failed_segments(segments, paths, tmp_path, "mp4") == _old_formula(segments, paths, tmp_path, "mp4")
