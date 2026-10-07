# 01.04.24

import time
from collections import deque
from pathlib import Path

_NOMINAL_FULL_TRUST_AT = 0.10
SPEED_WINDOW_SECONDS = 3.0


class SpeedWindow:
    def __init__(self, window: float = SPEED_WINDOW_SECONDS):
        self._window = window
        self._samples: deque[tuple[float, int]] = deque([(time.monotonic(), 0)])

    def update(self, total_bytes: int) -> float:
        """Record the running byte total and return the speed over the current window."""
        now = time.monotonic()
        self._samples.append((now, total_bytes))
        while len(self._samples) > 1 and now - self._samples[0][0] > self._window:
            self._samples.popleft()
        window_start_at, window_start_bytes = self._samples[0]
        return (total_bytes - window_start_bytes) / max(now - window_start_at, 0.001)


def normalize_path_key(path_value: str) -> str:
    """
    Return a canonical, case-folded absolute path string suitable for use as a dict key when comparing paths across the Python/C# boundary.
    """
    if not path_value:
        return ""
    return str(Path(path_value).resolve(strict=False)).casefold()


def format_size(nb: int) -> str:
    """Format *nb* bytes as a compact human-readable string."""
    if nb >= 1_073_741_824:
        return f"{nb / 1_073_741_824:.2f}G"
    if nb >= 1_048_576:
        return f"{nb / 1_048_576:.1f}M"
    if nb >= 1_024:
        return f"{nb / 1_024:.0f}K"
    return f"{nb}"


def format_speed(bps: float) -> str:
    """Format *bps* (bytes per second) as a compact human-readable string."""
    if bps <= 0:
        return "---"
    if bps >= 1_048_576:
        return f"{bps / 1_048_576:.2f}M/s"
    if bps >= 1_024:
        return f"{bps / 1_024:.0f}K/s"
    return f"{bps:.0f}/s"


def estimate_total_size(completed_bytes: int, done_segs: int, total_segs: int) -> int:
    """Linearly extrapolate total download size from completed segments."""
    if done_segs <= 0 or total_segs <= 0:
        return completed_bytes
    return int((completed_bytes / done_segs) * total_segs)


def resolve_display_total(
    completed_bytes: int,
    done_segs: int,
    total_segs: int,
    known_total: int = 0,
    known_exact: bool = True,
) -> int:
    """Resolve the total to show as ``downloaded/total``."""
    try:
        known = int(known_total or 0)
    except (TypeError, ValueError):
        known = 0
    try:
        done_bytes = int(completed_bytes or 0)
    except (TypeError, ValueError):
        done_bytes = 0

    observed = estimate_total_size(done_bytes, done_segs, total_segs)
    if known > 0 and not known_exact and done_segs > 0 and total_segs > 0:
        weight = min(1.0, (done_segs / total_segs) / _NOMINAL_FULL_TRUST_AT)
        base = int(known * (1 - weight) + observed * weight)
    else:
        base = known if known > 0 else observed

    return max(base, done_bytes)


def estimate_eta(remaining_bytes: int, speed_bps: float) -> float | None:
    """Seconds left at the current *speed_bps*: ``None`` while the speed is unknown, ``0`` when nothing remains."""
    if remaining_bytes <= 0:
        return 0.0
    if speed_bps <= 0:
        return None
    return remaining_bytes / speed_bps


def fmt_dur(seconds: float) -> str:
    """Format seconds as HH:MM:SS or MM:SS."""
    s = int(seconds)
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{sec:02d}" if h else f"{m:02d}:{sec:02d}"


def parse_time_scalar(s: str) -> float | None:
    """Parse "HH:MM:SS", "MM:SS", or plain seconds → seconds. None on malformed input."""
    s = s.strip()
    if not s:
        return None
    parts = s.split(":")
    try:
        if len(parts) == 3:
            return int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])
        if len(parts) == 2:
            return int(parts[0]) * 60 + float(parts[1])
        return float(s)
    except ValueError:
        return None


def parse_max_time(value: None | int | float | str | tuple[float, float | None]) -> tuple[float, float | None]:
    """Parse a "--max-time" value into a ``(start_seconds, end_seconds)`` range."""
    if isinstance(value, tuple):
        return value
    if value is None:
        return (0.0, None)
    if isinstance(value, (int, float)):
        return (0.0, float(value)) if value > 0 else (0.0, None)

    s = str(value).strip()
    if not s:
        return (0.0, None)

    if "-" in s:
        start_s, end_s = s.split("-", 1)
        start = parse_time_scalar(start_s) or 0.0
        end = parse_time_scalar(end_s)
        if end is not None and end <= start:
            return (0.0, None)
        return (start, end)

    end = parse_time_scalar(s)
    return (0.0, end) if end and end > 0 else (0.0, None)


def parse_max_segments(value: None | int | str | tuple[int, int | None]) -> tuple[int, int | None]:
    """Parse a "--max-segments" value into a ``(start_index, end_index)`` range."""
    if isinstance(value, tuple):
        return value
    if value is None:
        return (0, None)
    if isinstance(value, int):
        return (0, value) if value > 0 else (0, None)

    s = str(value).strip()
    if not s:
        return (0, None)

    try:
        if "-" in s:
            start_s, end_s = s.split("-", 1)
            start = int(start_s.strip()) if start_s.strip() else 0
            end = int(end_s.strip()) if end_s.strip() else None
            if end is not None and end <= start:
                return (0, None)
            return (max(start, 0), end)

        end = int(s)
        return (0, end) if end > 0 else (0, None)
    except ValueError:
        return (0, None)
