# 26.06.26

import logging

_buffer_handler = None


class _StartupBufferHandler(logging.Handler):
    """Handler that stores records in memory instead of writing them out."""

    def __init__(self):
        super().__init__(level=logging.DEBUG)
        self.records = []

    def emit(self, record):
        self.records.append(record)


def install_startup_buffer():
    """Park an in-memory handler on the root logger (idempotent)."""
    global _buffer_handler
    if _buffer_handler is not None:
        return

    root = logging.getLogger()

    # Default root level is WARNING, which would drop INFO/DEBUG records before
    # they ever reach a handler. Capture everything; the final level filtering
    # happens at flush time against the real file handler's level.
    root.setLevel(logging.DEBUG)

    _buffer_handler = _StartupBufferHandler()
    root.addHandler(_buffer_handler)


def flush_startup_buffer(target_handler=None):
    """Replay buffered records into ``target_handler`` and remove the buffer.

    When ``target_handler`` is ``None`` (e.g. ``--no-log``) the buffer is simply
    discarded so the records don't pile up in memory for the session.
    """
    global _buffer_handler
    if _buffer_handler is None:
        return

    root = logging.getLogger()
    root.removeHandler(_buffer_handler)

    if target_handler is not None:
        for record in _buffer_handler.records:
            if record.levelno >= target_handler.level:
                target_handler.handle(record)

    _buffer_handler.records.clear()
    _buffer_handler = None
