# 30.07.26
# by @ManoloZocco

"""History screen: past downloads viewer with status, paths, timestamps and errors."""

import logging
from typing import Any

from textual import on
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import Screen
from textual.timer import Timer
from textual.widgets import Button, DataTable, Header, Static

from VibraVid.cli.command.equivalent_command import EquivalentCommandBuilder
from VibraVid.cli.command.queue import enqueue_argv
from VibraVid.core.ui.tracker import download_tracker
from VibraVid.tui.formatting import format_time
from VibraVid.tui.i18n import t
from VibraVid.tui.widgets.custom_footer import CustomFooter
from VibraVid.utils.system_open import open_file, open_folder

logger = logging.getLogger(__name__)


class HistoryScreen(Screen):
    """Past download history panel."""

    def __init__(self) -> None:
        super().__init__()
        self._refresh_timer: Timer | None = None
        self._history_items: list[dict[str, Any]] = []

    def compose(self) -> ComposeResult:
        yield Header()
        with Vertical(id="history-panel"):
            yield Static(t("download_history"), classes="panel-title")
            yield Static(
                t("history_status_text"),
                id="history-status-text",
                classes="detail-meta",
            )
            yield DataTable(id="history-table")
            yield Static(t("history_item_details"), classes="panel-title")
            yield Static(
                t("select_item_view_details"),
                id="history-item-detail",
                classes="history-detail-box",
            )
            with Horizontal(id="history-actions"):
                yield Button(t("refresh"), id="refresh-btn")
                yield Button(f"▶ {t('play_file')}", id="play-history-btn")
                yield Button(f"📁 {t('open_folder')}", id="open-folder-history-btn")
                yield Button(t("re_enqueue_item"), id="retry-history-btn")
                yield Button(t("clear_history"), id="clear-history-btn")
        yield CustomFooter()

    def on_mount(self) -> None:
        table = self.query_one("#history-table", DataTable)
        table.add_columns(
            t("col_id"),
            t("col_title"),
            t("col_site"),
            t("col_type"),
            t("col_status"),
            t("col_output_path"),
            t("col_finished"),
        )
        table.cursor_type = "row"
        table.show_cursor = True

        self._refresh_timer = self.set_interval(2.0, self._refresh_history)
        self._refresh_history()

    def on_unmount(self) -> None:
        if self._refresh_timer:
            self._refresh_timer.stop()

    def _refresh_history(self) -> None:
        """Reload history items from download_tracker."""
        items = download_tracker.get_history()
        self._history_items = items

        table = self.query_one("#history-table", DataTable)
        current_cursor = table.cursor_row
        table.clear()

        for dl in items:
            item_id = str(dl.get("id", "?"))[:8]
            title = str(dl.get("title", "?"))[:35]
            site = str(dl.get("site", "?"))
            media_type = str(dl.get("type", "Film"))
            status = str(dl.get("status", "unknown"))
            path = str(dl.get("path") or "-")
            path_short = path if len(path) <= 40 else "..." + path[-37:]
            end_time = format_time(dl.get("end_time") or dl.get("last_update"))

            if status == "completed":
                status_fmt = f"[green]{status}[/green]"
            elif status in ("failed", "timed_out"):
                status_fmt = f"[red]{status}[/red]"
            elif status == "cancelled":
                status_fmt = f"[yellow]{status}[/yellow]"
            else:
                status_fmt = f"[white]{status}[/white]"

            table.add_row(
                item_id,
                title,
                site,
                media_type,
                status_fmt,
                path_short,
                end_time,
                key=str(dl.get("id")),
            )

        if current_cursor is not None and current_cursor < len(items):
            table.move_cursor(row=current_cursor)

        counts = {}
        for dl in items:
            st = dl.get("status", "unknown")
            counts[st] = counts.get(st, 0) + 1

        summary = (
            f"Total: {len(items)} items  ·  "
            f"[green]Completed: {counts.get('completed', 0)}[/green]  ·  "
            f"[red]Failed: {counts.get('failed', 0)}[/red]  ·  "
            f"[yellow]Cancelled: {counts.get('cancelled', 0)}[/yellow]"
        )
        self.query_one("#history-status-text", Static).update(summary)

        self._update_item_detail()
        self._update_buttons()

    def _update_item_detail(self) -> None:
        table = self.query_one("#history-table", DataTable)
        detail_box = self.query_one("#history-item-detail", Static)

        if table.cursor_row is None or not self._history_items or table.cursor_row >= len(self._history_items):
            detail_box.update(t("select_item_view_details"))
            return

        dl = self._history_items[table.cursor_row]
        lines = [
            f"[bold cyan]ID:[/] {dl.get('id', '?')}   [bold cyan]Site:[/] {dl.get('site', '?')}   [bold cyan]Type:[/] {dl.get('type', '?')}",
            f"[bold cyan]Title:[/] {dl.get('title', '?')}",
            f"[bold cyan]Status:[/] {dl.get('status', '?')}   [bold cyan]Progress:[/] {dl.get('progress', 0):.1f}%",
            f"[bold cyan]Output Path:[/] {dl.get('path') or '-'}",
            f"[bold cyan]Start Time:[/] {format_time(dl.get('start_time'))}   [bold cyan]End Time:[/] {format_time(dl.get('end_time'))}",
        ]

        if dl.get("error"):
            lines.append(f"[bold red]Error Details:[/] {dl.get('error')}")

        quality = dl.get("quality")
        language = dl.get("language")
        if quality or language:
            lines.append(f"[bold cyan]Quality/Lang:[/] {quality or '-'} / {language or '-'}")

        tasks = dl.get("tasks", {})
        if tasks:
            lines.append(f"[bold cyan]Tasks ({len(tasks)}):[/] " + ", ".join(f"{k}: {v.get('progress', 0):.1f}%" for k, v in tasks.items()))

        detail_box.update("\n".join(lines))

    def _get_selected_item(self) -> dict[str, Any] | None:
        table = self.query_one("#history-table", DataTable)
        if table.cursor_row is not None and 0 <= table.cursor_row < len(self._history_items):
            return self._history_items[table.cursor_row]
        return None

    def _update_buttons(self) -> None:
        clear_btn = self.query_one("#clear-history-btn", Button)
        retry_btn = self.query_one("#retry-history-btn", Button)
        play_btn = self.query_one("#play-history-btn", Button)
        open_folder_btn = self.query_one("#open-folder-history-btn", Button)

        clear_btn.disabled = len(self._history_items) == 0

        dl = self._get_selected_item()
        if dl:
            retry_btn.disabled = False
            status = dl.get("status")
            path = dl.get("path")
            can_open = status == "completed" and bool(path and path != "-")
            play_btn.disabled = not can_open
            open_folder_btn.disabled = not can_open
        else:
            retry_btn.disabled = True
            play_btn.disabled = True
            open_folder_btn.disabled = True

    @on(DataTable.RowHighlighted, "#history-table")
    def _on_row_highlighted(self) -> None:
        self._update_item_detail()
        self._update_buttons()

    @on(DataTable.RowSelected, "#history-table")
    def _on_row_selected(self) -> None:
        self._update_item_detail()
        self._update_buttons()
        dl = self._get_selected_item()
        if dl and dl.get("status") == "completed":
            path = dl.get("path")
            if path and path != "-":
                self._on_play_history_file()

    # ── Actions ───────────────────────────────────────────────────────────

    @on(Button.Pressed, "#play-history-btn")
    def _on_play_history_file(self) -> None:
        dl = self._get_selected_item()
        if not dl:
            return
        path = dl.get("path")
        if not path or path == "-":
            self.app.notify("Nessun file valido selezionato", severity="warning")
            return
        success, msg = open_file(path)
        severity = "information" if success else "error"
        self.app.notify(msg, severity=severity)

    @on(Button.Pressed, "#open-folder-history-btn")
    def _on_open_folder_history(self) -> None:
        dl = self._get_selected_item()
        if not dl:
            return
        path = dl.get("path")
        if not path or path == "-":
            self.app.notify("Nessun percorso valido selezionato", severity="warning")
            return
        success, msg = open_folder(path)
        severity = "information" if success else "error"
        self.app.notify(msg, severity=severity)

    @on(Button.Pressed, "#refresh-btn")
    def _on_refresh(self) -> None:
        self._refresh_history()
        self.app.notify("History refreshed", severity="information")

    @on(Button.Pressed, "#clear-history-btn")
    def _on_clear_history(self) -> None:
        try:
            download_tracker.clear_history()
            self._refresh_history()
            self.app.notify("Cleared download history", severity="information")
        except Exception as e:
            self.app.notify(f"Could not clear history: {e}", severity="error")

    @on(Button.Pressed, "#retry-history-btn")
    def _on_retry_history(self) -> None:
        table = self.query_one("#history-table", DataTable)
        if table.cursor_row is None or table.cursor_row >= len(self._history_items):
            return

        dl = self._history_items[table.cursor_row]
        site = dl.get("site")
        title = dl.get("title")

        if not site or not title:
            self.app.notify("Missing site or title information to retry.", severity="warning")
            return

        # Reuse the exact search query + selected result index captured at download time when available, instead of guessing item 1
        cli_search = dl.get("cli_search") or title
        cli_item = dl.get("cli_item")
        if cli_item is None:
            self.app.notify(
                "Original search selection unknown for this entry; re-queuing item 1 (may not match).",
                severity="warning",
            )
            cli_item = 0

        builder = EquivalentCommandBuilder(excluded_dests=[])
        argv = builder.build_argv_from_params(site=site, search=cli_search, item=cli_item)

        if not argv:
            self.app.notify("Could not construct equivalent command to retry.", severity="error")
            return

        try:
            item = enqueue_argv(argv)
            self.app.notify(f"Re-queued download '{title[:25]}' ({item['id']})", severity="information")
        except Exception as e:
            self.app.notify(f"Failed to re-queue download: {e}", severity="error")
