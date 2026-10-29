# 28.10.26

"""The labelled status items (country, platform, version, type) sit in a column to the right of the banner and are read once; the community links stay on their own line."""

import io

import pytest
from rich.console import Console

from VibraVid.utils.console import message
from VibraVid.utils.upload import update as updater

STATUS = (
    ("Geo", "[purple]IT"),
    ("Sys", "[cyan]windows x64"),
    ("Ver", "[red]1.4.9"),
    ("Type", "[green]Source code"),
)
COLUMN_ROWS = ("Geo:  IT", "Sys:  windows x64", "Ver:  1.4.9", "Type: Source code")  # the values lined up
ONE_LINE = "Geo: IT · Sys: windows x64 · Ver: 1.4.9 · Type: Source code"


def _rendered(status, width: int) -> list[str]:
    buf = io.StringIO()
    Console(file=buf, width=width, color_system=None).print(f"[#7C3AED]{message.banner_with_status(status, width)}")
    return [row for row in buf.getvalue().splitlines() if row.strip()]


@pytest.fixture(autouse=True)
def _fresh_status():
    updater.status_info.cache_clear()
    yield
    updater.status_info.cache_clear()


@pytest.mark.parametrize("width", [120, 80])
def test_each_item_has_its_own_row_in_one_column_right_of_the_art(width):
    art = _rendered(STATUS, width)

    assert len(art) == 5
    assert art[0].rstrip().endswith("__")  # the top row carries none: the items are bottom-aligned
    assert all(label not in art[0] for label in ("Geo", "Sys", "Ver", "Type"))

    rows = dict(zip(COLUMN_ROWS, art[1:], strict=True))
    assert all(row.rstrip().endswith(item) for item, row in rows.items())
    assert len({row.index(item) for item, row in rows.items()}) == 1  # one below the other, not one after the other
    assert len({row.index(item.split(None, 1)[1]) for item, row in rows.items()}) == 1  # and their values lined up too


def test_the_column_clears_the_widest_row_of_the_art():
    art = _rendered((), 120)
    with_status = _rendered(STATUS, 120)

    widest = max(len(row.rstrip()) for row in art)
    assert with_status[1].index("Geo:") >= widest + message.STATUS_GAP


def test_a_narrow_terminal_keeps_the_banner_whole_and_puts_the_column_under_it():
    art = _rendered(STATUS, 50)

    assert len(art) == 9
    assert all(label not in " ".join(art[:5]) for label in ("Geo", "Sys", "Ver", "Type"))
    assert tuple(row.strip() for row in art[5:]) == COLUMN_ROWS
    assert len({row.index(row.strip()) for row in art[5:]}) == 1


def test_without_a_status_the_banner_is_as_it_was():
    assert message.banner_with_status((), 120) == message.BANNER


def test_the_status_is_read_once_however_many_pages_redraw_the_banner(monkeypatch):
    calls = []
    monkeypatch.setattr(updater, "get_execution_mode", lambda: calls.append(1) or "source_code")

    first = updater.status_info()
    for _ in range(5):
        assert updater.status_info() is first

    assert len(calls) == 1
    assert [label for label, _ in first] == ["Geo", "Sys", "Ver", "Typ"]
    assert first[-1] == ("Typ", "[green]Source code")


def test_the_links_stay_on_their_own_line_under_the_banner(monkeypatch):
    buf = io.StringIO()
    monkeypatch.setattr(updater, "console", Console(file=buf, width=120, color_system=None))
    monkeypatch.setattr(updater, "SHOW", True)
    monkeypatch.setattr(updater, "status_info", lambda: STATUS)

    updater.update()

    assert buf.getvalue().strip() == "Discord · Donate"


def test_with_the_banner_switched_off_the_items_keep_the_links_company(monkeypatch):
    buf = io.StringIO()
    monkeypatch.setattr(updater, "console", Console(file=buf, width=200, color_system=None))
    monkeypatch.setattr(updater, "SHOW", False)
    monkeypatch.setattr(updater, "status_info", lambda: STATUS)

    updater.update()

    assert buf.getvalue().strip() == f"{ONE_LINE} · Discord · Donate"
