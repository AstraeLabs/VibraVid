# 29.07.26
# by @ManoloZocco

"""Filter-as-you-type list: an Input over a ListView of labelled items with mouse hover & directional events."""

import logging
import time
from difflib import SequenceMatcher
from typing import Any, NamedTuple

from textual import events, on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.message import Message
from textual.widget import Widget
from textual.widgets import Input, ListItem, ListView, Static

logger = logging.getLogger(__name__)


class FuzzyItem(NamedTuple):
    key: str
    label: str
    payload: Any = None


class HoverListItem(ListItem):
    """ListItem container for FuzzyList items."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._last_click_time: float = 0.0

    def on_click(self, event: events.Click) -> None:
        now = time.time()
        fuzzy_list = self.query_ancestor(FuzzyList)
        if fuzzy_list:
            fuzzy_list.action_focus_list()
            if (now - self._last_click_time < 0.5) and hasattr(self, "fuzzy_payload"):
                fuzzy_list.post_message(fuzzy_list.Activated(self.fuzzy_payload, fuzzy_list))
                self._last_click_time = 0.0
                event.prevent_default()
                event.stop()
                return
        self._last_click_time = now


class FuzzyList(Widget):
    """Input filter + ListView; reorders and filters items while typing."""

    class Chosen(Message):
        """Posted when the user confirms a list entry with ENTER or CLICK."""

        def __init__(self, item: FuzzyItem, control) -> None:
            super().__init__()
            self.item = item
            self._control_widget = control

        @property
        def control(self):
            return self._control_widget

    class Highlighted(Message):
        """Posted when a list entry is highlighted via mouse hover or arrow keys."""

        control: "FuzzyList | None" = None

        def __init__(self, item: FuzzyItem, control) -> None:
            super().__init__()
            self.item = item
            self.control = control

    class Activated(Message):
        """Posted when the user double-clicks a list entry."""

        control: "FuzzyList | None" = None

        def __init__(self, item: FuzzyItem, control) -> None:
            super().__init__()
            self.item = item
            self.control = control

    class ToggleRequested(Message):
        """Posted when the user presses SPACE on a list entry."""

        control: "FuzzyList | None" = None

        def __init__(self, item: FuzzyItem, control) -> None:
            super().__init__()
            self.item = item
            self.control = control

    BINDINGS = [Binding("down", "focus_list", show=False)]

    def __init__(self, placeholder: str = "Filter...", **kwargs) -> None:
        super().__init__(**kwargs)
        self._items: list[FuzzyItem] = []
        self._current_filtered: list[FuzzyItem] = []
        self._placeholder = placeholder
        self._last_click_time: float = 0.0
        self._last_click_key: str | None = None

    def compose(self) -> ComposeResult:
        yield Input(placeholder=self._placeholder, id="fuzzy-input")
        yield ListView(id="fuzzy-list")

    # ── Public API ────────────────────────────────────────────────────────

    def set_items(self, items: list[FuzzyItem], reset_filter: bool = True) -> None:
        self._items = list(items)
        if reset_filter:
            self.query_one("#fuzzy-input", Input).value = ""
            self._current_filtered = list(items)
            self.call_after_refresh(self._refresh_list, self._items)
        else:
            query = self.query_one("#fuzzy-input", Input).value.strip().lower()
            if not query:
                self._current_filtered = list(items)
                self.call_after_refresh(self._refresh_list, self._items)
            else:
                self.call_after_refresh(self._apply_filter, query)

    def action_focus_list(self) -> None:
        self.query_one("#fuzzy-list", ListView).focus()

    def get_highlighted_item(self) -> FuzzyItem | None:
        """Return the currently highlighted FuzzyItem, if any."""
        try:
            lv = self.query_one("#fuzzy-list", ListView)
            if lv.highlighted_child is not None and hasattr(lv.highlighted_child, "fuzzy_payload"):
                return lv.highlighted_child.fuzzy_payload
            if len(lv) > 0 and lv.index is not None and 0 <= lv.index < len(lv):
                return getattr(lv.children[lv.index], "fuzzy_payload", None)
        except Exception:
            pass
        return None

    def on_key(self, event: events.Key) -> None:
        """Intercept SPACE on list view for multi-selection."""
        if event.key == "space":
            try:
                lv = self.query_one("#fuzzy-list", ListView)
                if self.focused == lv or (self.focused and self.focused in lv.walk_children()):
                    item = self.get_highlighted_item()
                    if item:
                        self.post_message(self.ToggleRequested(item, self))
                        event.prevent_default()
                        event.stop()
            except Exception:
                pass

    # ── Internals ─────────────────────────────────────────────────────────

    async def _refresh_list(self, items: list[FuzzyItem]) -> None:
        lv = self.query_one("#fuzzy-list", ListView)
        await lv.clear()
        for it in items:
            li = HoverListItem(Static(it.label))
            li.fuzzy_payload = it
            lv.append(li)
        if len(lv) > 0:
            lv.index = 0
            first = getattr(lv.children[0], "fuzzy_payload", None)
            if first:
                self.post_message(self.Highlighted(first, self))

    @on(Input.Changed, "#fuzzy-input")
    async def _filter(self, event: Input.Changed) -> None:
        query = event.value.strip().lower()
        await self._apply_filter(query)

    async def _apply_filter(self, query: str) -> None:
        if not query:
            self._current_filtered = list(self._items)
            await self._refresh_list(self._items)
            return

        scored = []
        for it in self._items:
            label = it.label.lower()
            if query in label:
                score = 1.0 + len(query) / max(len(label), 1)
            else:
                score = SequenceMatcher(None, query, label).ratio()
            if score > 0.35:
                scored.append((score, it))
        scored.sort(key=lambda x: x[0], reverse=True)
        filtered_items = [it for _, it in scored]
        self._current_filtered = filtered_items
        await self._refresh_list(filtered_items)

    @on(ListView.Selected, "#fuzzy-list")
    def _chosen(self, event: ListView.Selected) -> None:
        item = getattr(event.item, "fuzzy_payload", None)
        if item is not None:
            self.post_message(self.Chosen(item, self))

    @on(ListView.Highlighted, "#fuzzy-list")
    def _highlighted(self, event: ListView.Highlighted) -> None:
        if event.item is not None:
            item = getattr(event.item, "fuzzy_payload", None)
            if item is not None:
                self.post_message(self.Highlighted(item, self))

    @on(events.Click, "ListItem")
    def _on_item_click(self, event: events.Click) -> None:
        self.action_focus_list()
        target_item = event.widget if isinstance(event.widget, ListItem) else None
        if target_item is None:
            for anc in event.widget.ancestors:
                if isinstance(anc, ListItem):
                    target_item = anc
                    break
        if target_item and hasattr(target_item, "fuzzy_payload"):
            fuzzy_item = target_item.fuzzy_payload
            now = time.time()
            if (now - self._last_click_time < 0.4) and (self._last_click_key == fuzzy_item.key):
                self.post_message(self.Activated(fuzzy_item, self))
                self._last_click_time = 0.0
                self._last_click_key = None
            else:
                self._last_click_time = now
                self._last_click_key = fuzzy_item.key
