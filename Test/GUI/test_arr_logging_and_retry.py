# 29.10.26
# ruff: noqa: E402

import logging
import logging.handlers
import os
import sys
import time
from pathlib import Path

import pytest

repo_root = Path(__file__).resolve().parents[2]
gui_dir = repo_root / "GUI"
for p in (str(repo_root), str(gui_dir)):
    if p not in sys.path:
        sys.path.insert(0, p)

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "webgui.settings")

import django

django.setup()

from django.conf import settings
from django.core.management import call_command

from searchapp import log_retention
from searchapp.arr import arr_service
from searchapp.arr.item_log import NoItemFilter, item_log
from searchapp.models import ArrProcessingQueue
from searchapp.views.logs_view import _tail_lines


# ── #787: tail without loading the file ──────────────────────────────────────────
@pytest.mark.parametrize("content", [b"", b"a", b"a\nb\nc", b"a\nb\nc\n"])
@pytest.mark.parametrize("count", [1, 2, 10])
def test_tail_small_files(tmp_path, content, count):
    path = tmp_path / "x.log"
    path.write_bytes(content)
    assert _tail_lines(path, count) == content.decode().splitlines()[-count:]


@pytest.mark.parametrize("count", [1, 500, 2000, 300000])
def test_tail_spans_blocks_with_multibyte_lines(tmp_path, count):
    lines = [f"{i} \U0001F4E1 poll" for i in range(200000)]
    path = tmp_path / "x.log"
    path.write_bytes("\n".join(lines).encode())
    assert _tail_lines(path, count) == lines[-count:]


def test_tail_byte_cap_drops_the_partial_first_line(tmp_path):
    path = tmp_path / "x.log"
    path.write_text("x" * 5000 + "\nfine\nend\n", encoding="utf-8")
    assert _tail_lines(path, 10, max_bytes=100) == ["fine", "end"]


# ── #788: retention + per-item logs ──────────────────────────────────────────────
def test_purge_old_logs_keeps_recent_and_active_files(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "PROJECT_ROOT", tmp_path, raising=False)
    arr = tmp_path / ".cache" / "arr"
    arr.mkdir(parents=True)
    old, recent, rotated, active, other = (arr / n for n in ("old.log", "recent.log", "old.log.1", "active.log", "keep.txt"))
    for f in (old, recent, rotated, active, other):
        f.write_text("x")
    stale = time.time() - 8 * 86400
    for f in (old, rotated, active, other):
        os.utime(f, (stale, stale))

    handler = logging.FileHandler(active)
    logging.getLogger("test_purge").addHandler(handler)
    try:
        assert log_retention.purge_old_logs(7) == 2
    finally:
        logging.getLogger("test_purge").removeHandler(handler)
        handler.close()

    assert not old.exists() and not rotated.exists()
    assert recent.exists() and active.exists() and other.exists()
    assert log_retention.purge_old_logs(0) == 0


def test_retention_days_setting(monkeypatch):
    monkeypatch.setenv("LOG_RETENTION_DAYS", "3")
    assert log_retention.retention_days() == 3
    monkeypatch.setenv("LOG_RETENTION_DAYS", "junk")
    assert log_retention.retention_days() == log_retention.DEFAULT_RETENTION_DAYS


def test_item_lines_go_to_their_own_file_only(tmp_path):
    arr_logger = logging.getLogger("ARR")
    global_log = tmp_path / "arr_global.log"
    handler = logging.handlers.RotatingFileHandler(global_log, encoding="utf-8")
    handler.addFilter(NoItemFilter())
    old_level, old_handlers = arr_logger.level, arr_logger.handlers[:]
    arr_logger.handlers = [handler]  # the project's own file handlers would write into .cache/arr
    arr_logger.setLevel(logging.INFO)
    try:
        arr_logger.info("poll started")
        with item_log("Mission: Impossible — Fallout"):
            arr_logger.info("first episode")
        with item_log("Mission: Impossible — Fallout"):
            arr_logger.info("searching item")
            logging.getLogger("ARR.RADARR").info("child logger line")
        arr_logger.info("poll done")
    finally:
        arr_logger.handlers = old_handlers
        arr_logger.setLevel(old_level)
        handler.close()

    assert "poll started" in global_log.read_text(encoding="utf-8")
    assert "searching item" not in global_log.read_text(encoding="utf-8")
    assert "poll done" in global_log.read_text(encoding="utf-8")
    item_files = [f for f in tmp_path.glob("*.log") if f != global_log]
    assert len(item_files) == 1 and item_files[0].name.startswith("Mission-Impossible-Fallout_")
    text = item_files[0].read_text(encoding="utf-8")
    assert "first episode" in text and "searching item" in text and "child logger line" in text and "poll" not in text
    assert handler not in arr_logger.handlers


# ── #789: max attempts ──────────────────────────────────────────────────────────
@pytest.fixture
def queue_db():  # noqa: PT004
    call_command("migrate", verbosity=0, interactive=False)
    ArrProcessingQueue.objects.all().delete()
    yield
    ArrProcessingQueue.objects.all().delete()


def _fail_once(item):
    assert arr_service._enqueue_if_new(item, "polling")
    arr_service._mark_failed(item, "search_no_results")


def test_polling_stops_retrying_after_max_attempts(queue_db, monkeypatch):
    monkeypatch.setattr(arr_service, "_load_arr_config", lambda: {"max_retry_attempts": 3})
    item = {"id": 267, "title": "Not on any provider", "content_type": "movie", "tmdbId": 1}

    for _ in range(3):
        _fail_once(item)

    assert ArrProcessingQueue.objects.get().attempts == 3
    assert arr_service._enqueue_if_new(item, "polling") is False
    assert ArrProcessingQueue.objects.get().completed_at is not None  # stays closed, not reopened

    # An explicit request (webhook) gets a fresh round
    assert arr_service._enqueue_if_new(item, "webhook") is True
    assert ArrProcessingQueue.objects.get().attempts == 0


def test_unlimited_and_unmonitored_failures_do_not_count(queue_db, monkeypatch):
    monkeypatch.setattr(arr_service, "_load_arr_config", lambda: {"max_retry_attempts": 0})
    item = {"id": 5, "title": "Always retried", "content_type": "movie", "tmdbId": 2}
    for _ in range(4):
        _fail_once(item)
    assert arr_service._enqueue_if_new(item, "polling") is True

    arr_service._mark_failed(item, "radarr_unmonitored")
    assert ArrProcessingQueue.objects.get().attempts == 4


def test_success_resets_attempts(queue_db, monkeypatch):
    monkeypatch.setattr(arr_service, "_load_arr_config", lambda: {"max_retry_attempts": 3})
    item = {"id": 9, "title": "Found later", "content_type": "movie", "tmdbId": 3}
    _fail_once(item)
    assert arr_service._enqueue_if_new(item, "polling")
    arr_service._mark_completed(item)
    assert ArrProcessingQueue.objects.get().attempts == 0
