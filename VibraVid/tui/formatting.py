# 06.10.26

import datetime
from typing import Any


def format_time(ts: Any) -> str:
    """Render a unix timestamp as local ``YYYY-MM-DD HH:MM:SS``; anything else is shown as-is, empty as ``-``."""
    if not ts:
        return "-"
    try:
        if isinstance(ts, (int, float)):
            return datetime.datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")
        return str(ts)
    except Exception:
        return str(ts)
