# 30.07.26
# by @ManoloZocco

"""Queue screen: batch queue viewer, runner and queue item manager."""

import logging
import os
import shlex
import subprocess

from textual import on, work
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen, Screen
from textual.timer import Timer
from textual.widgets import Button, DataTable, Header, Input, Static

from VibraVid.cli.command.queue import (
    _all_queue_paths,
    _child_command,
    _claim_next_any,
    _finish_item,
    _load_queue,
    _QueueLock,
    _save_queue,
    clear,
    enqueue_argv,
    remove,
)
from VibraVid.tui.i18n import t
from VibraVid.tui.widgets.custom_footer import CustomFooter

logger = logging.getLogger(__name__)


class EnqueueModal(ModalScreen[str | None]):
    """Modal dialog to enqueue a custom command or flag set."""

    def compose(self) -> ComposeResult:
        with Vertical(id="enqueue-modal-box"):
            yield Static(t("enqueue_job"), classes="panel-title")
            yield Static(
                t("enter_cli_args_hint"),
                classes="placeholder-hint",
            )
            yield Input(
                placeholder=t("enqueue_input_placeholder"),
                id="enqueue-input",
            )
            with Horizontal(id="modal-buttons"):
                yield Button(t("enqueue_job"), id="submit-btn", variant="primary")
                yield Button(t("cancel"), id="cancel-btn")

    @on(Button.Pressed, "#submit-btn")
    def _on_submit(self) -> None:
        val = self.query_one("#enqueue-input", Input).value.strip()
        self.dismiss(val if val else None)

    @on(Input.Submitted, "#enqueue-input")
    def _on_input_submitted(self, event: Input.Submitted) -> None:
        val = event.value.strip()
        self.dismiss(val if val else None)

    @on(Button.Pressed, "#cancel-btn")
    def _on_cancel(self) -> None:
        self.dismiss(None)


class QueueScreen(Screen):
    """Batch queue management screen."""

    def __init__(self) -> None:
        super().__init__()
        self._refresh_timer: Timer | None = None
        self._loaded_items: list[tuple[dict, str]] = []  # (item_dict, queue_file_path)
        self._is_worker_running: bool = False

    def compose(self) -> ComposeResult:
        yield Header()
        with Vertical(id="queue-panel"):
            yield Static(t("batch_queue"), classes="panel-title")
            yield Static(t("loading"), id="queue-status-text", classes="detail-meta")
            yield DataTable(id="queue-table")
            yield Static(t("item_detail"), classes="panel-title")
            yield Static(
                t("select_queue_item_view_details"),
                id="queue-item-detail",
                classes="queue-detail-box",
            )
            with Horizontal(id="queue-actions"):
                yield Button(t("run_queue"), id="run-queue-btn", variant="primary")
                yield Button(t("enqueue_job"), id="add-command-btn")
                yield Button(t("remove_item"), id="remove-btn")
                yield Button(t("retry_failed"), id="retry-btn")
                yield Button(t("clear_queue"), id="clear-queue-btn")
                yield Button(t("clear_completed"), id="queue-btn-clear")
        yield CustomFooter()

    def on_mount(self) -> None:
        table = self.query_one("#queue-table", DataTable)
        table.add_columns(
            t("col_id"),
            t("col_queue_tag"),
            t("col_status"),
            t("col_cmd_args"),
            t("col_enqueued"),
            t("col_attempts"),
        )
        table.cursor_type = "row"
        table.show_cursor = True

        self._refresh_timer = self.set_interval(1.0, self._refresh_queue)
        self._refresh_queue()

    def on_unmount(self) -> None:
        if self._refresh_timer:
            self._refresh_timer.stop()

    def _refresh_queue(self) -> None:
        """Reload all queued items from .cache/queue and update table & details."""
        paths = _all_queue_paths()
        loaded: list[tuple[dict, str]] = []
        counts: dict[str, int] = {"pending": 0, "running": 0, "done": 0, "failed": 0, "interrupted": 0}

        for path in paths:
            data = _load_queue(path)
            items = data.get("items", [])
            for it in items:
                loaded.append((it, path))
                st = it.get("status", "unknown")
                counts[st] = counts.get(st, 0) + 1

        self._loaded_items = loaded
        table = self.query_one("#queue-table", DataTable)
        current_cursor = table.cursor_row

        table.clear()

        for item, path in self._loaded_items:
            item_id = item.get("id", "?")
            tag = item.get("tag") or os.path.splitext(os.path.basename(path))[0]
            status = item.get("status", "pending")
            argv_str = " ".join(item.get("argv", []))
            enqueued = item.get("enqueued_at", "-")
            attempts = str(item.get("attempts", 0))

            # Style status column text
            if status == "done":
                status_formatted = f"[green]{status}[/green]"
            elif status == "failed":
                status_formatted = f"[red]{status}[/red]"
            elif status == "running":
                status_formatted = f"[cyan]{status}[/cyan]"
            elif status == "interrupted":
                status_formatted = f"[magenta]{status}[/magenta]"
            else:
                status_formatted = f"[yellow]{status}[/yellow]"

            table.add_row(
                item_id,
                tag,
                status_formatted,
                argv_str[:60] + ("..." if len(argv_str) > 60 else ""),
                enqueued,
                attempts,
                key=item_id,
            )

        if current_cursor is not None and current_cursor < len(self._loaded_items):
            table.move_cursor(row=current_cursor)

        status_msg = (
            f"Total: {len(loaded)} items  ·  "
            f"[yellow]Pending: {counts.get('pending', 0)}[/yellow]  ·  "
            f"[cyan]Running: {counts.get('running', 0)}[/cyan]  ·  "
            f"[green]Done: {counts.get('done', 0)}[/green]  ·  "
            f"[red]Failed: {counts.get('failed', 0)}[/red]  ·  "
            f"[magenta]Interrupted: {counts.get('interrupted', 0)}[/magenta]"
        )
        self.query_one("#queue-status-text", Static).update(status_msg)

        self._update_item_detail()
        self._update_buttons(counts)

    def _update_item_detail(self) -> None:
        table = self.query_one("#queue-table", DataTable)
        detail_box = self.query_one("#queue-item-detail", Static)

        if table.cursor_row is None or not self._loaded_items or table.cursor_row >= len(self._loaded_items):
            detail_box.update(t("select_queue_item_view_details"))
            return

        item, path = self._loaded_items[table.cursor_row]
        lines = [
            f"[bold cyan]Item ID:[/] {item.get('id', '?')}   [bold cyan]Queue File:[/] {os.path.basename(path)}",
            f"[bold cyan]Status:[/] {item.get('status', '?')}   [bold cyan]Attempts:[/] {item.get('attempts', 0)}",
            f"[bold cyan]Command:[/] python manual.py {' '.join(item.get('argv', []))}",
            f"[bold cyan]Enqueued:[/] {item.get('enqueued_at', '-')}   [bold cyan]Started:[/] {item.get('started_at', '-')}   [bold cyan]Finished:[/] {item.get('finished_at', '-')}",
        ]
        if item.get("returncode") is not None:
            lines.append(f"[bold cyan]Return Code:[/] {item.get('returncode')}")

        detail_box.update("\n".join(lines))

    def _update_buttons(self, counts: dict[str, int]) -> None:
        run_btn = self.query_one("#run-queue-btn", Button)
        remove_btn = self.query_one("#remove-btn", Button)
        retry_btn = self.query_one("#retry-btn", Button)
        clear_btn = self.query_one("#clear-queue-btn", Button)

        has_runnable = (counts.get("pending", 0) + counts.get("interrupted", 0)) > 0
        run_btn.disabled = self._is_worker_running or not has_runnable
        clear_btn.disabled = self._is_worker_running or len(self._loaded_items) == 0

        table = self.query_one("#queue-table", DataTable)
        if table.cursor_row is not None and table.cursor_row < len(self._loaded_items):
            item, _ = self._loaded_items[table.cursor_row]
            st = item.get("status")
            remove_btn.disabled = st == "running" or self._is_worker_running
            retry_btn.disabled = st not in ("failed", "interrupted")
        else:
            remove_btn.disabled = True
            retry_btn.disabled = True

    @on(DataTable.RowSelected, "#queue-table")
    @on(DataTable.RowHighlighted, "#queue-table")
    def _on_row_changed(self) -> None:
        self._update_item_detail()
        counts = {}
        for item, _ in self._loaded_items:
            st = item.get("status", "?")
            counts[st] = counts.get(st, 0) + 1
        self._update_buttons(counts)

    # ── Actions ───────────────────────────────────────────────────────────

    @on(Button.Pressed, "#run-queue-btn")
    def _on_run_queue(self) -> None:
        if self._is_worker_running:
            return
        self._is_worker_running = True
        self.query_one("#run-queue-btn", Button).disabled = True
        self.app.notify("Starting background queue worker...", severity="information")
        self._run_queue_worker()

    @work(thread=True, exclusive=True, group="queue_runner")
    def _run_queue_worker(self) -> None:
        paths = _all_queue_paths()
        processed = 0

        while True:
            item, path = _claim_next_any(paths, None)
            if item is None:
                break

            processed += 1
            self.app.call_from_thread(self._refresh_queue)

            cmd = _child_command(item["argv"])
            try:
                proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL)
                rc = proc.wait()
                status = "done" if rc == 0 else "failed"
            except Exception:
                logger.exception("Error executing queued item %s", item.get("id"))
                rc = -1
                status = "failed"

            _finish_item(path, item["id"], status, rc)
            self.app.call_from_thread(self._refresh_queue)

        self._is_worker_running = False
        msg = f"Queue runner finished: {processed} job(s) processed." if processed > 0 else "Queue runner finished: no pending jobs."
        self.app.call_from_thread(self.app.notify, msg, severity="information")
        self.app.call_from_thread(self._refresh_queue)

    @on(Button.Pressed, "#remove-btn")
    def _on_remove(self) -> None:
        table = self.query_one("#queue-table", DataTable)
        if table.cursor_row is None or table.cursor_row >= len(self._loaded_items):
            return
        item, _ = self._loaded_items[table.cursor_row]
        item_id = item.get("id")
        if not item_id:
            return

        try:
            remove(item_id)
            self.app.notify(f"Removed item '{item_id}'", severity="information")
        except Exception as e:
            self.app.notify(f"Could not remove item: {e}", severity="error")
        self._refresh_queue()

    @on(Button.Pressed, "#retry-btn")
    def _on_retry(self) -> None:
        table = self.query_one("#queue-table", DataTable)
        if table.cursor_row is None or table.cursor_row >= len(self._loaded_items):
            return
        item, path = self._loaded_items[table.cursor_row]
        item_id = item.get("id")
        if not item_id or not path:
            return

        try:
            with _QueueLock(path):
                data = _load_queue(path)
                for it in data.get("items", []):
                    if it.get("id") == item_id:
                        it["status"] = "pending"
                        it["finished_at"] = None
                        it["returncode"] = None
                        break
                _save_queue(path, data)
            self.app.notify(f"Reset item '{item_id}' to pending", severity="information")
        except Exception as e:
            self.app.notify(f"Could not retry item: {e}", severity="error")
        self._refresh_queue()

    @on(Button.Pressed, "#clear-queue-btn")
    def _on_clear_queue(self) -> None:
        try:
            clear()
            self.app.notify("Cleared all queues", severity="information")
        except Exception as e:
            self.app.notify(f"Could not clear queue: {e}", severity="error")
        self._refresh_queue()

    @on(Button.Pressed, "#add-command-btn")
    def _on_add_command(self) -> None:
        self.app.push_screen(EnqueueModal(), callback=self._on_enqueue_modal_dismissed)

    def _on_enqueue_modal_dismissed(self, raw_cmd: str | None) -> None:
        if not raw_cmd:
            return
        try:
            argv = shlex.split(raw_cmd)
        except Exception as e:
            self.app.notify(f"Invalid command string: {e}", severity="error")
            return

        try:
            item = enqueue_argv(argv)
            self.app.notify(f"Enqueued job {item['id']}", severity="information")
        except Exception as e:
            self.app.notify(f"Enqueue failed: {e}", severity="error")

        self._refresh_queue()
