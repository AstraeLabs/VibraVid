# 30.10.26

from rich.console import Console, Group
from rich.live import Live
from rich.text import Text

from VibraVid.utils.system.keyboard import get_key

GLOBAL_KEY = "global"
GLOBAL_LABEL = "(global) Global"
DEFAULT_KEY = "0"
COLUMNS = 4
CELL_GAP = 2
BACKSPACE_KEYS = ("\x08", "\x7f")
CANCEL_KEYS = ("ESC", "\x03")


class SiteMenu:
    def __init__(self, choice_labels: dict[str, tuple[str, str]], category_colors: dict[str, str], console: Console):
        self.choice_labels = choice_labels
        self.category_colors = category_colors
        self.console = console
        self.name_width = max([len(name) for name, _ in choice_labels.values()] + [len(GLOBAL_LABEL)])

    def run(self) -> str:
        typed = ""
        with Live(self._render(typed), console=self.console, auto_refresh=False, vertical_overflow="visible") as live:
            while True:
                key = get_key()
                if key == "ENTER":
                    selection = self._selection(typed)
                    if selection is not None:
                        live.update(self._render(typed, active=False), refresh=True)
                        return selection
                elif key in CANCEL_KEYS:
                    live.update(self._render(typed, active=False), refresh=True)
                    raise KeyboardInterrupt
                elif key in BACKSPACE_KEYS:
                    typed = typed[:-1]
                elif key == "SPACE":
                    typed += " "
                elif isinstance(key, str) and len(key) == 1 and key.isprintable():
                    typed += key
                live.update(self._render(typed), refresh=True)

    def _selection(self, typed: str) -> str | None:
        """The site Enter picks for ``typed``: the default for an empty line, the exact index, else the first match; None if nothing matches."""
        text = typed.strip().lower()
        if not text:
            return DEFAULT_KEY

        keys = self._matches(text)
        if text in keys:
            return text
        return keys[0] if keys else None

    def _matches(self, needle: str) -> list[str]:
        needle = needle.strip().lower()
        keys = [
            key
            for key, (name, cat) in self.choice_labels.items()
            if not needle or needle == key or needle in name.lower() or cat.startswith(needle)
        ]
        if GLOBAL_KEY.startswith(needle):
            keys.append(GLOBAL_KEY)
        return keys

    def _legend(self) -> Text:
        counts: dict[str, int] = {}
        for _, cat in self.choice_labels.values():
            counts[cat] = counts.get(cat, 0) + 1

        legend = Text("  Category: ", style="bold bright_white")
        for index, (cat, color) in enumerate(self.category_colors.items()):
            if index:
                legend.append("  ")
            legend.append(cat.capitalize(), style=f"bold {color}")
            legend.append(f" ({counts.get(cat, 0)})", style="dim bright_white")
        return legend

    def _grid(self, typed: str) -> Text:
        keys = self._matches(typed)
        if not keys:
            return Text("  no match", style="dim")

        first = keys[0] if typed.strip() else None
        cells: list[tuple[Text, Text, Text]] = []
        border_width = self.name_width + 5
        for key in keys:
            if key == GLOBAL_KEY:
                color = "magenta"
                name = Text.assemble(("(global) ", "bright_magenta"), ("Global", "bold bright_magenta"))
                name.append(" " * (self.name_width - name.cell_len))
                index = "  "
            else:
                name_value, cat = self.choice_labels[key]
                color = self.category_colors.get(cat, "white")
                name = Text(name_value.ljust(self.name_width), style="bold bright_white")
                index = f"{key:>2}"

            border_style = f"bold {color}"
            top = Text("┌" + "─" * border_width + "┐", style=border_style)
            middle = Text("│ ", style=border_style)
            middle.append(index, style=f"bold {color}")
            middle.append(" ")
            if key == first:
                name.stylize("on #30343b")
            middle.append_text(name)
            middle.append(" │", style=border_style)
            bottom = Text("└" + "─" * border_width + "┘", style=border_style)
            cells.append((top, middle, bottom))

        columns = max(1, min(COLUMNS, (self.console.width + CELL_GAP) // (border_width + 2 + CELL_GAP)))
        rows = []
        for start in range(0, len(cells), columns):
            row = cells[start : start + columns]
            rows.extend(
                Text(" " * CELL_GAP).join(cell[line] for cell in row)
                for line in range(3)
            )
        return Text("\n").join(rows)

    def _render(self, typed: str, active: bool = True) -> Group:
        default_category = self.choice_labels.get(DEFAULT_KEY, ("", ""))[1]
        default_color = self.category_colors.get(default_category, "bright_white")
        prompt = Text.assemble(
            ("Insert site index", "bold bright_white"),
            ("  (number or name) ", "dim"),
            (f"[{DEFAULT_KEY}]", f"bold {default_color}"),
            (": ", "dim"),
            (typed, "bold bright_white"),
        )
        if active:
            prompt.append(" ", style="reverse")
        return Group(Text(), self._legend(), Text(), self._grid(typed), Text(), prompt)
