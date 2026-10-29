# 19.10.26

import io
import logging
from collections.abc import Callable

from PIL import Image as PILImage
from textual.app import ComposeResult
from textual.containers import Vertical
from textual_image._geometry import ImageSize
from textual_image._terminal import get_cell_size
from textual_image.widget import (
    HalfcellImage,
    Image,
    SixelImage,
    TGPImage,
)

from VibraVid.tui import artwork
from VibraVid.utils import config_manager

logger = logging.getLogger(__name__)

MODES = ("auto", "sixel", "kitty", "blocks", "off")
MIN_ROWS = 6

MODE = str(config_manager.config.get("DEFAULT", "tui_images", default="auto") or "auto").strip().lower()
if MODE not in MODES:
    logger.warning(f"tui_images: unknown value '{MODE}' ignored (valid: {', '.join(MODES)})")
    MODE = "auto"

_WIDGETS = {"auto": Image, "sixel": SixelImage, "kitty": TGPImage, "blocks": HalfcellImage, "off": None}


def _image_widget(mode: str):
    """The textual-image widget class for the wanted mode (``auto`` picks Kitty, Sixel or characters by itself), or None for no covers."""
    return _WIDGETS[mode]


ImageWidget = _image_widget(MODE)


ImageWidget = _image_widget(MODE)


class CoverImage(Vertical):
    """Shows the cover returned by a loader. A newer ``show()`` supersedes an older one still in flight."""
    DEFAULT_CSS = """
    CoverImage {
        width: auto;
        height: auto;
        display: none;
    }
    CoverImage > .cover-art {
        width: auto;
        height: auto;
    }
    """

    def __init__(self, max_cells: tuple[int, int] = (24, 12), reserve: int = 0, widget=None, **kwargs) -> None:
        super().__init__(**kwargs)
        self._max_cells = max_cells
        self._reserve = reserve
        self._widget = widget or ImageWidget
        self._token = 0
        self.url: str | None = None

    def compose(self) -> ComposeResult:
        if self._widget is not None:
            yield self._widget(None, classes="cover-art")

    def clear(self) -> None:
        """Hide the cover and cancel whatever is loading."""
        self._token += 1
        self.url = None
        self.display = False

    def _box(self) -> tuple[int, int]:
        columns, rows = self._max_cells
        if self._reserve:
            rows = max(MIN_ROWS, min(rows, self.app.size.height - self._reserve))
        return columns, rows

    def show(self, resolve_url: Callable[[], str | None]) -> None:
        """Resolve a URL with *resolve_url* (blocking, runs in a thread), fetch it and draw it. The old image stays until the new one is ready."""
        if self._widget is None:
            return
        self._token += 1
        token = self._token
        box = self._box()
        self.run_worker(lambda: self._load(token, resolve_url, box), thread=True, exclusive=True, group="cover")

    def _load(self, token: int, resolve_url: Callable[[], str | None], box: tuple[int, int]) -> None:
        url, image = None, None
        try:
            url = resolve_url()
            data = artwork.fetch_cover(url) if url else None
            if data:
                with PILImage.open(io.BytesIO(data)) as source:
                    image = source.convert("RGB")
        except Exception as exc:
            logger.debug("cover failed: %s", exc)
            url, image = None, None
        if token != self._token:
            return
        try:
            self.app.call_from_thread(self._apply, token, url, image, box)
        except Exception:
            logger.debug("cover finished after its screen was closed")

    def _apply(self, token: int, url: str | None, image, box: tuple[int, int]) -> None:
        if token != self._token:
            return
        if image is None:
            self.url = None
            self.display = False
            return
        self.url = url
        art = self.query_one(".cover-art")
        art.styles.width, art.styles.height = ImageSize(*image.size, width="auto", height="auto").get_cell_size(*box, get_cell_size())
        art.image = image
        self.display = True
