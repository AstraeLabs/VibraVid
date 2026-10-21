# 19.10.26

import logging

from textual.app import ComposeResult
from textual.containers import Horizontal
from textual.widget import Widget
from textual.widgets import Static

from VibraVid.core.ui.tracker import download_tracker
from VibraVid.tui.i18n import t
from VibraVid.tui.palette import ACCENT, FG_MUTED, SUCCESS
from VibraVid.utils.upload.version import __version__

logger = logging.getLogger(__name__)

RUNNING_STATUSES = ("downloading", "active", "running")


class TopBar(Widget):
    """``TopBar("Settings", "config.toml and login.toml")``: a bold title and a muted description under the brand line."""

    DEFAULT_CSS = """
    TopBar {
        dock: top;
        height: 3;
        background: #121212;
    }
    #topbar-row {
        height: 1;
        width: 100%;
        padding: 0 2;
    }
    #topbar-brand {
        width: 1fr;
        height: 1;
        color: #71717a;
    }
    #topbar-activity {
        width: auto;
        height: 1;
        color: #86efac;
    }
    #topbar-context {
        height: 1;
        padding: 0 2;
        color: #71717a;
    }
    """

    def __init__(self, title: str = "", subtitle: str = "") -> None:
        super().__init__()
        self._title = title
        self._subtitle = subtitle

    def compose(self) -> ComposeResult:
        with Horizontal(id="topbar-row"):
            yield Static(f"[bold {ACCENT}]◆ VibraVid[/] v{__version__}", id="topbar-brand")
            yield Static("", id="topbar-activity")
        yield Static(self._context_text(), id="topbar-context")

    def on_mount(self) -> None:
        self._refresh_activity()
        self.set_interval(1.0, self._refresh_activity)

    def set_context(self, title: str, subtitle: str = "") -> None:
        self._title, self._subtitle = title, subtitle
        self.query_one("#topbar-context", Static).update(self._context_text())

    def _context_text(self) -> str:
        if not self._title:
            return ""
        head = f"[bold #ffffff]{self._title}[/]"
        return f"{head}  [{FG_MUTED}]·  {self._subtitle}[/]" if self._subtitle else head

    def _refresh_activity(self) -> None:
        try:
            running = [d for d in download_tracker.get_active_downloads() if d.get("status") in RUNNING_STATUSES]
            text = f"[{SUCCESS}]● {len(running)} {t('nav_downloads').lower()}[/]" if running else ""
            activity = self.query_one("#topbar-activity", Static)
            activity.update(text)
            activity.display = bool(text)
        except Exception:
            logger.debug("top bar activity not available", exc_info=True)
