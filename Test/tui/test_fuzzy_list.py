import pytest
from textual import events, on
from textual.app import App, ComposeResult
from textual.geometry import Offset

from VibraVid.tui.widgets.fuzzy_list import FuzzyItem, FuzzyItemLabel, FuzzyList


class FuzzyListApp(App):
    def __init__(self) -> None:
        super().__init__()
        self.activated: list[FuzzyItem] = []

    def compose(self) -> ComposeResult:
        yield FuzzyList(id="results")

    @on(FuzzyList.Activated)
    def record_activation(self, event: FuzzyList.Activated) -> None:
        self.activated.append(event.item)


@pytest.mark.anyio
async def test_detached_result_label_does_not_enter_textual_selection_path(monkeypatch):
    app = FuzzyListApp()

    async with app.run_test() as pilot:
        results = app.query_one("#results", FuzzyList)
        results.set_items([FuzzyItem("one", "First result")])
        await pilot.pause()

        label = results.query_one(FuzzyItemLabel)
        assert label.allow_select is False

        # Reproduce the stale compositor state from the crash: the hit test
        # returns a label whose ListItem was already cleared during a refresh.
        await label.remove()
        assert label.parent is None
        monkeypatch.setattr(app.screen, "get_widget_and_offset_at", lambda *_: (label, Offset(0, 0)))
        monkeypatch.setattr(app.screen, "get_widget_at", lambda *_: (app.screen, app.screen.region))

        app.screen._forward_event(events.MouseDown(None, 0, 0, 0, 0, 1, False, False, False))


@pytest.mark.anyio
async def test_disabling_label_selection_does_not_break_mouse_activation():
    app = FuzzyListApp()

    async with app.run_test() as pilot:
        results = app.query_one("#results", FuzzyList)
        item = FuzzyItem("one", "First result")
        results.set_items([item])
        await pilot.pause()

        label = results.query_one(FuzzyItemLabel)

        await pilot.double_click(label)
        await pilot.pause()

        assert item in app.activated
