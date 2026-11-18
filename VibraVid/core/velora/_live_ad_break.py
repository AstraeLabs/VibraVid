# 31.10.26

import time
from collections.abc import Callable

AD_BREAK_MAX_SECONDS = 900.0


class LiveAdBreak:
    def __init__(self, max_seconds: float = AD_BREAK_MAX_SECONDS, clock: Callable[[], float] = time.monotonic):
        self._max_seconds = max_seconds
        self._clock = clock
        self._since: float | None = None

    def holds(self, ads_in_manifest: bool) -> bool:
        """For a poll with no new content: True while it is an ad break still within the limit."""
        if not ads_in_manifest:
            self._since = None
            return False
        if self._since is None:
            self._since = self._clock()
        return self._clock() - self._since < self._max_seconds

    def is_started(self) -> bool:
        return self._since is not None

    def elapsed(self) -> float:
        """Seconds the current break has lasted (0 when there is none)."""
        return self._clock() - self._since if self._since is not None else 0.0

    def reset(self) -> None:
        """New content arrived: the break is over."""
        self._since = None
