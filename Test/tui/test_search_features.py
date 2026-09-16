from unittest.mock import patch
import pytest

from VibraVid.tui.screens.search import (
    SearchScreen,
    _get_library_status,
    _sort_results,
    deduplicate_search_results,
)
from VibraVid.utils.system_open import copy_to_clipboard


class MockItem:
    def __init__(
        self,
        name: str,
        year: int = 2021,
        is_movie: bool = False,
        is_song: bool = False,
        slug: str = "",
    ):
        self.name = name
        self.year = year
        self.is_movie = is_movie
        self.is_song = is_song
        self.slug = slug or name.lower().replace(" ", "-")


def test_sort_results_modes():
    item_a = MockItem("Avatar", 2009)
    item_b = MockItem("Batman", 2022)
    item_c = MockItem("Cyberpunk", 2020)

    # 1 provider for Avatar, 3 for Batman, 2 for Cyberpunk
    raw = [
        ("site1", item_a, [("site1", item_a)]),
        ("site1", item_b, [("site1", item_b), ("site2", item_b), ("site3", item_b)]),
        ("site1", item_c, [("site1", item_c), ("site2", item_c)]),
    ]

    # Year desc
    sorted_year_desc = _sort_results(raw, "year_desc")
    assert [r[1].name for r in sorted_year_desc] == ["Batman", "Cyberpunk", "Avatar"]

    # Year asc
    sorted_year_asc = _sort_results(raw, "year_asc")
    assert [r[1].name for r in sorted_year_asc] == ["Avatar", "Cyberpunk", "Batman"]

    # Providers desc
    sorted_prov = _sort_results(raw, "providers_desc")
    assert [r[1].name for r in sorted_prov] == ["Batman", "Cyberpunk", "Avatar"]

    # Title asc (A-Z)
    sorted_title = _sort_results(raw, "title_asc")
    assert [r[1].name for r in sorted_title] == ["Avatar", "Batman", "Cyberpunk"]


def test_library_status_detection():
    item = MockItem("Inception", 2010, is_movie=True)

    with patch("VibraVid.core.ui.tracker.download_tracker.get_active_downloads", return_value=[]), \
         patch("VibraVid.core.ui.tracker.download_tracker.get_history", return_value=[{"title": "Inception", "status": "completed"}]), \
         patch("VibraVid.tui.screens.search._load_queue", return_value={"items": []}):
        status = _get_library_status(item, "streamingcommunity")
        assert status == "in_library"

    with patch("VibraVid.core.ui.tracker.download_tracker.get_active_downloads", return_value=[{"title": "Inception", "status": "downloading"}]), \
         patch("VibraVid.core.ui.tracker.download_tracker.get_history", return_value=[]), \
         patch("VibraVid.tui.screens.search._load_queue", return_value={"items": []}):
        status = _get_library_status(item, "streamingcommunity")
        assert status == "in_queue"


def test_copy_to_clipboard():
    success, msg = copy_to_clipboard("python main.py --site streamingcommunity -s Batman --item 1")
    assert isinstance(success, bool)
    assert isinstance(msg, str)

    # Empty text check
    success_empty, msg_empty = copy_to_clipboard("")
    assert success_empty is False


def test_search_screen_sort_cycle():
    screen = SearchScreen(site=None, initial_query="Test")
    assert screen._current_sort_mode == "relevance"

    # Simulate sort cycle
    screen.action_cycle_sort = SearchScreen.action_cycle_sort.__get__(screen)
    # The sort modes list is ['relevance', 'year_desc', 'year_asc', 'providers_desc', 'title_asc']
    curr = screen._current_sort_mode
    assert curr == "relevance"


@pytest.mark.anyio
async def test_search_screen_category_pills_initial_and_update():
    from textual.app import App
    from textual.widgets import Button

    screen = SearchScreen(site=None, initial_query="")

    class TestApp(App):
        def compose(self):
            yield screen

    app = TestApp()
    async with app.run_test() as pilot:
        filter_all = screen.query_one("#filter-all", Button)
        filter_film = screen.query_one("#filter-film", Button)
        filter_serie = screen.query_one("#filter-serie", Button)
        filter_music = screen.query_one("#filter-music", Button)

        assert "(0)" in str(filter_all.label)
        assert "(0)" in str(filter_film.label)
        assert "(0)" in str(filter_serie.label)
        assert "(0)" in str(filter_music.label)

        # Update with mock results
        item_film = MockItem("Movie 1", 2022, is_movie=True)
        item_serie = MockItem("Serie 1", 2021, is_movie=False)
        item_music = MockItem("Song 1", 2020, is_song=True)
        results = [("site1", item_film), ("site1", item_serie), ("site1", item_music)]

        screen._apply_results(results, {}, None)
        await pilot.pause()

        assert "(3)" in str(filter_all.label)
        assert "(1)" in str(filter_film.label)
        assert "(1)" in str(filter_serie.label)
        assert "(1)" in str(filter_music.label)


@pytest.mark.anyio
async def test_custom_footer_quit_click():
    from textual.app import App
    from VibraVid.tui.widgets.custom_footer import CustomFooter

    class TestApp(App):
        def compose(self):
            yield CustomFooter()

    app = TestApp()
    async with app.run_test(size=(140, 30)) as pilot:
        await pilot.click("#foot-quit")
        await pilot.pause()
        assert app._exit is True



@pytest.mark.anyio
async def test_provider_pills_pool_grows_and_reuses():
    from textual.app import App
    from textual.widgets import Button

    screen = SearchScreen(site=None, initial_query="")

    class TestApp(App):
        def compose(self):
            yield screen

    def payload_with(count: int) -> tuple:
        providers = [(f"site{i}", MockItem(f"Title {i}")) for i in range(count)]
        return (providers[0][0], providers[0][1], providers)

    app = TestApp()
    async with app.run_test() as pilot:
        assert screen._provider_pills() == []

        screen._render_preview_card(payload_with(2))
        await pilot.pause()
        assert len(screen._provider_pills()) == 2

        # More providers than before: the pool grows without re-mounting existing IDs
        screen._render_preview_card(payload_with(5))
        await pilot.pause()
        pills = screen._provider_pills()
        assert len(pills) == 5
        assert [p.id for p in pills] == [f"btn-prov-{i}" for i in range(5)]
        assert all(p.display for p in pills)

        # Fewer providers: pills are reused and the extras are hidden, not removed
        screen._render_preview_card(payload_with(3))
        await pilot.pause()
        pills = screen._provider_pills()
        assert len(pills) == 5
        assert [p.display for p in pills] == [True, True, True, False, False]
        assert str(pills[0].label) == "[1] site0"

        # Single provider: the whole box is hidden
        screen._render_preview_card(payload_with(1))
        await pilot.pause()
        assert screen.query_one("#preview-providers-box").display is False
        assert not any(p.display for p in screen._provider_pills())
        assert len(list(screen.query("#preview-providers-wrap > Button").results(Button))) == 5
