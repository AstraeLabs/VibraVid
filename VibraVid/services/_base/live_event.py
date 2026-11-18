# 30.10.26

import logging
import threading
from collections.abc import Callable
from datetime import UTC, datetime

logger = logging.getLogger(__name__)
START_MARGIN = 10.0


class LiveEvent:
    """When a scheduled live event started, for services whose manifest never says it is over."""
    def __init__(self, starts_at: datetime | None, is_linear: bool = False):
        self.starts_at = starts_at
        self.is_linear = is_linear

    def elapsed_seconds(self) -> float | None:
        """Seconds since the event started (plus a margin): what to ask as ``live_tail_seconds`` to record it from the start. None for 24/7 channels and events that have not started."""
        if self.is_linear or self.starts_at is None:
            return None
        elapsed = (datetime.now(UTC) - self.starts_at).total_seconds()
        return elapsed + START_MARGIN if elapsed > 0 else None


class LiveEndWatcher:
    def __init__(self, is_live: Callable[[], bool], interval: float = 30.0, misses: int = 3):
        self._is_live = is_live
        self._interval = interval
        self._misses = misses
        self._ended = threading.Event()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def is_ended(self) -> bool:
        return self._ended.is_set()

    def _watch(self) -> None:
        missing = 0
        while not self._stop.wait(self._interval):
            try:
                live = bool(self._is_live())
            except Exception as error:
                logger.warning("Live status not checked: %s", error)
                continue
            missing = 0 if live else missing + 1
            if missing >= self._misses:
                logger.info("The event is no longer live (%d checks in a row): the live is over, stopping the recording", missing)
                self._ended.set()
                return

    def __enter__(self) -> "LiveEndWatcher":
        self._thread = threading.Thread(target=self._watch, name="live-end-watcher", daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *exc) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=15)
