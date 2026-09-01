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
