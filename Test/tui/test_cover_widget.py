# 19.10.26

import pytest
from PIL import Image
from textual.app import App

from VibraVid.tui.widgets import cover


def _app(mode: str) -> App:
    class CoverApp(App):
        def compose(self):
            yield cover.CoverImage(max_cells=(20, 10), widget=cover._image_widget(mode), id="cover")

    return CoverApp()


def test_off_draws_no_covers():
    assert cover._image_widget("off") is None


@pytest.mark.parametrize("mode", ["auto", "sixel", "kitty", "blocks"])
def test_every_other_mode_has_a_widget(mode):
    assert cover._image_widget(mode) is not None


def _show(widget, url, image):
    widget._apply(widget._token, url, image, widget._box())


@pytest.mark.anyio
@pytest.mark.parametrize("mode", ["auto", "blocks"])
async def test_cover_is_shown_then_hidden(mode):
    app = _app(mode)
    async with app.run_test(size=(60, 20)) as pilot:
        widget = app.query_one("#cover")
        assert not widget.display

        _show(widget, "http://x/c.png", Image.new("RGB", (40, 60), (30, 90, 200)))
        await pilot.pause()
        assert widget.display and widget.url == "http://x/c.png"

        _show(widget, None, None)
        await pilot.pause()
        assert not widget.display and widget.url is None


@pytest.mark.anyio
async def test_stale_result_is_ignored():
    app = _app("auto")
    async with app.run_test(size=(60, 20)) as pilot:
        widget = app.query_one("#cover")
        stale = widget._token
        widget._token += 1
        widget._apply(stale, "http://x/old.png", Image.new("RGB", (4, 4)), (20, 10))
        await pilot.pause()
        assert not widget.display


@pytest.mark.anyio
@pytest.mark.parametrize("mode", ["sixel", "kitty", "blocks"])
async def test_the_widget_scales_the_image_inside_the_box(mode):
    """No size maths of ours: a poster is limited by the rows, a wide still by the columns."""
    app = _app(mode)
    async with app.run_test(size=(60, 30)) as pilot:
        widget = app.query_one("#cover")

        _show(widget, "http://x/poster.png", Image.new("RGB", (400, 600), (180, 40, 40)))
        await pilot.pause()
        assert widget.query_one(".cover-art").size.height == 10 and widget.query_one(".cover-art").size.width < 20

        _show(widget, "http://x/still.png", Image.new("RGB", (1600, 900), (40, 40, 180)))
        await pilot.pause()
        assert widget.query_one(".cover-art").size.width == 20 and widget.query_one(".cover-art").size.height < 10
