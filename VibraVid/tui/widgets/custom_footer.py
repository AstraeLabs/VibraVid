# 30.07.26
# by @ManoloZocco

"""Custom bracketed footer widget displaying interactive shortcut labels."""

import logging

from rich.cells import cell_len
from textual import events, on
from textual.app import ComposeResult
from textual.containers import Horizontal
from textual.widget import Widget
from textual.widgets import Static

from VibraVid.tui.i18n import t

logger = logging.getLogger(__name__)

# (widget id, key hint, i18n key, drop order). The footer drops entries with the
# highest drop order first when the terminal is too narrow to show them all;
# 0 is never dropped, so the quit hint always stays on screen.
FOOTER_ITEMS: list[tuple[str, str, str, int]] = [
    ("foot-home", "H", "nav_home", 3),
    ("foot-downloads", "d", "nav_downloads", 5),
    ("foot-queue", "q", "nav_queue", 6),
    ("foot-history", "h", "nav_history", 7),
    ("foot-settings", ",", "nav_settings", 8),
    ("foot-system", "s", "nav_system", 9),
    ("foot-help", "?", "nav_help", 4),
    ("foot-back", "ESC", "nav_back", 2),
    ("foot-quit", "Ctrl+Q", "nav_quit", 0),
]


class CustomFooter(Widget):
    """Custom footer displaying bracketed shortcut labels that highlight on hover and respond to mouse clicks."""

    DEFAULT_CSS = """
    CustomFooter {
        dock: bottom;
        height: 1;
        background: #1f2335;
        color: #c0caf5;
        padding: 0 1;
        border: none;
    }
    #custom-footer-bar {
        height: 1;
        width: 100%;
        align: center middle;
    }
    .foot-item {
        width: auto;
        height: 1;
        padding: 0 1;
        margin: 0;
        color: #7aa2f7;
        background: transparent;
    }
    .foot-item:hover {
        color: #ffffff;
        background: #2f3549;
        text-style: bold;
    }
    """

    def compose(self) -> ComposeResult:
        with Horizontal(id="custom-footer-bar"):
            for item_id, key, label_key, _ in FOOTER_ITEMS:
                yield Static(
                    f"[bold #7dcfff]\\[{key}][/bold #7dcfff] {t(label_key)}",
                    id=item_id,
                    classes="foot-item",
                )

    def _item_width(self, key: str, label_key: str) -> int:
        """Rendered width of one entry: its plain text plus the .foot-item padding."""
        return cell_len(f"[{key}] {t(label_key)}") + 2

    def on_resize(self, event: events.Resize) -> None:
        """Hide the least important entries when they no longer fit.

        Without this the bar simply overflows: on an 80 column terminal the last
        entries are laid out past the right edge and become invisible, quit included.
        """
        available = max(0, event.size.width - self.styles.gutter.width)
        widths = {item_id: self._item_width(key, label_key) for item_id, key, label_key, _ in FOOTER_ITEMS}
        hidden: set[str] = set()

        def used() -> int:
            return sum(width for item_id, width in widths.items() if item_id not in hidden)

        for item_id, _, _, drop_order in sorted(FOOTER_ITEMS, key=lambda item: -item[3]):
            if used() <= available:
                break
            if drop_order > 0:
                hidden.add(item_id)

        for item_id, _, _, _ in FOOTER_ITEMS:
            self.query_one(f"#{item_id}", Static).display = item_id not in hidden

    @on(events.Click, ".foot-item")
    def _on_foot_item_click(self, event: events.Click) -> None:
        target_id = event.widget.id
        if target_id == "foot-home":
            self.app.action_go_home()
        elif target_id == "foot-downloads":
            self.app.action_open_area("downloads")
        elif target_id == "foot-queue":
            self.app.action_open_area("queue")
        elif target_id == "foot-history":
            self.app.action_open_area("history")
        elif target_id == "foot-settings":
            self.app.action_open_area("settings")
        elif target_id == "foot-system":
            self.app.action_open_area("system")
        elif target_id == "foot-help":
            self.app.action_help()
        elif target_id == "foot-back":
            self.app.action_back()
        elif target_id == "foot-quit":
            self.app.exit()
