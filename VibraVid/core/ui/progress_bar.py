# 04.01.25

from rich.progress import ProgressColumn
from rich.text import Text

from VibraVid.utils import internet_manager

SHOW_ELAPSED_REMAINING = True
SHOW_SIZE = True
SHOW_DURATION = False


class StatusLine:
    """Single extra rich-markup line rendered above the progress bars."""
    def __init__(self):
        self._text = ""

    def set_text(self, text: str) -> None:
        self._text = text or ""

    def __rich_console__(self, console, options):
        # Yield nothing when there's no text, so the Live/Group above the progress bars
        # doesn't reserve a blank line for downloads that never call set_text().
        if self._text:
            yield Text.from_markup(self._text)


class CustomBarColumn(ProgressColumn):
    def __init__(
        self,
        bar_width=30,
        complete_char="-",
        incomplete_char="-",
        complete_style="bright_magenta",
        incomplete_style="dim white",
    ):
        super().__init__()
        self.bar_width = bar_width
        self.complete_char = complete_char
        self.incomplete_char = incomplete_char
        self.complete_style = complete_style
        self.incomplete_style = incomplete_style

    def render(self, task):
        completed = task.completed
        total = task.total or 100

        bar_width = int((completed / total) * self.bar_width) if total > 0 else 0
        bar_width = min(bar_width, self.bar_width)

        text = Text()
        if bar_width > 0:
            text.append(self.complete_char * bar_width, style=self.complete_style)
            text.append(">", style=self.complete_style)
        if bar_width < self.bar_width:
            text.append(self.incomplete_char * (self.bar_width - bar_width - 1), style=self.incomplete_style)

        return text


class CompactTimeRemainingColumn(ProgressColumn):
    def render(self, task):
        if not SHOW_ELAPSED_REMAINING:
            return Text("")

        # For live streams show content duration downloaded
        if task.fields.get("segment", "").endswith("/~"):
            duration = task.fields.get("duration", "")
            if duration:
                d = duration.split("/")[0] if "/" in duration else duration
                return Text.from_markup(f"[green]{d}[/green]")
            return Text.from_markup("[dim]--:--[/dim]")

        # Producers that know the byte total and speed send their own ETA ("eta" present, even if None),
        # refreshed with every speed update; rich's estimate is based on the integer pct and goes stale.
        remaining = task.fields["eta"] if "eta" in task.fields else task.time_remaining
        if remaining is None:
            return Text.from_markup("[cyan]--:--[/cyan]")
        return Text.from_markup(f"[cyan]{internet_manager.format_time(remaining)}[/cyan]")


class TransferStatsColumn(ProgressColumn):
    def render(self, task):
        if task.fields.get("compact_metrics"):
            return Text("")

        size = task.fields.get("size", "")
        speed = task.fields.get("speed", "")
        duration = task.fields.get("duration", "")
        text = Text()
        has_content = False

        # SIZE
        if size and SHOW_SIZE:
            if "/" in size:
                current, total = size.split("/", 1)
                text.append(current, style="dim")
                text.append(" / ", style="dim")
                text.append(total, style="green")
            else:
                text.append(size, style="green")
            has_content = True

        # DURATION
        if duration and SHOW_DURATION:
            if has_content:
                text.append(" ", style="dim")
            text.append("|", style="dim")
            text.append(" ", style="dim")

            if "/" in duration:
                elapsed_d, total_d = duration.split("/", 1)
                text.append(elapsed_d, style="yellow")
                text.append("/", style="dim")
                text.append(total_d, style="cyan")
            else:
                text.append(duration, style="yellow")

            has_content = True

        # SPEED
        if speed:
            if has_content:
                text.append(" ")
            text.append("·", style="dim")
            text.append(" ")
            text.append(speed, style="red")

        return text
