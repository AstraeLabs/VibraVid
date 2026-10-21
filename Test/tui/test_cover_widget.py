# 19.10.26

import pytest
from PIL import Image
from textual.app import App

from VibraVid.tui.widgets import cover
from VibraVid.tui.widgets.cover import CoverImage


class CoverApp(App):
    def compose(self):
        yield CoverImage(max_cells=(20, 10), id="cover")


def test_unknown_mode_is_not_an_error():
    assert cover._graphics_widget("blocks") is None
    assert cover._graphics_widget("off") is None


def test_fit_keeps_the_proportions_inside_the_box():
    # a 2:3 poster in a 20x10 cell box: 10 rows are 20 "pixels" tall, so it ends 13 columns wide
    columns, rows = CoverImage._fit(400, 600, (20, 10))
    assert (columns, rows) == (13, 10)
    # a wide still is limited by the width instead
    columns, rows = CoverImage._fit(1600, 900, (20, 10))
    assert columns == 20 and rows == 5


@pytest.mark.anyio
async def test_blocks_cover_is_shown_then_hidden(monkeypatch):
    monkeypatch.setattr(cover, "GraphicsImage", None)
    app = CoverApp()
    async with app.run_test(size=(60, 20)) as pilot:
        widget = app.query_one("#cover", CoverImage)
        assert not widget.display

        from VibraVid.tui import artwork

        art = artwork.render_cover(_png_bytes(), 20, 10)
        token = widget._token
        widget._apply(token, "http://x/c.png", None, None, art)
        await pilot.pause()
        assert widget.display and widget.url == "http://x/c.png"

        widget._apply(token, None, None, None, None)
        await pilot.pause()
        assert not widget.display and widget.url is None


@pytest.mark.anyio
async def test_stale_result_is_ignored(monkeypatch):
    monkeypatch.setattr(cover, "GraphicsImage", None)
    app = CoverApp()
    async with app.run_test(size=(60, 20)) as pilot:
        widget = app.query_one("#cover", CoverImage)
        stale = widget._token
        widget._token += 1
        widget._apply(stale, "http://x/old.png", None, None, "art")
        await pilot.pause()
        assert not widget.display


@pytest.mark.anyio
@pytest.mark.parametrize("protocol", ["SixelImage", "TGPImage"])
async def test_graphics_cover_mounts_and_takes_its_scaled_size(monkeypatch, protocol):
    widgets = pytest.importorskip("textual_image.widget")
    monkeypatch.setattr(cover, "GraphicsImage", getattr(widgets, protocol))
    app = CoverApp()
    async with app.run_test(size=(60, 20)) as pilot:
        widget = app.query_one("#cover", CoverImage)
        image = Image.new("RGB", (400, 600), (180, 40, 40))
        cells = CoverImage._fit(*image.size, (20, 10))
        widget._apply(widget._token, "http://x/c.png", image, cells, None)
        await pilot.pause()
        assert widget.display
        art = widget.query_one(".cover-art")
        assert (art.styles.width.value, art.styles.height.value) == cells


def _png_bytes() -> bytes:
    import io

    buffer = io.BytesIO()
    Image.new("RGB", (40, 60), (30, 90, 200)).save(buffer, format="PNG")
    return buffer.getvalue()
