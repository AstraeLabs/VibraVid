# 18.03.26

import logging

from rich.console import Console

from VibraVid.core.ui.ui import build_table, sort_streams_key
from VibraVid.utils.keyboard import get_key

logger = logging.getLogger(__name__)
console = Console(force_terminal=True)


class InteractiveStreamSelector:
    def __init__(self, streams: list, window_size: int = 15):
        """
        Initialize interactive stream selector.

        Args:
            streams: List of Stream objects to select from
            window_size: Number of rows to display at once (for pagination)
        """
        self.streams = sorted(streams, key=sort_streams_key)
        self.window_size = max(5, window_size)
        self.selected: set[int] = set()
        self.cursor = 0
        self.total = len(self.streams)

        for idx, s in enumerate(self.streams):
            if getattr(s, "selected", False):
                self.selected.add(idx)

    def run(self) -> set[int]:
        """
        Run interactive selection loop.

        Returns:
            Set of selected stream indices
        """
        if not self.streams:
            console.print("[yellow]No streams available for selection[/yellow]")
            return set()

        console.print("\n[cyan]Stream Selection Mode[/cyan]")
        console.print("[green]Use Up/Down to navigate, [bold]SPACE[/bold] to toggle, [bold]ENTER[/bold] to confirm[/green]\n")

        while True:
            # Clear and redraw table with current selection
            console.clear()
            table = build_table(
                self.streams,
                selected=self.selected,
                cursor=self.cursor,
                window_size=self.window_size,
                highlight_cursor=True,
            )
            console.print(table)

            # Show controls info
            console.print("\n[dim]Controls: Up/Down navigate | SPACE toggle | ENTER confirm | ESC cancel[/dim]")

            # Wait for key input
            key = get_key()

            if key == "UP":
                self.cursor = max(0, self.cursor - 1)

            elif key == "DOWN":
                self.cursor = min(self.total - 1, self.cursor + 1)

            elif key == "SPACE":
                if self.cursor in self.selected:
                    self.selected.discard(self.cursor)
                else:
                    self.selected.add(self.cursor)

            elif key == "ENTER":
                if self.selected:
                    self._apply_selection()
                    console.print("\n[green]OK Selection confirmed[/green]\n")
                    return self.selected
                else:
                    console.print("\n[yellow]! Please select at least one stream![/yellow]")

            elif key == "ESC":
                console.print("\n[yellow]Selection cancelled - keeping original[/yellow]\n")
                return self.selected

    def _apply_selection(self) -> None:
        """Mark selected streams and unmark deselected ones."""
        for idx, stream in enumerate(self.streams):
            stream.selected = idx in self.selected
            logger.debug(f"Stream {idx}: {getattr(stream, 'type', '?')} -> selected={stream.selected}")
