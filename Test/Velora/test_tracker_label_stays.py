# 29.10.26
# ruff: noqa: E402

import sys
from pathlib import Path

workspace_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(workspace_root))

import pytest

from VibraVid.core.ui import bar_manager as bm
from VibraVid.core.ui.tracker import context_tracker, download_tracker

LABEL = "[bold cyan]Aud[/bold cyan] [AAC] it-IT"


@pytest.fixture
def manager():
    context_tracker.is_gui = True  # no Rich Live: only the tracker is updated
    download_id = "label-test"
    download_tracker.start_download(download_id, "Cars 2", "streamingcommunity")
    try:
        yield bm.DownloadBarManager(download_id), download_id
    finally:
        download_tracker.downloads.pop(download_id, None)
        context_tracker.is_gui = False


def _label(download_id: str, key: str) -> str | None:
    return download_tracker.downloads[download_id]["tasks"][key].get("label")


def test_tick_without_label_keeps_the_rich_label_in_the_tracker(manager):
    bars, download_id = manager
    bars.handle_progress_line({"task_key": "aud_it", "label": LABEL, "pct": 10})
    bars.handle_progress_line({"task_key": "aud_it", "pct": 100})  # the terminal tick of the pipeline has no label
    bars.handle_progress_line({"task_key": "aud_it", "speed": "Failed"})

    assert _label(download_id, "aud_it") == LABEL


def test_label_arriving_later_replaces_the_placeholder(manager):
    bars, download_id = manager
    bars.handle_progress_line({"task_key": "aud_it", "pct": 5})
    assert _label(download_id, "aud_it") is None  # nothing was invented: the screens fall back to the key

    bars.handle_progress_line({"task_key": "aud_it", "label": LABEL, "pct": 6})
    assert _label(download_id, "aud_it") == LABEL
