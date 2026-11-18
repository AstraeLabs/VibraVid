# 23.10.26

"""ARR hands AnimeUnity/AnimeWorld the Sonarr season as season_selection (the tracker's season is reset to 0 first): it must reach the filename."""

from types import SimpleNamespace
from unittest import mock

import pytest

from VibraVid.core.ui.tracker import context_tracker
from VibraVid.services.animeunity import downloader as au
from VibraVid.services.animeworld import downloader as aw


@pytest.fixture(autouse=True)
def _reset_tracker():
    context_tracker.season = 0  # site_search_manager resets it before calling the service
    yield
    context_tracker.season = 0


def run_animeunity(season_selection):
    """Download ep 1 through AnimeUnity's download_series; return (season given to map_episode_path, tracker season)."""
    scrape = SimpleNamespace(
        series_name="My Hero Academia",
        is_series=True,
        episodes_cache=[{"number": "1"}],
        get_count_episodes=lambda: 1,
        get_info_episode=lambda _index: SimpleNamespace(id=1, number="1"),
    )
    seen = {}

    def fake_map(**kwargs):
        seen.update(kwargs)
        return ([], "ep")

    with (
        mock.patch.object(au, "VideoSourceAnime", return_value=mock.MagicMock(master_playlist="m3u8")),
        mock.patch.object(au, "map_episode_path", side_effect=fake_map),
        mock.patch.object(au, "start_message"),
        mock.patch.object(au, "print_episode_header"),
        mock.patch.object(au, "anime_folder", return_value="out"),
        mock.patch.object(au.os_manager, "create_path"),
        mock.patch.object(au.os_manager, "get_sanitize_path", side_effect=lambda p: p),
        mock.patch.object(au, "HLS_Downloader") as hls,
    ):
        hls.return_value.start.return_value = ("out/ep.mp4", False, None)
        au.download_series(SimpleNamespace(id=1, slug="x"), season_selection, "1", scrape)
    return seen["season_number"], context_tracker.season


def run_animeworld(season_selection):
    seen = {}

    def fake_map(**kwargs):
        seen.update(kwargs)
        return ([], "ep")

    scrape = SimpleNamespace(get_name=lambda: "My Hero Academia", get_episodes=lambda: [object()], session_id="s", csrf_token="c")
    with (
        mock.patch.object(aw, "start_message"),
        mock.patch.object(aw, "print_episode_header"),
        mock.patch.object(aw, "map_episode_path", side_effect=fake_map),
        mock.patch.object(aw, "anime_folder", return_value="out"),
        mock.patch.object(aw.os_manager, "create_path"),
        mock.patch.object(aw, "VideoSource"),
        mock.patch.object(aw, "MP4_Downloader"),
    ):
        aw.download_series(SimpleNamespace(url="u"), season_selection, "1", scrape)
    return seen["season_number"], context_tracker.season


def test_animeunity_keeps_arr_season():
    assert run_animeunity("2") == (2, 2)


@pytest.mark.parametrize("selection", [None, "1", "0", "abc"])
def test_animeunity_defaults_to_season_1(selection):
    assert run_animeunity(selection) == (1, 1)


def test_animeworld_keeps_arr_season():
    assert run_animeworld("3") == (3, 3)


def test_animeworld_defaults_to_season_1():
    assert run_animeworld(None) == (1, 1)
