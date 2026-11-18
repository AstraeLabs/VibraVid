# 01.08.26

import pytest
from textual.app import App
from textual.widgets import Button

from VibraVid.tui.screens.queue import QueueScreen


@pytest.mark.anyio
async def test_queue_actions_include_enqueue_button():
    class TestApp(App):
        def compose(self):
            yield QueueScreen()

    app = TestApp()
    async with app.run_test() as pilot:
        screen = app.screen
        add_command_btn = screen.query_one("#add-command-btn", Button)
        assert add_command_btn is not None
        await pilot.pause()


@pytest.mark.anyio
async def test_queue_actions_all_buttons_present():
    expected_ids = {
        "run-queue-btn",
        "add-command-btn",
        "remove-btn",
        "retry-btn",
        "clear-queue-btn",
        "queue-btn-clear",
    }

    class TestApp(App):
        def compose(self):
            yield QueueScreen()

    app = TestApp()
    async with app.run_test() as pilot:
        screen = app.screen
        actions = screen.query_one("#queue-actions")
        present_ids = {btn.id for btn in actions.query(Button)}
        assert expected_ids.issubset(present_ids)
        await pilot.pause()
