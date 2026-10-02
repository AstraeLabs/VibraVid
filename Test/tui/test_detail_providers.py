import pytest
from textual.app import App
from VibraVid.tui import bridge
from VibraVid.tui.screens.detail import TitleDetailScreen


class MockItem:
    def __init__(self, name: str, is_movie: bool = False):
        self.name = name
        self.is_movie = is_movie
        self.type = "series" if not is_movie else "movie"


def test_detail_screen_multi_provider_init():
    item1 = MockItem("Breaking Bad")
    item2 = MockItem("Breaking Bad")
    providers = [("animeworld", item1), ("streamingcommunity", item2)]

    screen = TitleDetailScreen(site="animeworld", item=item1, providers=providers)
    assert len(screen._providers) == 2
    assert screen._current_site == "animeworld"


def test_detail_screen_single_provider_fallback():
    item = MockItem("Single Provider Series")
    screen = TitleDetailScreen(site="animeworld", item=item)
    assert len(screen._providers) == 1
    assert screen._providers[0] == ("animeworld", item)
    assert screen._current_site == "animeworld"


@pytest.mark.anyio
async def test_detail_screen_providers_ui(monkeypatch):
    item1 = MockItem("Test Series")
    item2 = MockItem("Test Series")
    providers = [("site1", item1), ("site2", item2)]

    class TestApp(App):
        def compose(self):
            yield TitleDetailScreen(site="site1", item=item1, providers=providers)

    # No real provider lookup: the season worker would otherwise call get_api("site1") and report its
    # failure back at an arbitrary moment, possibly while the app is already shutting down.
    monkeypatch.setattr(bridge, "get_seasons", lambda site, item: [])

    app = TestApp()
    async with app.run_test() as pilot:
        screen = app.screen
        providers_list = screen.query_one("#providers")
        assert len(providers_list.children) == 2
        await app.workers.wait_for_complete()
        await pilot.pause()
