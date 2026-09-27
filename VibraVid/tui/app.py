# 29.07.26
# by @ManoloZocco

"""VibraVid Textual application shell.

Launched by tui.py. The classic CLI in VibraVid/cli/run.py is untouched
and remains the default interface.
Directional navigation:
- Right Arrow (->): Drill in / Select / Move right to child pane
- Left Arrow (<-): Go back / Move left to parent pane
- H: Jump to Home screen
- ESC: Go back one level
- Ctrl+Q: Quit
"""

import logging
from collections.abc import Callable

from textual.app import App
from textual.binding import Binding
from textual.events import Resize
from textual.screen import Screen
from textual.timer import Timer

from VibraVid.core.ui.tracker import download_tracker
from VibraVid.tui.screens.downloads import DownloadsScreen
from VibraVid.tui.screens.help import HelpScreen
from VibraVid.tui.screens.history import HistoryScreen
from VibraVid.tui.screens.home import HomeScreen
from VibraVid.tui.screens.placeholder import PlaceholderScreen
from VibraVid.tui.screens.queue import QueueScreen
from VibraVid.tui.screens.settings import SettingsScreen
from VibraVid.tui.screens.system import SystemScreen
from VibraVid.utils.upload.version import __version__

logger = logging.getLogger(__name__)

MIN_WIDTH = 80
MIN_HEIGHT = 24

AREAS = {
    "downloads": ("Downloads", "Live progress with per-track bars, cancel and retry.", "M2"),
    "queue": ("Queue", "Batch queue, shared with the --queue-* CLI commands.", "M3"),
    "history": ("History", "Past downloads with status, paths and errors.", "M3"),
    "settings": ("Settings", "config.json and login.json editors.", "M4"),
    "system": ("System", "Dependencies, DRM, logs and update check.", "M4"),
}


class VibraVidApp(App):
    """Root application: screen stack, global keymap, theme."""

    CSS_PATH = "theme.tcss"
    TITLE = "VibraVid"
    SUB_TITLE = f"v{__version__}"

    BINDINGS = [
        Binding("escape", "back", "Back", show=False, priority=True),
        Binding("left", "nav_left", "Left/Back", show=False),
        Binding("right", "nav_right", "Right/Select", show=False),
        Binding("f1", "go_home", "Home", show=False),
        Binding("H", "go_home", "Home", show=False),
        Binding("home", "go_home", "Home", show=False),
        Binding("f2", "go_search", "Search", show=False),
        Binding("f3", "open_area('downloads')", "Downloads", show=False),
        Binding("d", "open_area('downloads')", "Downloads", show=False),
        Binding("f4", "open_area('queue')", "Queue", show=False),
        Binding("q", "open_area('queue')", "Queue", show=False),
        Binding("f5", "open_area('history')", "History", show=False),
        Binding("h", "open_area('history')", "History", show=False),
        Binding("f6", "open_area('settings')", "Settings", show=False),
        Binding("comma", "open_area('settings')", "Settings", show=False),
        Binding("f7", "open_area('system')", "System", show=False),
        Binding("s", "open_area('system')", "System", show=False),
        Binding("f8", "help", "Help", show=False),
        Binding("f9", "help", "Help", show=False),
        Binding("question_mark", "help", "Help", show=False),
        Binding("ctrl+q", "quit", "Quit", show=False),
    ]

    def __init__(self) -> None:
        super().__init__()
        self._small_terminal_warned = False
        self._header_timer: Timer | None = None
        # Last search, kept alive so its results survive leaving the search screen.
        # Written by SearchScreen; see VibraVid/tui/screens/search.py.
        self.search_snapshot: object | None = None

    def on_mount(self) -> None:
        self.push_screen(HomeScreen())
        self._header_timer = self.set_interval(1.0, self._update_header_status)

    def _update_header_status(self) -> None:
        """Update subtitle with active download count and global speed."""
        try:
            active = download_tracker.get_active_downloads()
            running = [d for d in active if d.get("status") in ("downloading", "active", "running")]
            if running:
                self.sub_title = f"v{__version__}  ·  ● {len(running)} active download(s)"
            else:
                self.sub_title = f"v{__version__}"
        except Exception:
            pass

    # ── Global directional navigation & actions ───────────────────────────

    def action_go_home(self) -> None:
        """Jump directly back to the HomeScreen from anywhere."""
        if not isinstance(self.screen, HomeScreen):
            while len(self.screen_stack) > 1 and not isinstance(self.screen, HomeScreen):
                self.pop_screen()

    def _return_to(self, screen_type: type[Screen]) -> bool:
        """Pop back to a screen already in the stack, instead of stacking a duplicate.

        Pushing a second instance would bury the first one with all of its state:
        the search results, the active filters and the current selection.
        """
        if not any(isinstance(screen, screen_type) for screen in self.screen_stack):
            return False
        while not isinstance(self.screen, screen_type):
            self.pop_screen()
        return True

    def _open_unique(self, screen_type: type[Screen], factory: Callable[[], Screen] | None = None) -> None:
        """Show the one instance of this screen, creating it only if there is none."""
        if isinstance(self.screen, screen_type) or self._return_to(screen_type):
            return
        self.push_screen(factory() if factory is not None else screen_type())

    def action_go_search(self) -> None:
        """Jump directly to the global SearchScreen, reusing the open one if there is one."""
        from VibraVid.tui.screens.search import SearchScreen

        self._open_unique(SearchScreen, lambda: SearchScreen(site=None))

    def action_nav_left(self) -> None:
        """Move left between columns, or ascend/go back one level."""
        current = self.screen
        if hasattr(current, "action_nav_left") and callable(current.action_nav_left):
            current.action_nav_left()
        elif len(self.screen_stack) > 1:
            self.pop_screen()

    def action_nav_right(self) -> None:
        """Move right between columns, or descend/select current item."""
        current = self.screen
        if hasattr(current, "action_nav_right") and callable(current.action_nav_right):
            current.action_nav_right()

    def action_back(self) -> None:
        """ESC goes one level back, never kills the process."""
        if isinstance(self.screen, HomeScreen):
            return
        if len(self.screen_stack) > 1:
            self.pop_screen()

    def action_open_area(self, area: str) -> None:
        area_screens: dict[str, type[Screen]] = {
            "downloads": DownloadsScreen,
            "queue": QueueScreen,
            "history": HistoryScreen,
            "settings": SettingsScreen,
            "system": SystemScreen,
        }
        if area in area_screens:
            self._open_unique(area_screens[area])
            return
        if isinstance(self.screen, PlaceholderScreen) and self.screen.area == area:
            return
        title, body, milestone = AREAS[area]
        self.push_screen(PlaceholderScreen(area=area, title=title, body=body, milestone=milestone))

    def action_help(self) -> None:
        if isinstance(self.screen, HelpScreen):
            self.pop_screen()
        else:
            self.push_screen(HelpScreen())

    # ── Small terminal guard ──────────────────────────────────────────────

    def on_resize(self, event: Resize) -> None:
        small = event.size.width < MIN_WIDTH or event.size.height < MIN_HEIGHT
        if small and not self._small_terminal_warned:
            self._small_terminal_warned = True
            self.notify(
                f"Terminal is smaller than {MIN_WIDTH}x{MIN_HEIGHT}: layout may degrade.",
                title="Small terminal",
                severity="warning",
            )
        elif not small:
            self._small_terminal_warned = False


def main() -> None:
    from VibraVid.tui import bridge

    bridge.install_stdio_proxy()
    try:
        bridge.list_sites()
        bridge.preload_registry()
    except Exception as e:
        print(f"[tui] preload failed: {e}")
    VibraVidApp().run()
