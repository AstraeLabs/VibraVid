# 19.10.26

import io
import logging
from collections.abc import Callable

from PIL import Image as PILImage
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widgets import Static

from VibraVid.tui import artwork
from VibraVid.utils import config_manager

logger = logging.getLogger(__name__)

MODES = ("auto", "sixel", "kitty", "blocks", "off")
MIN_ROWS = 6
CELL_RATIO = 2

MODE = str(config_manager.config.get("DEFAULT", "tui_images", default="auto") or "auto").strip().lower()
if MODE not in MODES:
    logger.warning(f"tui_images: unknown value '{MODE}' ignored (valid: {', '.join(MODES)})")
    MODE = "auto"


def _graphics_widget(mode: str):
    """The textual-image widget class for the wanted protocol, or None to draw blocks (also when the library or the terminal lacks it)."""
    if mode in ("blocks", "off"):
        return None
    try:  # imported here, before the app starts: the library probes the terminal when it is imported
        from textual_image.renderable import Image as auto_renderable
        from textual_image.renderable import SixelImage as sixel_renderable
        from textual_image.renderable import TGPImage as tgp_renderable
        from textual_image.widget import SixelImage, TGPImage
    except ImportError:
        return None

    if mode == "sixel":
        return SixelImage
    if mode == "kitty":
        return TGPImage
    
    # auto: only a real graphics protocol is worth it; the library's own character fallbacks need a newer Pillow than we require
    if issubclass(auto_renderable, tgp_renderable):
        return TGPImage
    if issubclass(auto_renderable, sixel_renderable):
        return SixelImage
    return None


GraphicsImage = _graphics_widget(MODE)


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

    def __init__(self, max_cells: tuple[int, int] = (24, 12), reserve: int = 0, **kwargs) -> None:
        super().__init__(**kwargs)
        self._max_cells = max_cells
        self._reserve = reserve
        self._token = 0
        self.url: str | None = None

    def compose(self) -> ComposeResult:
        if GraphicsImage is not None:
            yield GraphicsImage(None, classes="cover-art")
        else:
            yield Static("", classes="cover-art")

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
        self._token += 1
        token = self._token
        box = self._box()
        self.run_worker(lambda: self._load(token, resolve_url, box), thread=True, exclusive=True, group="cover")

    @staticmethod
    def _fit(width: int, height: int, box: tuple[int, int]) -> tuple[int, int]:
        """Cells the image takes inside the box, keeping its proportions."""
        max_w, max_h = box
        scale = min(max_w / width, max_h * CELL_RATIO / height)
        return max(1, int(width * scale)), max(1, int(height * scale / CELL_RATIO))

    def _load(self, token: int, resolve_url: Callable[[], str | None], box: tuple[int, int]) -> None:
        url, image, art, cells = None, None, None, None
        try:
            url = resolve_url()
            data = artwork.fetch_cover(url) if url else None
            if data and GraphicsImage is not None:
                with PILImage.open(io.BytesIO(data)) as source:
                    image = source.convert("RGB")
                cells = self._fit(*image.size, box)
            elif data:
                art = artwork.render_cover(data, *box)
        except Exception as exc:
            logger.debug("cover failed: %s", exc)
            url, image, art = None, None, None
        if token != self._token:
            return
        try:
            self.app.call_from_thread(self._apply, token, url, image, cells, art)
        except Exception:
            logger.debug("cover finished after its screen was closed")

    def _apply(self, token: int, url: str | None, image, cells, art) -> None:
        if token != self._token:
            return
        if image is None and art is None:
            self.url = None
            self.display = False
            return
        self.url = url
        target = self.query_one(".cover-art")
        if image is not None:
            target.styles.width, target.styles.height = cells
            target.image = image
        else:
            target.update(art)
        self.display = True
