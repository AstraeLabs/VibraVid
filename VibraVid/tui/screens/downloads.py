# 30.07.26
# by @ManoloZocco

"""Downloads screen: live progress panel with per-track bars, status badges, cancel/retry."""

import datetime
import logging
from typing import Any

from textual import on
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import Screen
from textual.timer import Timer
from textual.widgets import Button, DataTable, Header, Static

from VibraVid.core.ui.tracker import download_tracker
from VibraVid.tui.i18n import t
from VibraVid.tui.widgets.custom_footer import CustomFooter
from VibraVid.utils.system_open import open_file, open_folder

logger = logging.getLogger(__name__)


def make_progress_bar(percentage: float, width: int = 12) -> str:
    """Create a block progress bar string [████████░░] 80.0%."""
    pct = max(0.0, min(100.0, percentage))
    filled_len = int(width * pct / 100.0)
    bar = "█" * filled_len + "░" * (width - filled_len)
    return f"[{bar}] {pct:5.1f}%"


def format_status_badge(status: str) -> str:
    """Format status into a high-visibility semantic badge."""
    st = str(status).lower()
    if "run" in st or "down" in st or "active" in st:
        return "[bold cyan]● RUNNING[/bold cyan]"
    elif "comp" in st or "done" in st or "finish" in st:
        return "[bold green]✓ DONE[/bold green]"
    elif "fail" in st or "err" in st:
        return "[bold red]✖ FAILED[/bold red]"
    elif "stop" in st or "cancel" in st:
        return "[bold yellow]⏸ STOPPED[/bold yellow]"
    else:
        return f"[dim]⏳ {status.upper()}[/dim]"


def _format_time(ts: Any) -> str:
    if not ts:
        return "-"
    try:
        if isinstance(ts, (int, float)):
            return datetime.datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")
        return str(ts)
    except Exception:
        return str(ts)


class DownloadsScreen(Screen):
    """Live download progress panel with cancel/retry controls."""

    def __init__(self) -> None:
        super().__init__()
        self._refresh_timer: Timer | None = None
        self._completed_items: list[dict[str, Any]] = []

    def compose(self) -> ComposeResult:
        yield Header()
        with Vertical(id="downloads-panel"):
            yield Static(t("active_downloads"), classes="panel-title")
            yield DataTable(id="downloads-table")
            yield Static(t("track_details_streams"), classes="panel-title")
            yield DataTable(id="tasks-table")
            yield Static(t("completed_downloads"), classes="panel-title")
            yield DataTable(id="completed-table")
            with Horizontal(id="downloads-actions"):
                yield Button(t("cancel_selected"), id="btn-cancel", variant="error")
                yield Button(t("clear_completed"), id="btn-clear")
                yield Button(f"▶ {t('play_file')}", id="btn-play-file")
                yield Button(f"📁 {t('open_folder')}", id="btn-open-folder")
        yield CustomFooter()

    def on_mount(self) -> None:
        main_table = self.query_one("#downloads-table", DataTable)
        main_table.add_columns(
            t("col_id"),
            t("col_title"),
            t("col_site"),
            t("col_status"),
            t("col_progress"),
            t("col_speed"),
            t("col_size"),
            t("col_segments"),
        )
        main_table.cursor_type = "row"
        main_table.show_cursor = True

        tasks_table = self.query_one("#tasks-table", DataTable)
        tasks_table.add_columns(
            t("col_track_stream"),
            t("col_progress"),
            t("col_speed"),
            t("col_size"),
            t("col_segments"),
        )

        completed_table = self.query_one("#completed-table", DataTable)
        completed_table.add_columns(
            t("col_id"),
            t("col_title"),
            t("col_site"),
            t("col_size"),
            t("col_path"),
            t("col_finished"),
        )
        completed_table.cursor_type = "row"
        completed_table.show_cursor = True

        self._refresh_timer = self.set_interval(0.5, self._refresh_downloads)
        self._refresh_downloads()

    def on_unmount(self) -> None:
        if self._refresh_timer:
            self._refresh_timer.stop()

    def _refresh_downloads(self) -> None:
        active = download_tracker.get_active_downloads()
        main_table = self.query_one("#downloads-table", DataTable)
        tasks_table = self.query_one("#tasks-table", DataTable)

        active_cursor = main_table.cursor_row
        main_table.clear()
        for dl in active:
            dl_id = str(dl.get("id", "?"))[:8]
            title = str(dl.get("title", "?"))[:38]
            site = str(dl.get("site", "?"))
            status = format_status_badge(str(dl.get("status", "?")))
            progress = make_progress_bar(float(dl.get("progress", 0)))
            speed = str(dl.get("speed", "0B/s"))
            size = str(dl.get("size", "0B/0B"))
            segments = str(dl.get("segments", "0/0"))
            main_table.add_row(dl_id, title, site, status, progress, speed, size, segments, key=dl.get("id"))

        if active_cursor is not None and active_cursor < len(active):
            main_table.move_cursor(row=active_cursor)

        tasks_table.clear()
        if main_table.cursor_row is not None and active:
            row_keys = list(main_table.rows.keys())
            if main_table.cursor_row < len(row_keys):
                row_key_obj = row_keys[main_table.cursor_row]
                row_key_str = str(getattr(row_key_obj, "value", row_key_obj))
                dl = next((d for d in active if str(d.get("id")) == row_key_str), None)
                if dl:
                    tasks = dl.get("tasks", {})
                    for task_key, task_data in tasks.items():
                        label = task_data.get("label", task_key)
                        progress = make_progress_bar(float(task_data.get("progress", 0)))
                        speed = str(task_data.get("speed", "0B/s"))
                        size = str(task_data.get("size", "0B/0B"))
                        segments = str(task_data.get("segments", "0/0"))
                        tasks_table.add_row(label, progress, speed, size, segments)

        completed_table = self.query_one("#completed-table", DataTable)
        completed_cursor = completed_table.cursor_row
        completed_table.clear()

        history_items = download_tracker.get_history()
        self._completed_items = [dl for dl in history_items if dl.get("status") == "completed"]

        for row_index, dl in enumerate(self._completed_items):
            dl_id = str(dl.get("id", "?"))[:8]
            title = str(dl.get("title", "?"))[:38]
            site = str(dl.get("site", "?"))
            size = str(dl.get("size", "-"))
            path = str(dl.get("path") or "-")
            finished = _format_time(dl.get("end_time") or dl.get("last_update"))
            # A file may legitimately appear more than once in history after it
            # has been downloaded again. DataTable keys must still be unique.
            row_key = f"{dl.get('id') or 'completed'}:{row_index}"
            completed_table.add_row(dl_id, title, site, size, path, finished, key=row_key)

        if completed_cursor is not None and completed_cursor < len(self._completed_items):
            completed_table.move_cursor(row=completed_cursor)

        self._update_buttons()

    def _update_buttons(self) -> None:
        active_table = self.query_one("#downloads-table", DataTable)
        completed_table = self.query_one("#completed-table", DataTable)

        btn_cancel = self.query_one("#btn-cancel", Button)
        btn_clear = self.query_one("#btn-clear", Button)
        btn_play = self.query_one("#btn-play-file", Button)
        btn_folder = self.query_one("#btn-open-folder", Button)

        active = download_tracker.get_active_downloads()
        btn_cancel.disabled = active_table.cursor_row is None or not active or active_table.cursor_row >= len(active)

        if completed_table.cursor_row is not None and self._completed_items and completed_table.cursor_row < len(self._completed_items):
            item = self._completed_items[completed_table.cursor_row]
            path = item.get("path")
            has_path = bool(path and path != "-")
            btn_play.disabled = not has_path
            btn_folder.disabled = not has_path
        else:
            btn_play.disabled = True
            btn_folder.disabled = True

        btn_clear.disabled = len(self._completed_items) == 0

    def _get_selected_completed_path(self) -> str | None:
        completed_table = self.query_one("#completed-table", DataTable)
        if completed_table.cursor_row is None or not self._completed_items:
            return None
        if completed_table.cursor_row < len(self._completed_items):
            item = self._completed_items[completed_table.cursor_row]
            return item.get("path")
        return None

    @on(Button.Pressed, "#btn-cancel")
    def _on_cancel(self) -> None:
        main_table = self.query_one("#downloads-table", DataTable)
        if main_table.cursor_row is None:
            return
        row_keys = list(main_table.rows.keys())
        if main_table.cursor_row < len(row_keys):
            row_key_obj = row_keys[main_table.cursor_row]
            row_key_str = str(getattr(row_key_obj, "value", row_key_obj))
            download_tracker.request_stop(row_key_str)
            self.app.notify(f"Cancel requested for {row_key_str[:8]}", severity="information")

    @on(Button.Pressed, "#btn-clear")
    def _on_clear(self) -> None:
        download_tracker.clear_history()
        self._refresh_downloads()
        self.app.notify("Cleared completed downloads from tracker", severity="information")

    @on(Button.Pressed, "#btn-play-file")
    def _on_play_file(self) -> None:
        path = self._get_selected_completed_path()
        if not path or path == "-":
            self.app.notify("Nessun file valido selezionato", severity="warning")
            return
        success, msg = open_file(path)
        severity = "information" if success else "error"
        self.app.notify(msg, severity=severity)

    @on(Button.Pressed, "#btn-open-folder")
    def _on_open_folder(self) -> None:
        path = self._get_selected_completed_path()
        if not path or path == "-":
            self.app.notify("Nessun percorso valido selezionato", severity="warning")
            return
        success, msg = open_folder(path)
        severity = "information" if success else "error"
        self.app.notify(msg, severity=severity)

    @on(DataTable.RowSelected, "#completed-table")
    def _on_completed_row_selected(self) -> None:
        self._on_play_file()

    @on(DataTable.RowHighlighted, "#completed-table")
    def _on_completed_row_highlighted(self) -> None:
        self._update_buttons()

    @on(DataTable.RowHighlighted, "#downloads-table")
    def _on_downloads_row_highlighted(self) -> None:
        self._update_buttons()
