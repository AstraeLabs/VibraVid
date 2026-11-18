# 10.04.26

from enum import Enum

from rich import box
from rich.table import Table


class TableStyle(Enum):
    ORIGINAL = "original"
    MINIMAL = "minimal"
    MODERN_ROUNDED = "modern"
    COMPACT = "compact"


_STYLE_CONFIGS = {
    TableStyle.ORIGINAL: {
        "box": box.ROUNDED,
        "show_header": True,
        "header_style": "cyan",
        "border_style": "blue",
        "padding": (0, 1),
        "show_edge": True,
        "show_lines": False,
    },
    TableStyle.MINIMAL: {
        "box": box.SIMPLE_HEAD,
        "show_header": True,
        "header_style": "bold white on #1a1a2e",
        "border_style": "#4a4e69",
        "padding": (0, 2),
        "show_edge": False,
        "show_lines": False,
    },
    TableStyle.MODERN_ROUNDED: {
        "box": box.ROUNDED,
        "show_header": True,
        "header_style": "bold #e0e1dd on #0d1b2a",
        "border_style": "#415a77",
        "padding": (0, 2),
        "show_edge": True,
        "show_lines": False,
    },
    TableStyle.COMPACT: {
        "box": box.SIMPLE_HEAD,
        "show_header": True,
        "header_style": "bold #f8f9fa on #212529",
        "border_style": "#495057",
        "padding": (0, 1),
        "show_edge": False,
        "show_lines": False,
    },
}


def create_styled_table(style: TableStyle = TableStyle.ORIGINAL) -> Table:
    config = _STYLE_CONFIGS.get(style, _STYLE_CONFIGS[TableStyle.ORIGINAL])
    return Table(
        box=config["box"],
        show_header=config["show_header"],
        header_style=config["header_style"],
        border_style=config["border_style"],
        padding=config["padding"],
        show_edge=config["show_edge"],
        show_lines=config["show_lines"],
    )
