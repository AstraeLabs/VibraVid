# 29.07.26
# by @ManoloZocco

"""Temporary screen for areas not yet implemented (see plan milestones)."""

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.screen import Screen
from textual.widgets import Static

from VibraVid.tui.widgets.custom_footer import CustomFooter


class PlaceholderScreen(Screen):
    """Centered card announcing the milestone that will implement the area."""

    def __init__(self, area: str, title: str, body: str, milestone: str) -> None:
        super().__init__()
        self.area = area
        self._title = title
        self._body = body
        self._milestone = milestone

    def compose(self) -> ComposeResult:
        with Vertical(id="placeholder-box"):
            yield Static(self._title, classes="placeholder-title")
            yield Static(self._body)
            yield Static(f"Coming with milestone {self._milestone}.", classes="placeholder-milestone")
            yield Static("ESC to go back", classes="placeholder-hint")
        yield CustomFooter()
