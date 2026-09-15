# 13.03.26

import logging
import platform
import threading
from contextlib import nullcontext
from typing import Any

from rich.console import Console, Group
from rich.live import Live
from rich.progress import Progress, TextColumn
from rich.text import Text

from VibraVid.core.ui.progress_bar import (
    SHOW_ELAPSED_REMAINING,
    CompactTimeRemainingColumn,
    CustomBarColumn,
    StatusLine,
    TransferStatsColumn,
)
from VibraVid.core.ui.tracker import context_tracker, download_tracker

logger = logging.getLogger(__name__)

console = Console(force_terminal=True if platform.system().lower() != "windows" else None)

# Registry so any thread (e.g. a service's background status poller) can reach the
# DownloadBarManager for a given download_id and call set_status_text on it
_registry: dict[str, "DownloadBarManager"] = {}
_registry_lock = threading.Lock()


def get_bar_manager(download_id: str | None) -> "DownloadBarManager | None":
    if not download_id:
        return None
    with _registry_lock:
        return _registry.get(download_id)


class DownloadBarManager:
    def __init__(self, download_id: str | None = None):
        self.download_id = download_id
        self.tasks: dict[str, Any] = {}
        self.subtitle_sizes: dict[str, str] = {}
        self.status_line = StatusLine()
        time_columns = []
        if SHOW_ELAPSED_REMAINING:
            time_columns = [
                TextColumn("[dim]·[/dim]"),
                CompactTimeRemainingColumn(),
            ]

        self.progress = None
        self._live: Live | None = None
        self.progress_ctx = nullcontext()
        if not context_tracker.is_gui:
            self.progress = Progress(
                TextColumn("[purple]{task.description}", justify="left"),
                CustomBarColumn(),
                TextColumn("[dim]|[/dim]"),
                TransferStatsColumn(),
                *time_columns,
                console=console,
                refresh_per_second=5.0,
            )
            self._live = Live(Group(self.status_line, self.progress), console=console, refresh_per_second=5.0)

    def __enter__(self):
        if self._live:
            self._live.__enter__()
        else:
            self.progress_ctx.__enter__()
        if self.download_id:
            with _registry_lock:
                _registry[self.download_id] = self
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self.download_id:
            with _registry_lock:
                _registry.pop(self.download_id, None)
        if self._live:
            self._live.__exit__(exc_type, exc_val, exc_tb)
        else:
            self.progress_ctx.__exit__(exc_type, exc_val, exc_tb)

    def set_status_text(self, text: str) -> None:
        """Set/update the single status line rendered above the progress bars."""
        self.status_line.set_text(text)
        plain_text = Text.from_markup(text).plain
        if plain_text:
            logger.info(f"[msg_room] {plain_text}")

    @staticmethod
    def _wrap_label(label: str) -> str:
        """Wrap a plain label in [cyan] markup unless it already contains Rich markup."""
        return label if label.startswith("[") else f"[cyan]{label}"

    def add_prebuilt_tasks(self, prebuilt_tasks):
        """Pre-crates tasks to maintain order."""
        if self.progress:
            for task_key, task_label in prebuilt_tasks:
                if task_key not in self.tasks:
                    # If task_label already contains Rich markup (starts with [), use it as-is otherwise wrap it with [cyan] for consistency
                    final_label = task_label if task_label.startswith("[") else f"[cyan]{task_label}[/cyan]"
                    initial_segment = "0/100" if task_key.startswith("decrypt_") else "0/0"
                    compact_metrics = task_key.startswith("decrypt_")
                    self.tasks[task_key] = self.progress.add_task(
                        final_label,
                        total=100,
                        segment=initial_segment,
                        speed="" if compact_metrics else "0Bps",
                        size="" if compact_metrics else "0B/0B",
                        duration="",
                        compact_metrics=compact_metrics,
                    )

    def add_external_track_task(self, label: str, track_key: str):
        if self.progress:
            if track_key not in self.tasks:
                self.tasks[track_key] = self.progress.add_task(
                    self._wrap_label(label),
                    total=100,
                    segment="0/1",
                    speed="0Bps",
                    size="0B/0B",
                    compact_metrics=False,
                )

    def handle_progress_line(self, parsed: dict[str, Any] | None):
        if not parsed:
            return

        key = (
            parsed.get("task_key")
            or parsed.get("_task_key")
            or f"{parsed.get('track', 'trk')}_{parsed.get('label', '')}"
        )
        label = parsed.get("label", key)

        # ── Create task if first time we see this key ──────────────────────
        if key not in self.tasks:
            compact_metrics = bool(parsed.get("compact_metrics")) or key.startswith("decrypt_")
            self.tasks[key] = (
                self.progress.add_task(
                    self._wrap_label(label),
                    total=100,
                    segment="0/0",
                    speed="" if compact_metrics else "0Bps",
                    size="" if compact_metrics else "0B/0B",
                    duration="",
                    compact_metrics=compact_metrics,
                )
                if self.progress
                else "gui"
            )

        # ── Update tracker (for GUI mode) ──────────────────────────────────
        if self.download_id:
            download_tracker.update_progress(
                self.download_id,
                key,
                parsed.get("pct"),
                parsed.get("speed"),
                parsed.get("size"),
                parsed.get("segments"),
                label=label,
                display_label=parsed.get("display_label"),
                quality=parsed.get("quality"),
                language=parsed.get("language"),
            )

        # ── Update Rich progress bar ───────────────────────────────────────
        if not self.progress or self.tasks.get(key) == "gui":
            return

        tid = self.tasks[key]
        fields: dict[str, Any] = {}
        if "compact_metrics" in parsed:
            fields["compact_metrics"] = bool(parsed["compact_metrics"])
        if "speed" in parsed and not parsed.get("compact_metrics"):
            fields["speed"] = parsed["speed"]
        if "size" in parsed and not parsed.get("compact_metrics"):
            fields["size"] = parsed["size"]
        if "segments" in parsed:
            fields["segment"] = parsed["segments"]
        if "duration" in parsed and not parsed.get("compact_metrics"):
            fields["duration"] = parsed["duration"]

        completed = parsed["pct"] if "pct" in parsed else None
        try:
            self.progress.update(tid, completed=completed, **fields)
        except Exception:
            pass

        # Subtitle completion
        if "final_size" in parsed:
            self.progress.update(tid, size=parsed["final_size"], completed=100)
            lang_raw = parsed.get("_lang_code") or key.replace("sub_", "", 1).split("_")[0]
            codec = parsed.get("codec", "")
            if lang_raw:
                self.subtitle_sizes[f"{lang_raw}:{codec}" if codec else lang_raw] = parsed["final_size"]

    def finish_all_tasks(self):
        if self.progress:
            for tid in self.tasks.values():
                if tid != "gui":
                    self.progress.update(tid, completed=100)
