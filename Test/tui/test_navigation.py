"""Navigation across the screen stack: screens are reused, search results survive."""

import pytest

from VibraVid.tui.app import VibraVidApp
from VibraVid.tui.screens.downloads import DownloadsScreen
from VibraVid.tui.screens.home import HomeScreen
from VibraVid.tui.screens.search import SearchScreen


class MockItem:
    def __init__(self, name, year=2021, is_movie=False, is_song=False):
        self.name = name
        self.year = year
        self.is_movie = is_movie
        self.is_song = is_song
        self.type = "Movie" if is_movie else "TV"
        self.slug = name.lower().replace(" ", "-")


def fake_results():
    return [
        ("animeworld", MockItem("Cowboy Bebop")),
        ("animeunity", MockItem("Frieren")),
        ("streamingcommunity", MockItem("Dune", is_movie=True)),
    ]


async def open_search_with_results(app, pilot):
    app.action_go_search()
    await pilot.pause()
    search = app.screen
    assert isinstance(search, SearchScreen)
    search._apply_results(fake_results(), {}, None)
    await pilot.pause()
    return search


@pytest.mark.anyio
async def test_search_screen_is_reused_not_stacked():
    """Reaching search from another screen must return to the open one, not open a second."""
    app = VibraVidApp()
    async with app.run_test(size=(140, 40)) as pilot:
        search = await open_search_with_results(app, pilot)
        assert len(search._raw) == 3

        app.action_open_area("downloads")
        await pilot.pause()
        assert isinstance(app.screen, DownloadsScreen)

        app.action_go_search()
        await pilot.pause()

        assert app.screen is search
        assert len(search._raw) == 3
        assert sum(isinstance(s, SearchScreen) for s in app.screen_stack) == 1


@pytest.mark.anyio
async def test_area_screens_are_reused_not_stacked():
    app = VibraVidApp()
    async with app.run_test(size=(140, 40)) as pilot:
        app.action_open_area("downloads")
        await pilot.pause()
        downloads = app.screen

        app.action_open_area("queue")
        await pilot.pause()
        app.action_open_area("downloads")
        await pilot.pause()

        assert app.screen is downloads
        assert sum(isinstance(s, DownloadsScreen) for s in app.screen_stack) == 1


@pytest.mark.anyio
async def test_results_survive_going_home():
    """Home unwinds the stack; the snapshot brings the results back without searching again."""
    app = VibraVidApp()
    async with app.run_test(size=(140, 40)) as pilot:
        search = await open_search_with_results(app, pilot)
        search._last_query = "bebop"
        search._store_snapshot()

        app.action_go_home()
        await pilot.pause()
        assert isinstance(app.screen, HomeScreen)

        app.action_go_search()
        await pilot.pause()

        restored = app.screen
        assert isinstance(restored, SearchScreen)
        assert restored is not search
        assert len(restored._raw) == 3
        assert restored.query_one("#query").value == "bebop"


@pytest.mark.anyio
async def test_snapshot_keeps_filter_and_sort():
    app = VibraVidApp()
    async with app.run_test(size=(140, 40)) as pilot:
        search = await open_search_with_results(app, pilot)
        search._current_filter_category = "film"
        search._current_sort_mode = "title_asc"
        search._populate_results_list()
        await pilot.pause()

        app.action_go_home()
        await pilot.pause()
        app.action_go_search()
        await pilot.pause()

        restored = app.screen
        assert restored._current_filter_category == "film"
        assert restored._current_sort_mode == "title_asc"
        assert restored.query_one("#filter-film").variant == "primary"
        assert restored.query_one("#filter-all").variant == "default"


@pytest.mark.anyio
async def test_no_snapshot_leaves_the_screen_empty():
    app = VibraVidApp()
    async with app.run_test(size=(140, 40)) as pilot:
        app.action_go_search()
        await pilot.pause()
        assert app.screen._raw == []
