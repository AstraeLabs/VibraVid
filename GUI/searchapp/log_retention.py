# 29.10.26

import logging
import os
import time
from pathlib import Path

from django.conf import settings

from VibraVid.utils import config_manager

logger = logging.getLogger(__name__)

LOG_DIRS = (".cache/logs", ".cache/arr")
DEFAULT_RETENTION_DAYS = 7


def active_log_files() -> set[Path]:
    """Files currently open by a logging file handler (never deleted, only truncated)."""
    active = set()
    for log in [logging.getLogger(), *logging.Logger.manager.loggerDict.values()]:
        if isinstance(log, logging.Logger):
            for handler in log.handlers:
                if isinstance(handler, logging.FileHandler):
                    active.add(Path(handler.baseFilename).resolve())
    return active


def retention_days() -> int:
    """`[DEFAULT] log_retention_days` (env `LOG_RETENTION_DAYS` wins); 0 keeps every log."""
    raw = os.environ.get("LOG_RETENTION_DAYS")
    if raw is None:
        raw = config_manager.config.get("DEFAULT", "log_retention_days", default=DEFAULT_RETENTION_DAYS)
    try:
        return max(0, int(raw))
    except (TypeError, ValueError):
        return DEFAULT_RETENTION_DAYS


def purge_old_logs(days: int | None = None) -> int:
    """Delete log files (rotated `.log.N` included) not modified for `days` days. Returns the count."""
    days = retention_days() if days is None else days
    if days <= 0:
        return 0

    root = Path(getattr(settings, "PROJECT_ROOT", None) or Path(settings.BASE_DIR).parent)
    cutoff = time.time() - days * 86400
    active = active_log_files()
    removed = 0
    for rel in LOG_DIRS:
        directory = root / rel
        if not directory.is_dir():
            continue
        for path in directory.iterdir():
            if ".log" not in path.suffixes or path.is_symlink() or not path.is_file() or path.resolve() in active:
                continue
            try:
                if path.stat().st_mtime < cutoff:
                    path.unlink()
                    removed += 1
            except OSError as exc:
                logger.debug("Log '%s' not removed: %s", path, exc)
    if removed:
        logger.info("Removed %d log file(s) older than %d day(s)", removed, days)
    return removed
