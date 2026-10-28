# 3.12.23

import logging
import os
import platform

from rich.console import Console
from rich.text import Text

from VibraVid.utils import config_manager

console = Console()
logger = logging.getLogger(__name__)
CLEAN = config_manager.config.get_bool("DEFAULT", "show_message")
SHOW = config_manager.config.get_bool("DEFAULT", "show_message")
STATUS_GAP = 4

BANNER = r"""
 [#7C3AED] _    ___ __              _    ___     __
 [#7C3AED]| |  / (_) /_  _________ | |  / (_)___/ /
 [#7C3AED]| | / / / __ \/ ___/ __ `/ | / / / __  /
 [#7C3AED]| |/ / / /_/ / /  / /_/ /| |/ / / /_/ /
 [#7C3AED]|___/_/_.___/_/   \__,_/ |___/_/\__,_/
"""


def _status_info() -> tuple[tuple[str, str], ...]:
    """The country, platform, version and execution type items (read once by the updater); a page is never worth failing over them."""
    try:
        from VibraVid.utils.upload.update import status_info  # not at module level: the updater pulls in the HTTP stack

        return status_info()
    except Exception as exc:
        logger.debug(f"Banner status unavailable: {exc}")
        return ()


def _cells(markup: str) -> int:
    return Text.from_markup(markup).cell_len


def format_status(status: tuple[tuple[str, str], ...], align: bool = False) -> list[str]:
    """The ``(label, value)`` *status* items as ``Label: value`` Rich markup, the values lined up when *align*."""
    pad = max(len(label) for label, _ in status) + 1 if align else 0
    return [f"{(label + ':').ljust(pad)} {value}[/]" for label, value in status]


def banner_with_status(status: tuple[tuple[str, str], ...], width: int) -> str:
    """The banner with each *status* item on its own row, in one column to the right of the art and bottom-aligned with it; when *width* columns are too few for the column, in the same column under the banner."""
    if not status:
        return BANNER

    rows: list[str] = [*BANNER.split("\n")]  # a blank row on each side of the art
    art = rows[1:-1]
    items = format_status(status, align=True)
    column = max(_cells(row.rstrip()) for row in art) + STATUS_GAP
    if len(items) <= len(art) and width >= column + max(_cells(item) for item in items):
        for index, item in zip(range(len(rows) - 1 - len(items), len(rows) - 1), items, strict=True):
            row = rows[index].rstrip()
            rows[index] = f"{row}{' ' * (column - _cells(row))}{item}"
    else:
        rows[-1:-1] = [f"      {item}" for item in items]
    return "\n".join(rows)


def start_message(clean: bool = True):
    """Display a stylized start message in the console."""
    if CLEAN and clean:
        os.system("cls" if platform.system() == "Windows" else "clear")

    if SHOW:
        console.print(f"[#7C3AED]{banner_with_status(_status_info(), console.width)}")
