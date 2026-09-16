"""Layout audit: catch widgets the user cannot see or click.

Textual lays widgets out even when they do not fit, so a button pushed past the
edge of its container simply disappears without any error. These tests drive the
real app headless and compare the compositor's geometry against the viewport, so
a widget that ends up clipped, off-screen or truncated fails the build.
"""

import pytest
from rich.cells import cell_len
from textual.widgets import Button, Input, Static

from VibraVid.tui.app import VibraVidApp
from VibraVid.tui.i18n import get_language, set_language
from VibraVid.tui.screens.search import SearchScreen

INTERESTING = (Button, Input, Static)


class MockItem:
    def __init__(self, name, year=2021, is_movie=False, is_song=False):
        self.name = name
        self.year = year
        self.is_movie = is_movie
        self.is_song = is_song
        self.type = "Movie" if is_movie else "TV"
        self.slug = name.lower().replace(" ", "-")
        self.desc = "Sinossi di prova per il collaudo del layout."


def fake_results():
    return [
        ("animeworld", MockItem("Cowboy Bebop")),
        ("animeunity", MockItem("Cowboy Bebop")),
        ("animeworld", MockItem("Frieren")),
        ("streamingcommunity", MockItem("Blade Runner 2049", 2017, is_movie=True)),
        ("streamingcommunity", MockItem("Random Access Memories", 2013, is_song=True)),
    ]


def scrolls(widget, axis: str) -> bool:
    """True if an ancestor scrolls on this axis, so the user can bring the widget into view.

    Checked per axis on purpose: a column that scrolls vertically does not make a
    button that overflows to the right reachable.
    """
    attr = "overflow_x" if axis == "x" else "overflow_y"
    for ancestor in widget.ancestors:
        styles = getattr(ancestor, "styles", None)
        if styles is not None and getattr(styles, attr) in ("auto", "scroll"):
            return True
    return False


def layout_defects(app) -> list[str]:
    """One line per widget the user cannot fully see or click on the current screen."""
    defects: list[str] = []
    compositor = app.screen._compositor
    visible = compositor.visible_widgets
    screen_region = app.screen.region

    for widget, (region, clip) in visible.items():
        if not isinstance(widget, INTERESTING) or not widget.display:
            continue
        ref = f"{type(widget).__name__}#{widget.id or '-'} {str(getattr(widget, 'label', '') or '')!r}"

        shown = region.intersection(clip)
        if shown.area == 0:
            defects.append(f"HIDDEN      {ref}: {region} entirely outside {clip}")
            continue
        if (shown.width < region.width and not scrolls(widget, "x")) or (
            shown.height < region.height and not scrolls(widget, "y")
        ):
            defects.append(f"CLIPPED     {ref}: {region.width}x{region.height} shown as {shown.width}x{shown.height}")

        if region.width == 0 or region.height == 0:
            defects.append(f"ZERO-SIZE   {ref}: {region}")

        on_screen = region.intersection(screen_region)
        if (on_screen.width < region.width and not scrolls(widget, "x")) or (
            on_screen.height < region.height and not scrolls(widget, "y")
        ):
            defects.append(f"OFF-SCREEN  {ref}: {region} vs screen {screen_region}")

        if isinstance(widget, Button):
            needed = cell_len(str(widget.label)) + widget.styles.gutter.width
            if needed > region.width:
                defects.append(f"TRUNCATED   {ref}: needs {needed} cells, has {region.width}")

    for widget, geometry in compositor.full_map.items():
        if not isinstance(widget, INTERESTING) or widget in visible or not widget.display:
            continue
        if not all(a.display for a in widget.ancestors_with_self):
            continue
        region, clip = geometry.region, geometry.clip
        out_x = region.x >= clip.right or region.right <= clip.x
        out_y = region.y >= clip.bottom or region.bottom <= clip.y
        if (out_x and not scrolls(widget, "x")) or (out_y and not scrolls(widget, "y")):
            label = str(getattr(widget, "label", "") or "")
            defects.append(f"UNREACHABLE {type(widget).__name__}#{widget.id or '-'} {label!r}: {region} vs {clip}")

    return defects


@pytest.fixture
def language(request):
    """Run a test under one locale: label lengths differ, and so does what overflows."""
    previous = get_language()
    set_language(request.param)
    yield request.param
    set_language(previous)


async def search_flow_defects(app, pilot):
    """Walk the search flow and collect the defects found at each step."""
    app.action_go_search()
    await pilot.pause()
    assert isinstance(app.screen, SearchScreen)
    found = list(layout_defects(app))

    app.screen._apply_results(fake_results(), {"altadefinizione": "timeout"}, None)
    await pilot.pause()
    found += layout_defects(app)

    # walk down to a title with several providers so the preview card fills in
    for _ in range(3):
        await pilot.press("down")
        await pilot.pause()
    return found + layout_defects(app)


@pytest.mark.anyio
@pytest.mark.parametrize("size", [(140, 40), (110, 32), (100, 30), (80, 24)])
@pytest.mark.parametrize("language", ["it", "en"], indirect=True)
async def test_home_screen_is_clean_at_every_supported_size(size, language):
    """Home adapts down to the smallest supported terminal.

    The scope pills wrap onto two rows when five of them no longer fit, the provider
    pills are packed by measured width, and the card scrolls, so nothing is cut off.
    """
    app = VibraVidApp()
    async with app.run_test(size=size) as pilot:
        await pilot.pause()
        assert layout_defects(app) == [], f"home at {size} in {language}"


@pytest.mark.anyio
@pytest.mark.parametrize("language", ["it", "en"], indirect=True)
async def test_search_screen_is_clean_at_reference_size(language):
    """At the reference size every control is fully visible, labels included."""
    app = VibraVidApp()
    async with app.run_test(size=(140, 40)) as pilot:
        assert await search_flow_defects(app, pilot) == [], f"140x40 in {language}"


@pytest.mark.anyio
@pytest.mark.parametrize("size", [(100, 30), (80, 24)])
@pytest.mark.parametrize("language", ["it", "en"], indirect=True)
async def test_search_screen_controls_stay_reachable_when_narrow(size, language):
    """On a small terminal labels may be shortened, but nothing may become unreachable.

    Truncation is legible degradation; a control clipped out of its container or laid
    out past the edge of the screen is not, because the user cannot get to it at all.
    """
    app = VibraVidApp()
    async with app.run_test(size=size) as pilot:
        defects = await search_flow_defects(app, pilot)
        unreachable = [d for d in defects if not d.startswith("TRUNCATED")]
        assert unreachable == [], f"{size} in {language}"


@pytest.mark.anyio
@pytest.mark.parametrize("size", [(140, 40), (100, 30), (80, 24)])
@pytest.mark.parametrize("language", ["it", "en"], indirect=True)
async def test_footer_keeps_its_essential_entries(size, language):
    """Down to the smallest supported terminal the footer must not push entries off screen.

    It used to lay all nine entries out unconditionally, so on an 80 column terminal
    back and quit were rendered past the right edge and became invisible.
    """
    app = VibraVidApp()
    async with app.run_test(size=size) as pilot:
        await pilot.pause()
        footer_defects = [d for d in layout_defects(app) if "foot-" in d]
        assert footer_defects == [], f"footer at {size} in {language}"

        for item_id in ("foot-quit", "foot-back", "foot-home"):
            assert app.screen.query_one(f"#{item_id}", Static).display, f"{item_id} hidden at {size} in {language}"
