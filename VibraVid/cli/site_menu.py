# 30.10.26

from rich.console import Console, Group
from rich.live import Live
from rich.text import Text

from VibraVid.utils.system.keyboard import get_key

GLOBAL_KEY = "global"
GLOBAL_LABEL = "(global) Global"
DEFAULT_KEY = "0"
COLUMNS = 4
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

        legend = Text("  Category: ", style="cyan")
        for cat, color in self.category_colors.items():
            legend.append(f"{cat.capitalize()}({counts.get(cat, 0)})  ", style=color)
        return legend

    def _grid(self, typed: str) -> Text:
        keys = self._matches(typed)
        if not keys:
            return Text("  no match", style="dim")

        first = keys[0] if typed.strip() else None
        cells = []
        for key in keys:
            if key == GLOBAL_KEY:
                cell = Text(GLOBAL_LABEL.ljust(self.name_width + 4), style="magenta")
            else:
                name, cat = self.choice_labels[key]
                cell = Text.assemble((f"{key:>2}:", self.category_colors.get(cat, "white")), " ", (name.ljust(self.name_width), "cyan"))
            if key == first:
                cell.stylize("reverse")
            cells.append(cell)

        rows = [Text(" ").join(cells[i : i + COLUMNS]) for i in range(0, len(cells), COLUMNS)]
        return Text("\n").join(rows)

    def _render(self, typed: str, active: bool = True) -> Group:
        prompt = Text.assemble(("Insert site index", "cyan"), f" (number or name) [{DEFAULT_KEY}]: {typed}")
        if active:
            prompt.append(" ", style="reverse")
        return Group(Text(), self._legend(), Text(), self._grid(typed), Text(), prompt)
