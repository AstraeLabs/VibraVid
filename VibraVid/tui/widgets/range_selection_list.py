# 31.07.26
# by @ManoloZocco

"""SelectionList with advanced range selection, Shift+Click, Shift+Arrows, visual anchor & invert."""

import logging

from textual.events import Click, Key
from textual.message import Message
from textual.widgets import SelectionList

logger = logging.getLogger(__name__)


def parse_range_expression(expr: str, available_episodes: list[int]) -> set[int]:
    """Parse a range expression string into a set of episode numbers.

    Supported syntax:
    - '1-10': Episodes 1 to 10
    - '1-5, 8, 12-15': Multiple ranges and individual numbers
    - '*', 'all', 'tutti': All available episodes
    - 'even', 'pari': Even-numbered episodes
    - 'odd', 'dispari': Odd-numbered episodes
    - '1-*': From 1 to max episode
    """
    expr = expr.strip().lower()
    if not expr:
        return set()
    if expr in ("*", "all", "tutti"):
        return set(available_episodes)
    if expr in ("even", "pari"):
        return {e for e in available_episodes if e % 2 == 0}
    if expr in ("odd", "dispari"):
        return {e for e in available_episodes if e % 2 != 0}

    selected = set()
    max_ep = max(available_episodes) if available_episodes else 9999

    parts = [p.strip() for p in expr.split(",") if p.strip()]
    for part in parts:
        if "-" in part:
            subparts = part.split("-")
            if len(subparts) == 2:
                try:
                    start_str, end_str = subparts[0].strip(), subparts[1].strip()
                    start = int(start_str)
                    end = max_ep if end_str in ("*", "max", "end") else int(end_str)
                    for n in range(min(start, end), max(start, end) + 1):
                        if n in available_episodes:
                            selected.add(n)
                except ValueError:
                    pass
        else:
            try:
                n = int(part)
                if n in available_episodes:
                    selected.add(n)
            except ValueError:
                pass
    return selected


class RangeSelectionList(SelectionList):
    """SelectionList subclass with multi-episode range selection features:
    - Shift + Click mouse range selection
    - Shift + Up / Shift + Down keyboard range selection
    - Visual Anchor mode ('v' key)
    - Range modal trigger ('r' key)
    - Invert selection ('i' key)
    """

    class RequestRangeModal(Message):
        """Posted when the user requests the range input modal dialog."""

        control: "RangeSelectionList | None" = None

        def __init__(self, control: "RangeSelectionList | None" = None) -> None:
            super().__init__()
            self.control = control

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.anchor_index: int | None = None
        self.last_index: int | None = None

    def select_range(self, start_idx: int, end_idx: int, state: bool = True) -> None:
        """Select or deselect all options within 0-based index range [start_idx, end_idx]."""
        if self.option_count == 0:
            return
        low = max(0, min(start_idx, end_idx))
        high = min(self.option_count - 1, max(start_idx, end_idx))
        for i in range(low, high + 1):
            val = self.get_option_at_index(i).value
            if state:
                self.select(val)
            else:
                self.deselect(val)

    def invert_selection(self) -> None:
        """Invert selection state of all options in the list."""
        if self.option_count == 0:
            return
        currently_selected = set(self.selected)
        for i in range(self.option_count):
            val = self.get_option_at_index(i).value
            if val in currently_selected:
                self.deselect(val)
            else:
                self.select(val)

    def toggle_visual_anchor(self) -> tuple[bool, int | None, int | None]:
        """Toggle visual anchor mode on/off.

        Returns (is_now_active, anchor_idx, current_highlighted).
        """
        if self.anchor_index is None:
            self.anchor_index = self.highlighted if self.highlighted is not None else 0
            return True, self.anchor_index, self.highlighted
        else:
            target = self.highlighted if self.highlighted is not None else self.anchor_index
            self.select_range(self.anchor_index, target)
            old_anchor = self.anchor_index
            self.anchor_index = None
            return False, old_anchor, target

    def clear_anchor(self) -> None:
        """Cancel visual anchor mode without committing a range."""
        self.anchor_index = None

    def on_key(self, event: Key) -> None:
        """Keyboard navigation and range shortcuts."""
        if event.key == "shift+down":
            if self.highlighted is None:
                self.highlighted = 0
            if self.anchor_index is None:
                self.anchor_index = self.highlighted
            if self.highlighted < self.option_count - 1:
                self.highlighted += 1
                self.select_range(self.anchor_index, self.highlighted)
            event.prevent_default()
            event.stop()
        elif event.key == "shift+up":
            if self.highlighted is None:
                self.highlighted = 0
            if self.anchor_index is None:
                self.anchor_index = self.highlighted
            if self.highlighted > 0:
                self.highlighted -= 1
                self.select_range(self.anchor_index, self.highlighted)
            event.prevent_default()
            event.stop()
        elif event.key == "v":
            is_active, start_idx, end_idx = self.toggle_visual_anchor()
            if is_active:
                ep_val = (
                    self.get_option_at_index(start_idx).value
                    if start_idx is not None and start_idx < self.option_count
                    else "?"
                )
                self.app.notify(
                    f"Ancora impostata a E{ep_val}. Spostati e premi 'v' o INVIO per selezionare il range.",
                    severity="information",
                )
            else:
                self.app.notify("Range di episodi selezionato!", severity="information")
            event.prevent_default()
            event.stop()
        elif event.key == "r":
            self.post_message(self.RequestRangeModal(control=self))
            event.prevent_default()
            event.stop()
        elif event.key == "i":
            self.invert_selection()
            self.app.notify("Selezione episodi invertita!", severity="information")
            event.prevent_default()
            event.stop()
        elif event.key == "escape" and self.anchor_index is not None:
            self.clear_anchor()
            self.app.notify("Modalità ancoraggio annullata.", severity="information")
            event.prevent_default()
            event.stop()
        elif event.key in ("enter", "space") and self.anchor_index is not None:
            is_active, start_idx, end_idx = self.toggle_visual_anchor()
            self.app.notify("Range di episodi selezionato!", severity="information")
            event.prevent_default()
            event.stop()

    def _get_clicked_option_index(self, event: Click) -> int | None:
        """Helper to get 0-based option index from Click event via meta, style, or y coordinate."""
        opt = event.style.meta.get("option")
        if opt is not None and 0 <= opt < self.option_count:
            return opt

        try:
            style = self.get_style_at(event.x, event.y)
            if style and style.meta:
                opt = style.meta.get("option")
                if opt is not None and 0 <= opt < self.option_count:
                    return opt
        except Exception:
            pass

        y = event.y + self.scroll_offset.y
        if 0 <= y < self.option_count:
            return y

        return None

    async def _on_click(self, event: Click) -> None:
        """Handle mouse click including Left-Click, Right-Click (Range), and Shift+Click."""
        clicked_option = self._get_clicked_option_index(event)
        if clicked_option is not None and clicked_option < len(self._options) and not self._options[clicked_option].disabled:
            is_range_request = (event.button == 3) or event.shift or (self.anchor_index is not None)
            if is_range_request:
                anchor = (
                    self.anchor_index
                    if self.anchor_index is not None
                    else (self.last_index if self.last_index is not None else self.highlighted)
                )
                if anchor is None:
                    anchor = clicked_option
                self.select_range(anchor, clicked_option)
                self.highlighted = clicked_option
                self.last_index = clicked_option
                self.anchor_index = None
                self.app.notify(
                    f"Range selezionato col mouse (da #{anchor + 1} a #{clicked_option + 1})",
                    severity="information",
                )
                event.prevent_default()
                event.stop()
                return
            else:
                self.highlighted = clicked_option
                val = self.get_option_at_index(clicked_option).value
                self.toggle(val)
                self.last_index = clicked_option
                event.prevent_default()
                event.stop()
                return
        await super()._on_click(event)
