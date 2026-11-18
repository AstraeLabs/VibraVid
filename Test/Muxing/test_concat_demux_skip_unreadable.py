# 13.10.26
# ruff: noqa: E402

import subprocess
import sys
from pathlib import Path

import pytest

workspace_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(workspace_root))

from VibraVid.core.muxing.helper.video.merge import concat_demux_merge_segments
from VibraVid.setup import get_ffmpeg_path, get_ffprobe_path


@pytest.fixture(scope="module")
def have_ffmpeg():
    try:
        available = bool(get_ffmpeg_path() and get_ffprobe_path())
    except Exception:
        available = False
    if not available:
        pytest.skip("ffmpeg/ffprobe not available")


def _make_ts(path: Path, seed: int) -> None:
    subprocess.run(
        [get_ffmpeg_path(), "-y", "-loglevel", "error",
         "-f", "lavfi", "-i", "testsrc=size=160x90:rate=10:duration=1",
         "-f", "lavfi", "-i", f"sine=frequency={440 + seed}:duration=1",
         "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac",
         "-f", "mpegts", str(path)],
        check=True,
    )


def _duration(path: Path) -> float:
    out = subprocess.run(
        [get_ffprobe_path(), "-v", "error", "-show_entries", "format=duration",
         "-of", "csv=p=0", str(path)],
        capture_output=True, text=True, encoding="utf-8", check=True,
    ).stdout
    return float(out.strip().splitlines()[0].split(",")[-1])


def test_concat_demux_skips_unreadable_segment(have_ffmpeg, tmp_path):
    good1, good2 = tmp_path / "seg_00000.ts", tmp_path / "seg_00001.ts"
    _make_ts(good1, 0)
    _make_ts(good2, 1)
    bad = tmp_path / "seg_00002.ts"
    bad.write_bytes(b"\x47" + b"\x00" * 99999)  # TS sync byte, otherwise garbage
    good3 = tmp_path / "seg_00003.ts"
    _make_ts(good3, 2)

    out = tmp_path / "merged.ts"
    assert concat_demux_merge_segments([good1, good2, bad, good3], out, expected_duration=3.0) is True
    assert 2.5 < _duration(out) < 3.5


def test_concat_demux_all_good_needs_no_retry(have_ffmpeg, tmp_path):
    good1, good2 = tmp_path / "seg_00000.ts", tmp_path / "seg_00001.ts"
    _make_ts(good1, 0)
    _make_ts(good2, 1)
    out = tmp_path / "merged.ts"
    assert concat_demux_merge_segments([good1, good2], out, expected_duration=2.0) is True
    assert 1.5 < _duration(out) < 2.5


def test_concat_demux_failure_with_nothing_to_prune_reports_false(have_ffmpeg, tmp_path, monkeypatch):
    """A failed re-merge must not report success just because the caller's previous output still exists."""
    from VibraVid.core.muxing.helper.video import merge

    good1, good2 = tmp_path / "seg_00000.ts", tmp_path / "seg_00001.ts"
    _make_ts(good1, 0)
    _make_ts(good2, 1)
    out = tmp_path / "merged.ts"
    out.write_bytes(b"previous binary merge")

    monkeypatch.setattr(merge, "_run_concat_demuxer", lambda *a, **k: False)
    assert concat_demux_merge_segments([good1, good2], out) is False
    assert out.read_bytes() == b"previous binary merge"
