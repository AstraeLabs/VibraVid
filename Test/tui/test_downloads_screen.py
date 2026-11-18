import pytest
from textual.app import App
from textual.widgets import DataTable

from VibraVid.core.ui.tracker import download_tracker
from VibraVid.tui.screens.downloads import DownloadsScreen


@pytest.mark.anyio
async def test_completed_downloads_allow_duplicate_output_paths(monkeypatch):
    shared_path = "/tmp/episode.mkv"
    history = [
        {
            "id": "download-one",
            "title": "Episode",
            "site": "example",
            "status": "completed",
            "path": shared_path,
        },
        {
            "id": "download-two",
            "title": "Episode retry",
            "site": "example",
            "status": "completed",
            "path": shared_path,
        },
    ]

    monkeypatch.setattr(download_tracker, "get_active_downloads", lambda: [])
    monkeypatch.setattr(download_tracker, "get_history", lambda: history)

    class TestApp(App):
        def compose(self):
            yield DownloadsScreen()

    app = TestApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        completed_table = app.screen.query_one("#completed-table", DataTable)
        assert completed_table.row_count == 2
