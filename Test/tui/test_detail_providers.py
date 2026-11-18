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


@pytest.mark.anyio
async def test_seasons_browser_sits_beside_a_tall_cover_not_below_it(monkeypatch):
    """A big cover must not push the seasons/episodes down the screen."""
    from VibraVid.tui.app import VibraVidApp

    monkeypatch.setattr(bridge, "get_seasons", lambda site, item: [])
    app = VibraVidApp()
    async with app.run_test(size=(140, 40)) as pilot:
        await app.push_screen(TitleDetailScreen(site="site1", item=MockItem("Test Series")))
        await pilot.pause()
        cover = app.screen.query_one("#detail-cover")
        cover.display = True
        cover.styles.width, cover.styles.height = 36, 18
        await pilot.pause()

        area = app.screen.query_one("#series-area")
        assert area.region.x >= cover.region.right, "beside the cover"
        assert area.region.y < 12, "near the top"


def _fake_seasons():
    from types import SimpleNamespace

    def season(number, count):
        episodes = [SimpleNamespace(number=n, name=f"Ep {n}", image=None) for n in range(1, count + 1)]
        return SimpleNamespace(number=number, episodes=episodes, image=None)

    return [season(1, 3), season(2, 2), season(3, 4)]


@pytest.mark.anyio
async def test_a_whole_season_is_picked_with_one_key(monkeypatch):
    """`g` (or space on the seasons list) selects every episode of the highlighted season, and again clears it."""
    from VibraVid.tui.app import VibraVidApp

    monkeypatch.setattr(bridge, "get_seasons", lambda site, item: _fake_seasons())
    app = VibraVidApp()
    async with app.run_test(size=(140, 40)) as pilot:
        screen = TitleDetailScreen(site="site1", item=MockItem("Test Series"))
        await app.push_screen(screen)
        for _ in range(40):  # the cover worker gets cancelled on teardown, so poll for the seasons instead of awaiting workers
            await pilot.pause(0.1)
            if screen._seasons:
                break
        label = lambda i: str(screen.query_one("#seasons").children[i].query_one("Static").render())  # noqa: E731

        await pilot.press("g")
        await pilot.pause()
        assert screen._episode_selections[1] == {1, 2, 3}
        assert label(0).startswith("✓") and "3/3" in label(0)

        await pilot.press("down", "space")  # space on the seasons list: season 2 whole as well
        await pilot.pause()
        assert screen._episode_selections[2] == {1, 2}
        assert "S2" in label(1) and label(1).startswith("✓")
        assert "season" in str(screen.query_one("#dsl-preview").render()).lower() and "'1-2'" in str(screen.query_one("#dsl-preview").render())

        await pilot.press("up", "g")  # whole already -> cleared
        await pilot.pause()
        assert screen._episode_selections[1] == set()
        assert label(0) == "S1  ·  3 ep"
