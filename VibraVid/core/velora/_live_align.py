# 31.10.26

import threading
import time
from collections.abc import Callable
from typing import Any

ALIGN_TIMEOUT_SECONDS = 600.0


class LiveStartAligner:
    def __init__(self, tracks: list):
        self._tracks = tracks
        self._lock = threading.Lock()
        self._starts: dict[int, float] = {}
        self._out: set[int] = set()
        self._ready = threading.Event()

    @classmethod
    def create(cls, owner: Any) -> "LiveStartAligner | None":
        """The aligner for the live video/audio tracks of *owner* (a MediaDownloader), or None when there is a single one to align."""
        media = [
            s
            for s in getattr(owner, "streams", None) or []
            if getattr(s, "selected", False)
            and not getattr(s, "is_external", False)
            and getattr(s, "type", None) in ("video", "audio")
            and getattr(s, "is_live", False)
        ]
        return cls(media) if len(media) > 1 else None

    def _index(self, stream: Any) -> int | None:
        return next((i for i, track in enumerate(self._tracks) if track is stream), None)

    def _check_ready(self) -> None:
        if all(i in self._starts or i in self._out for i in range(len(self._tracks))):
            self._ready.set()

    def common_start(self, stream: Any, start: float, stop_check: Callable[[], bool]) -> float | None:
        """Report that *stream* starts at *start* seconds; blocks until every other track did, then returns the latest start. None if not aligned (unknown track, stop, timeout)."""
        index = self._index(stream)
        if index is None:
            return None
        with self._lock:
            self._starts[index] = start
            self._check_ready()

        deadline = time.monotonic() + ALIGN_TIMEOUT_SECONDS
        while not self._ready.wait(timeout=1.0):
            if stop_check() or time.monotonic() >= deadline:
                return None
        with self._lock:
            return max(self._starts.values())

    def leave(self, stream: Any) -> None:
        """*stream* will not report a start (no usable segment, or it stopped): the others no longer wait for it."""
        index = self._index(stream)
        if index is None:
            return
        with self._lock:
            self._out.add(index)
            self._check_ready()
