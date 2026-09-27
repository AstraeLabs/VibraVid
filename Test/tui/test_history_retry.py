# 01.08.26

import pytest
from textual.app import App
from textual.widgets import DataTable

from VibraVid.cli.command.queue import _PROCESS_TAG, _load_queue, _queue_path
from VibraVid.tui.screens.history import HistoryScreen
from VibraVid.utils import config_manager


@pytest.fixture(autouse=True)
def _isolated_queue_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(config_manager, "base_path", str(tmp_path))
    return tmp_path


async def _mount_with_history(entry):
    class TestApp(App):
        def on_mount(self):
            self.push_screen(HistoryScreen())

    app = TestApp()
    async with app.run_test() as pilot:
        screen = app.screen
        screen._history_items = [entry]
        await pilot.pause()
        table = screen.query_one("#history-table", DataTable)
        table.add_row("id", "title", "site", "Film", "status", "-", "-", key=entry["id"])
        table.move_cursor(row=0)
        await pilot.pause()

        screen._on_retry_history()
        await pilot.pause()


@pytest.mark.anyio
async def test_retry_reuses_persisted_cli_search_and_item(monkeypatch):
    entry = {
        "id": "abc12345",
        "title": "Some Title",
        "site": "mysite",
        "cli_search": "some title exact query",
        "cli_item": 3,
    }
    await _mount_with_history(entry)

    data = _load_queue(_queue_path(_PROCESS_TAG))
    items = data.get("items", [])
    assert len(items) == 1
    argv = items[0]["argv"]

    assert argv[argv.index("-i") + 1] == "mysite"
    assert argv[argv.index("-s") + 1] == "some title exact query"
    assert argv[argv.index("--item") + 1] == "3"


@pytest.mark.anyio
async def test_retry_falls_back_to_item_zero_when_selection_unknown(monkeypatch):
    entry = {
        "id": "legacy123",
        "title": "Legacy Entry",
        "site": "mysite",
        # No cli_search/cli_item: simulates a history entry persisted before this fix.
    }
    await _mount_with_history(entry)

    data = _load_queue(_queue_path(_PROCESS_TAG))
    items = data.get("items", [])
    assert len(items) == 1
    argv = items[0]["argv"]

    assert argv[argv.index("-s") + 1] == "Legacy Entry"
    assert argv[argv.index("--item") + 1] == "0"
