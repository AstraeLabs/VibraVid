# 01.10.26
# ruff: noqa: E402

import sys
from pathlib import Path
from types import SimpleNamespace

workspace_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(workspace_root))

from VibraVid.core.ui.bar_manager import DownloadBarManager
from VibraVid.core.ui.progress_bar import CompactTimeRemainingColumn


def _task(fields: dict, time_remaining=None):
    return SimpleNamespace(fields=fields, time_remaining=time_remaining)


def _text(task) -> str:
    return CompactTimeRemainingColumn().render(task).plain


def test_column_prefers_the_producer_eta():
    assert _text(_task({"eta": 125.0}, time_remaining=5)) == "02:05"


def test_column_shows_placeholder_when_eta_is_unknown():
    assert _text(_task({"eta": None}, time_remaining=5)) == "--:--"


def test_column_falls_back_to_rich_estimate_without_eta():
    """mp4 / yt-dlp / muxing bars never send an eta."""
    assert _text(_task({}, time_remaining=65)) == "01:05"
    assert _text(_task({}, time_remaining=None)) == "--:--"


def _eta_of(bar: DownloadBarManager, key: str):
    task = next(t for t in bar.progress.tasks if t.id == bar.tasks[key])
    return task.fields.get("eta", "absent")


def test_bar_manager_updates_eta_with_each_speed_update():
    bar = DownloadBarManager()
    bar.handle_progress_line({"task_key": "v", "pct": 2, "speed": "1.70M/s", "size": "1M/10M", "eta": 3600.0})
    assert _eta_of(bar, "v") == 3600.0
    bar.handle_progress_line({"task_key": "v", "pct": 2, "speed": "3.40M/s", "size": "2M/10M", "eta": 1800.0})
    assert _eta_of(bar, "v") == 1800.0


def test_bar_manager_clears_eta_when_the_bar_moves_to_merge_or_decrypt():
    bar = DownloadBarManager()
    bar.handle_progress_line({"task_key": "v", "pct": 99, "speed": "1.70M/s", "eta": 12.0})
    bar.handle_progress_line({"task_key": "v", "pct": 100, "speed": "Merge"})
    assert _eta_of(bar, "v") is None


def test_bar_manager_leaves_bars_without_eta_to_rich():
    bar = DownloadBarManager()
    bar.handle_progress_line({"task_key": "mp4", "pct": 10, "speed": "2.0M/s", "size": "1M/10M"})
    bar.handle_progress_line({"task_key": "mp4", "pct": 11, "speed": "2.0M/s", "size": "1.1M/10M"})
    assert _eta_of(bar, "mp4") == "absent"
