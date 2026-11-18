"""Navigation across the screen stack: screens are reused, search results survive."""

from types import SimpleNamespace

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


@pytest.mark.anyio
async def test_download_button_opens_the_downloads_page(monkeypatch):
    """Scarica on a film/song goes straight to the live Downloads screen."""
    app = VibraVidApp()
    async with app.run_test(size=(140, 40)) as pilot:
        search = await open_search_with_results(app, pilot)
        movie = MockItem("Dune", is_movie=True)
        started = []
        monkeypatch.setattr(search, "_get_active_target", lambda: ("streamingcommunity", movie, []))
        monkeypatch.setattr(search, "_start_direct_download", lambda site, item: started.append((site, item)))

        search.set_focus(None)  # the hotkey is ignored while a text input has the focus
        search.action_quick_download()
        await pilot.pause()

        assert started == [("streamingcommunity", movie)]
        assert isinstance(app.screen, DownloadsScreen)


@pytest.mark.anyio
async def test_home_has_no_logo_tagline_or_category_icons():
    from textual.css.query import NoMatches
    from textual.widgets import Button

    app = VibraVidApp()
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause()
        assert isinstance(app.screen, HomeScreen)
        for gone in ("#home-logo", "#home-tagline"):
            with pytest.raises(NoMatches):
                app.screen.query_one(gone)
        for pill in ("#scope-film", "#scope-serie", "#scope-music"):
            assert str(app.screen.query_one(pill, Button).label).isascii()


def test_result_type_tags_carry_no_icons():
    import inspect

    from VibraVid.tui.screens import search

    source = inspect.getsource(search)
    assert not any(icon in source for icon in ("🎬", "📺", "🎵"))


@pytest.mark.anyio
async def test_top_bar_shows_the_cli_status_items_centred():
    from textual.widgets import Static

    app = VibraVidApp()
    async with app.run_test(size=(120, 30)) as pilot:
        await pilot.pause()
        bar = app.screen.query_one("#topbar-status", Static)
        text = str(bar.render())

        assert text.startswith("VibraVid")
        for label in ("Geo:", "Sys:", "Ver:", "Typ:"):
            assert label in text
        first_row = app.screen.query_one("#topbar-row")
        text_width = len(text)
        middle = (bar.region.x + (bar.region.width - text_width) // 2) + text_width / 2
        assert abs(middle - (first_row.region.x + first_row.region.width / 2)) <= 2, "the block is centred"
        assert "◆" not in text and app.screen.query_one("#topbar-context", Static).render().plain == ""


@pytest.mark.anyio
async def test_home_footer_has_no_key_hints_line():
    from textual.css.query import NoMatches

    app = VibraVidApp()
    async with app.run_test(size=(120, 30)) as pilot:
        await pilot.pause()
        with pytest.raises(NoMatches):
            app.screen.query_one("#foot-hints")



@pytest.mark.anyio
async def test_global_search_shows_fast_sites_before_the_slow_one_finishes(monkeypatch):
    """A slow provider must not hold back the results of the others."""
    import threading

    from VibraVid.tui import bridge

    release = threading.Event()

    def fake_search_titles(site, query):  # noqa: ARG001
        if site == "slow":
            release.wait(10)
        return [MockItem(f"{site} result")]

    monkeypatch.setattr(bridge, "search_titles", fake_search_titles)
    monkeypatch.setattr(bridge, "available_adapters", lambda: ["fast", "slow"])

    app = VibraVidApp()
    async with app.run_test(size=(140, 40)) as pilot:
        app.action_go_search()
        await pilot.pause()
        search = app.screen
        assert isinstance(search, SearchScreen)
        search.query_one("#query").value = "cars"
        search._start_search()

        for _ in range(50):
            await pilot.pause(0.1)
            if search._raw:
                break
        assert [item.name for _, item, _ in search._raw] == ["fast result"], "fast site is visible while slow is pending"
        assert search.query_one("#search-loading").display is True

        release.set()
        for _ in range(50):
            await pilot.pause(0.1)
            if not search.query_one("#search-loading").display:
                break
        assert search.query_one("#search-loading").display is False
        assert sorted(item.name for _, item, _ in search._raw) == ["fast result", "slow result"]


def test_sites_needing_a_login_are_left_out_of_the_global_search_until_set_up(monkeypatch):
    from VibraVid.tui import bridge
    from VibraVid.utils import config_manager, disk_cache

    monkeypatch.setattr(bridge, "available_adapters", lambda: ["raiplay", "timvision", "crunchyroll", "chili"])
    monkeypatch.setattr(disk_cache, "load", lambda *a, **kw: None)
    logins = {"timvision": {"cookies": "[paste here]", "cdm": "x"}, "crunchyroll": {"etp_rt": "abc"}}
    monkeypatch.setattr(config_manager.login, "get_section", lambda site, default=None: logins.get(site, default or {}))

    sites, skipped = bridge.global_search_sites()

    assert skipped == ["timvision"]  # placeholder + cdm is not a credential; sites that work anonymously are never skipped
    assert sites == ["raiplay", "crunchyroll", "chili"]


def test_a_cached_session_counts_as_a_login(monkeypatch):
    from VibraVid.tui import bridge
    from VibraVid.utils import config_manager, disk_cache

    monkeypatch.setattr(config_manager.login, "get_section", lambda site, default=None: default or {})
    monkeypatch.setattr(disk_cache, "load", lambda site, name, *a, **kw: {"refresh_token": "t"} if name == "token_tv" else None)

    assert bridge.has_login("crunchyroll")


def test_the_home_category_limits_the_sites_searched(monkeypatch):
    from VibraVid.tui import bridge
    from VibraVid.utils import config_manager, disk_cache

    monkeypatch.setattr(bridge, "available_adapters", lambda: ["raiplay", "animeunity", "youtube_music", "aurora"])
    monkeypatch.setattr(disk_cache, "load", lambda *a, **kw: None)
    monkeypatch.setattr(config_manager.login, "get_section", lambda site, default=None: default or {})
    monkeypatch.setattr(bridge, "has_login", lambda site: True)
    monkeypatch.setattr(
        bridge,
        "sites_by_category",
        lambda: {
            "film_serie": [SimpleNamespace(name="raiplay")],
            "anime": [SimpleNamespace(name="animeunity")],
            "serie": [SimpleNamespace(name="aurora")],
            "song": [SimpleNamespace(name="youtube_music")],
        },
    )

    assert bridge.global_search_sites()[0] == ["raiplay", "animeunity", "youtube_music", "aurora"]
    assert bridge.global_search_sites("anime")[0] == ["animeunity"]
    assert bridge.global_search_sites("film")[0] == ["raiplay"]
    assert bridge.global_search_sites("serie")[0] == ["raiplay", "animeunity", "aurora"]
    assert bridge.global_search_sites("music")[0] == ["youtube_music"]


@pytest.mark.anyio
@pytest.mark.parametrize("scope, pill", [("film", "#filter-film"), ("serie", "#filter-serie"), ("music", "#filter-music"), ("anime", "#filter-all"), ("global", "#filter-all")])
async def test_the_home_category_stays_selected_on_the_search_screen(monkeypatch, scope, pill):
    from textual.widgets import Button

    from VibraVid.tui import bridge

    monkeypatch.setattr(bridge, "global_search_sites", lambda scope="global": ([], []))
    monkeypatch.setattr(bridge, "search_global", lambda *a, **kw: ({}, {}))
    app = VibraVidApp()
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause()
        await app.push_screen(SearchScreen(site=None, initial_query="x", scope=scope))
        await pilot.pause()
        primary = [b.id for b in app.screen.query(".filter-pill").results(Button) if b.variant == "primary"]
        assert primary == [pill.lstrip("#")]
