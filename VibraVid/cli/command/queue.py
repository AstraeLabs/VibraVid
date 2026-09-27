# 22.07.26

import json
import logging
import os
import re
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

from rich.console import Console
from rich.prompt import Prompt

from VibraVid.core.ui.tracker import context_tracker
from VibraVid.setup.system import is_binary_installation
from VibraVid.utils import TVShowManager, config_manager

logger = logging.getLogger(__name__)
console = Console()

_RESUMABLE_STATUSES = ("pending", "interrupted")
_STATUS_COLOR = {"pending": "yellow", "running": "cyan", "done": "green", "failed": "red", "interrupted": "magenta"}

_QUEUE_FLAGS_BOOL = {"--queue-add", "--queue-run", "--queue-list", "--queue-clear", "--queue-select"}
_QUEUE_FLAGS_VALUE = {"--queue-remove", "--queue-ids"}
_PROCESS_TAG = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")


def _queue_dir() -> str:
    return os.path.join(config_manager.base_path, ".cache", "queue")


def _queue_path(tag: str | None = None) -> str:
    name = f"queue_{tag}.json" if tag else "queue.json"
    return os.path.join(_queue_dir(), name)


def _lock_path_for(queue_path: str) -> str:
    return os.path.splitext(queue_path)[0] + ".lock"


def _all_queue_paths() -> list:
    d = _queue_dir()
    if not os.path.isdir(d):
        return []
    return sorted(
        os.path.join(d, name) for name in os.listdir(d) if name.startswith("queue") and name.endswith(".json")
    )


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def add_queue_arguments(parser) -> None:
    """CLI-only batch queue. Independent of the GUI's scheduled_downloads and ARR's DB-backed queue."""
    group = parser.add_argument_group("Queue (CLI batch downloads)")
    group.add_argument("--queue-add", dest="queue_add", action="store_true", help="Enqueue this invocation instead of running it now (requires --down, or -i + --search + --item)")
    group.add_argument( "--queue-run", dest="queue_run", nargs="?", const=True, default=False, metavar="QUEUE_NAME", help='Process every pending/interrupted queued item, one at a time. Optionally pass a queue name (as shown by --queue-list, e.g. "20260723-152525") to restrict to just that one batch.')
    group.add_argument("--queue-list", dest="queue_list", nargs="?", const=True, default=False, metavar="QUEUE_NAME", help="Without a name: show one summary line per queue (item count, status breakdown, completed or not). With a queue name: list every item in that one queue individually.")
    group.add_argument("--queue-remove", dest="queue_remove", metavar="ID", help="Remove one queued item by id (must not be running)")
    group.add_argument("--queue-clear", dest="queue_clear", nargs="?", const=True, default=False, metavar="QUEUE_NAME", help="Remove every queued item. Optionally pass a queue name to only clear that one batch.")
    group.add_argument("--queue-ids", dest="queue_ids", metavar="ID1,ID2,...", help="Restrict --queue-run to only these specific item ids (comma-separated, as shown by --queue-list) instead of every pending item.")
    group.add_argument("--queue-select", dest="queue_select", action="store_true", help="Show a numbered list of existing queues (like --queue-list) and prompt for which ONE to run — then downloads every item in that queue that is not already done.")


def _load_queue(path: str) -> dict:
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        if isinstance(data, dict) and isinstance(data.get("items"), list):
            return data
    except FileNotFoundError:
        pass
    except Exception as e:
        logger.warning(f"[queue] could not parse {path}, starting fresh: {e}")
    return {"version": 1, "items": []}


def _save_queue(path: str, data: dict) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp_path = f"{path}.tmp-{os.getpid()}"
    with open(tmp_path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2)
    os.replace(tmp_path, path)


def _pid_alive(pid: int) -> bool:
    try:
        if sys.platform == "win32":
            result = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"], capture_output=True, text=True, timeout=5)
            return str(pid) in result.stdout
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except Exception:
        return True  # unknown -> assume alive; safer than deleting a live process's lock


def _holder_pid(lock_path: str) -> int | None:
    try:
        with open(lock_path, encoding="utf-8") as fh:
            content = fh.read()
        m = re.search(r"pid=(\d+)", content)
        return int(m.group(1)) if m else None
    except Exception:
        return None


class _QueueLock:
    def __init__(self, queue_path: str, timeout: float = 5.0, poll: float = 0.2):
        self._lock_path = _lock_path_for(queue_path)
        self._timeout = timeout
        self._poll = poll
        self._acquired = False

    def acquire(self) -> bool:
        path = self._lock_path
        os.makedirs(os.path.dirname(path), exist_ok=True)
        deadline = time.time() + self._timeout
        stale_checked = False
        while True:
            try:
                fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                with os.fdopen(fd, "w") as fh:
                    fh.write(f"pid={os.getpid()} at={_now_iso()}\n")
                self._acquired = True
                return True
            except FileExistsError:
                if not stale_checked:
                    stale_checked = True
                    holder_pid = _holder_pid(path)
                    if holder_pid and not _pid_alive(holder_pid):
                        try:
                            os.remove(path)
                            logger.info(f"[queue] removed stale lock (dead pid {holder_pid}): {path}")
                            continue
                        except FileNotFoundError:
                            continue
                if time.time() >= deadline:
                    return False
                time.sleep(self._poll)

    def release(self) -> None:
        if not self._acquired:
            return
        try:
            os.remove(self._lock_path)
        except FileNotFoundError:
            pass
        self._acquired = False

    def holder_info(self) -> str:
        try:
            with open(self._lock_path, encoding="utf-8") as fh:
                return fh.read().strip()
        except Exception:
            return "unknown holder"

    def __enter__(self):
        if not self.acquire():
            console.print(f"[red]Queue is busy ({self.holder_info()}). If no VibraVid process is actually running, delete '{self._lock_path}' manually and retry.")
            raise SystemExit(1)
        return self

    def __exit__(self, exc_type, exc, tb):
        self.release()


def _is_enqueueable(args) -> tuple:
    if getattr(args, "down", None):
        return True, None
    if getattr(args, "global_search", False):
        return False, "cannot enqueue a --global search (needs interactive category/result selection)"

    site = getattr(args, "site", None)
    search = getattr(args, "search", None)
    has_item = getattr(args, "item", None) is not None
    if site and search and has_item:
        return True, None

    return (False, "enqueue requires either --down, or -i + --search + --item N - this invocation would need interactive input")


def _strip_queue_flags(argv: list) -> list:
    result = []
    skip_next = False
    for tok in argv:
        if skip_next:
            skip_next = False
            continue
        base = tok.split("=", 1)[0]
        if base in _QUEUE_FLAGS_BOOL:
            continue
        if base in _QUEUE_FLAGS_VALUE:
            if "=" not in tok:
                skip_next = True
            continue
        result.append(tok)
    return result


def enqueue(argv: list, args) -> None:
    ok, reason = _is_enqueueable(args)
    if not ok:
        console.print(f"[red]Cannot enqueue: {reason}")
        raise SystemExit(1)

    tag = _PROCESS_TAG
    path = _queue_path(tag)

    item = {
        "id": uuid.uuid4().hex[:8],
        "argv": _strip_queue_flags(argv),
        "status": "pending",
        "tag": tag,
        "enqueued_at": _now_iso(),
        "started_at": None,
        "finished_at": None,
        "returncode": None,
        "attempts": 0,
    }

    with _QueueLock(path):
        data = _load_queue(path)
        data["items"].append(item)
        _save_queue(path, data)

    logger.info(f"Queued item {item['id']} [tag={tag}]: {' '.join(item['argv'])}")
    queue_name = os.path.splitext(os.path.basename(path))[0]
    console.print(f"[green]Added to queue[/green]: [cyan]{queue_name}[/cyan]")


def enqueue_down_from_context(url: str, output_path: str) -> None:
    argv = ["--down", url, "--type", "hls", "-o", output_path]
    if context_tracker.title:
        argv += ["--meta-title", context_tracker.title]
    if context_tracker.media_type:
        argv += ["--meta-type", context_tracker.media_type]
    if context_tracker.site_name:
        argv += ["--meta-site", context_tracker.site_name]
    if context_tracker.season:
        argv += ["--meta-season", str(context_tracker.season)]
    if context_tracker.episode:
        argv += ["--meta-episode", str(context_tracker.episode)]

    fake_args = SimpleNamespace(down=url, global_search=False, site=None, search=None, item=None)
    enqueue(argv, fake_args)


def _paths_for(tag: str | None) -> list:
    return [_queue_path(tag)] if tag else _all_queue_paths()


def list_queue(tag: str | None = None) -> None:
    if tag:
        _list_queue_detail(tag)
        return

    paths = _all_queue_paths()
    rows = []
    for path in paths:
        items = _load_queue(path).get("items", [])
        if not items:
            continue

        file_tag = os.path.splitext(os.path.basename(path))[0]  # queue.json -> "queue", queue_<tag>.json -> "queue_<tag>"
        counts = {}
        for it in items:
            counts[it.get("status", "?")] = counts.get(it.get("status", "?"), 0) + 1

        completed = counts.get("done", 0) == len(items)
        rows.append(
            {
                "Queue": file_tag,
                "Items": len(items),
                "Done": counts.get("done", 0),
                "Pending": counts.get("pending", 0),
                "Running": counts.get("running", 0),
                "Failed": counts.get("failed", 0),
                "Interrupted": counts.get("interrupted", 0),
                "Status": "Completed" if completed else "Not completed",
            }
        )

    if not rows:
        console.print("[dim]No queues found.")
        return

    table = TVShowManager()
    table.add_column(
        {
            "Queue": {"color": "cyan"},
            "Items": {"color": "white"},
            "Done": {"color": "green"},
            "Pending": {"color": "yellow"},
            "Running": {"color": "cyan"},
            "Failed": {"color": "red"},
            "Interrupted": {"color": "magenta"},
            "Status": {"color": "white"},
        }
    )
    for row in rows:
        table.add_tv_show(row)
    table.display_data(rows)


def _list_queue_detail(tag: str) -> None:
    all_items = []
    for path in _paths_for(tag):
        if not os.path.exists(path):
            continue
        data = _load_queue(path)
        all_items.extend(data.get("items", []))

    if not all_items:
        console.print(f"[dim]Queue is empty for tag {tag!r}.")
        return

    for item in all_items:
        color = _STATUS_COLOR.get(item.get("status"), "white")
        tag_label = f" [magenta][{item['tag']}][/magenta]" if item.get("tag") else ""
        console.print(f"[{color}]{item.get('status', '?'):<11}[/{color}] [cyan]{item['id']}[/cyan]{tag_label}  {' '.join(item.get('argv', []))}")
        meta = []
        if item.get("enqueued_at"):
            meta.append(f"enqueued {item['enqueued_at']}")
        if item.get("started_at"):
            meta.append(f"started {item['started_at']}")
        if item.get("finished_at"):
            meta.append(f"finished {item['finished_at']} (rc={item.get('returncode')})")
        if meta:
            console.print(f"[dim]  {' | '.join(meta)}[/dim]")


def remove(item_id: str) -> None:
    for path in _all_queue_paths():
        with _QueueLock(path):
            data = _load_queue(path)
            items = data.get("items", [])
            target = next((it for it in items if it["id"] == item_id), None)
            if target is None:
                continue
            if target.get("status") == "running":
                console.print(f"[red]Item '{item_id}' is currently running; cannot remove.")
                raise SystemExit(1)
            data["items"] = [it for it in items if it["id"] != item_id]
            _save_queue(path, data)
        console.print(f"[green]Removed[/green] {item_id}")
        return

    console.print(f"[red]No queued item with id '{item_id}'.")
    raise SystemExit(1)


def clear(tag: str | None = None) -> None:
    paths = [p for p in _paths_for(tag) if os.path.exists(p)]

    total = 0
    for path in paths:
        with _QueueLock(path):
            data = _load_queue(path)
            items = data.get("items", [])
            if any(it.get("status") == "running" for it in items):
                console.print(f"[red]Cannot clear: at least one item is currently running in {os.path.basename(path)}.")
                raise SystemExit(1)
            total += len(items)

    for path in paths:
        try:
            os.remove(path)
        except FileNotFoundError:
            pass

    console.print(f"[green]Cleared[/green] {total} item(s){f' with tag {tag!r}' if tag else ''}.")


def _child_command(item_argv: list) -> list:
    if is_binary_installation:
        return [sys.executable, *item_argv]
    return [sys.executable, sys.argv[0], *item_argv]


def select_and_run_queue() -> None:
    rows = []
    for path in _all_queue_paths():
        items = _load_queue(path).get("items", [])
        pending = sum(1 for it in items if it.get("status") in _RESUMABLE_STATUSES)
        if pending == 0:
            continue  # nothing runnable in this queue

        file_tag = os.path.splitext(os.path.basename(path))[0]  # queue.json -> "queue", queue_<tag>.json -> "queue_<tag>"
        counts = {}
        for it in items:
            counts[it.get("status", "?")] = counts.get(it.get("status", "?"), 0) + 1
        rows.append((file_tag, len(items), counts))

    if not rows:
        console.print("[dim]No queues with pending/interrupted items found.")
        return

    for idx, (file_tag, total, counts) in enumerate(rows, start=1):
        console.print(
            f"[cyan]{idx:>3}[/cyan]  {file_tag}  "
            f"({total} items — [green]done={counts.get('done', 0)}[/green] "
            f"[yellow]pending={counts.get('pending', 0)}[/yellow] "
            f"[red]failed={counts.get('failed', 0)}[/red] "
            f"[magenta]interrupted={counts.get('interrupted', 0)}[/magenta])"
        )

    while True:
        raw = Prompt.ask('\n[cyan]Which queue do you want to run? (number, or "q" to cancel)')
        if raw.strip().lower() in ("q", "quit"):
            console.print("[yellow]Cancelled.")
            return
        if raw.strip().isdigit() and 1 <= int(raw.strip()) <= len(rows):
            break
        console.print(f"[red]Invalid selection. Enter a number 1-{len(rows)}.")

    chosen_file_tag = rows[int(raw.strip()) - 1][0]
    tag = None if chosen_file_tag == "queue" else chosen_file_tag[len("queue_") :]
    run_queue(tag=tag)


def _claim_next_in(path: str, id_set: set | None) -> dict | None:
    if not os.path.exists(path):
        return None

    with _QueueLock(path):
        data = _load_queue(path)
        for it in data["items"]:
            if it.get("status") not in _RESUMABLE_STATUSES:
                continue
            if id_set and it["id"] not in id_set:
                continue

            it["status"] = "running"
            it["started_at"] = _now_iso()
            it["attempts"] = it.get("attempts", 0) + 1
            _save_queue(path, data)
            return dict(it)

    return None


def _claim_next_any(paths: list, id_set: set | None) -> tuple:
    for path in paths:
        item = _claim_next_in(path, id_set)
        if item is not None:
            return item, path
    return None, None


def _finish_item(path: str, item_id: str, status: str, rc: int | None) -> None:
    with _QueueLock(path):
        data = _load_queue(path)
        item = next((it for it in data["items"] if it["id"] == item_id), None)
        if item is not None:
            item["status"] = status
            item["finished_at"] = _now_iso()
            item["returncode"] = rc
            _save_queue(path, data)


def run_queue(tag: str | None = None, ids: list | None = None) -> None:
    id_set = set(ids) if ids else None
    paths = _paths_for(tag)

    if id_set:
        known_ids = set()
        for path in paths:
            if not os.path.exists(path):
                continue
            known_ids.update(it["id"] for it in _load_queue(path)["items"])
        missing = id_set - known_ids
        if missing:
            console.print(f"[yellow]Warning: unknown queue id(s), ignored: {', '.join(sorted(missing))}")

    processed = 0
    while True:
        item, path = _claim_next_any(paths, id_set)
        if item is None:
            break

        processed += 1
        console.print(f"\n[cyan]> [{processed}] {item['id']}[/cyan]: {' '.join(item['argv'])}")

        cmd = _child_command(item["argv"])
        proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL)
        try:
            rc = proc.wait()
        except KeyboardInterrupt:
            # Ctrl+C reaches the child too (shared console) — let it finish its own
            # graceful shutdown instead of killing it out from under it.
            rc = proc.wait()
            _finish_item(path, item["id"], "interrupted", rc)
            console.print(f"\n[yellow]Queue run interrupted at item {item['id']}; remaining items left pending.")
            return

        status = "done" if rc == 0 else "failed"
        _finish_item(path, item["id"], status, rc)
        console.print(f"[{_STATUS_COLOR[status]}]{status}[/{_STATUS_COLOR[status]}] {item['id']} (exit {rc})")

    if processed == 0:
        filters = []
        if tag:
            filters.append(f"tag {tag!r}")
        if ids:
            filters.append(f"ids {ids!r}")
        suffix = f" matching {' and '.join(filters)}" if filters else ""
        console.print(f"[dim]Nothing to run: no pending or interrupted items{suffix}.")
        return

    console.print(f"[cyan]Processing complete: {processed} item(s) handled by this run.")


def _as_tag(value) -> str | None:
    if not isinstance(value, str):
        return None
    return value[len("queue_") :] if value.startswith("queue_") else value


def handle_queue_dispatch(args, argv: list) -> bool:
    if getattr(args, "queue_add", False):
        enqueue(argv, args)
        return True
    if getattr(args, "queue_select", False):
        select_and_run_queue()
        return True
    if getattr(args, "queue_run", False):
        raw_ids = getattr(args, "queue_ids", None)
        ids = [i.strip() for i in raw_ids.split(",") if i.strip()] if raw_ids else None
        run_queue(tag=_as_tag(args.queue_run), ids=ids)
        return True
    if getattr(args, "queue_list", False):
        list_queue(tag=_as_tag(args.queue_list))
        return True
    if getattr(args, "queue_remove", None):
        remove(args.queue_remove)
        return True
    if getattr(args, "queue_clear", False):
        clear(tag=_as_tag(args.queue_clear))
        return True
    return False
