# 29.10.26

import logging
import logging.handlers
import re
import unicodedata
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime
from pathlib import Path

_ARR_LOGGER = "ARR"
_current_item: ContextVar[object | None] = ContextVar("arr_item_log", default=None)


class NoItemFilter(logging.Filter):
    """Drop records emitted while an ARR item is processed: they live in the item's own file."""
    def filter(self, record: logging.LogRecord) -> bool:
        return _current_item.get() is None


class _ItemFilter(logging.Filter):
    """Accept only the records emitted inside one `item_log` context."""

    def __init__(self, token: object):
        super().__init__()
        self._token = token

    def filter(self, record: logging.LogRecord) -> bool:
        return _current_item.get() is self._token


def _slug(title: str) -> str:
    ascii_title = unicodedata.normalize("NFKD", title or "").encode("ascii", "ignore").decode()
    return re.sub(r"[^A-Za-z0-9]+", "-", ascii_title).strip("-")[:60] or "item"


def _arr_log_dir(arr_logger: logging.Logger) -> Path | None:
    """Directory of the ARR logger's file handler (so tests and Docker follow the same path)."""
    for handler in arr_logger.handlers:
        if isinstance(handler, logging.FileHandler):
            return Path(handler.baseFilename).parent
    return None


@contextmanager
def item_log(title: str):
    """Send every ARR log line emitted in this block to the title's own `<title>_<date>.log`."""
    arr_logger = logging.getLogger(_ARR_LOGGER)
    directory = _arr_log_dir(arr_logger)
    if _current_item.get() is not None or directory is None:
        yield
        return

    name = f"{_slug(title)}_{datetime.now().strftime('%Y%m%d')}.log"
    token = object()

    handler = None
    try:
        directory.mkdir(parents=True, exist_ok=True)
        handler = logging.handlers.RotatingFileHandler(
            directory / name, maxBytes=10 * 1024 * 1024, backupCount=5, encoding="utf-8"
        )
        file_handler = next((h for h in arr_logger.handlers if isinstance(h, logging.FileHandler)), None)
        handler.setFormatter(file_handler.formatter if file_handler and file_handler.formatter else logging.Formatter("{asctime} [{levelname}] {name}: {message}", style="{"))
        handler.addFilter(_ItemFilter(token))
        arr_logger.addHandler(handler)
    except OSError as exc:
        arr_logger.warning(f"Could not create the item log '{name}': {exc}")
        handler = None
        yield
        return

    ctx = _current_item.set(token)
    try:
        yield
    finally:
        _current_item.reset(ctx)
        arr_logger.removeHandler(handler)
        handler.close()
