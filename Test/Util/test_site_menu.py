# 30.10.26

"""The site picker redrawn in place: filter as you type, Enter takes the exact index or the first match, Esc cancels."""

import io

import pytest
from rich.console import Console

from VibraVid.cli import site_menu
from VibraVid.cli.run import COLOR_MAP
from VibraVid.cli.site_menu import SiteMenu

LABELS = {
    "0": ("Streamingcommunity", "film_serie"),
    "1": ("Animeunity", "anime"),
    "4": ("Animeworld", "anime"),
    "10": ("La7", "film_serie"),
    "14": ("Youtube_music", "song"),
    "24": ("Extto", "tor"),
}


def menu() -> SiteMenu:
    return SiteMenu(LABELS, COLOR_MAP, Console(file=io.StringIO(), force_terminal=True, width=120))


def press(monkeypatch, *keys: str) -> None:
    sequence = iter(keys)
    monkeypatch.setattr(site_menu, "get_key", lambda: next(sequence))


@pytest.mark.parametrize(
    ("typed", "expected"),
    [
        ([], "0"),  # empty line = default site
        (["1"], "1"),  # exact index wins over 10, 14
        (["1", "4"], "14"),
        (["a", "n", "i"], "1"),  # first match
        (["y", "o", "u", "t", "u", "b", "e", "_"], "14"),
        (["t", "o", "r"], "24"),  # category
        (["g", "l"], "global"),
        (["a", "n", "x", "\x08"], "1"),  # backspace
    ],
)
def test_enter_returns_the_expected_site(monkeypatch, typed, expected):
    press(monkeypatch, *typed, "ENTER")
    assert menu().run() == expected


def test_no_match_keeps_the_menu_open_until_something_matches(monkeypatch):
    press(monkeypatch, "z", "ENTER", "\x08", "1", "ENTER")
    assert menu().run() == "1"


@pytest.mark.parametrize("key", ["ESC", "\x03"])
def test_cancel_keys_raise_keyboard_interrupt(monkeypatch, key):
    press(monkeypatch, "1", key)
    with pytest.raises(KeyboardInterrupt):
        menu().run()


def test_grid_hides_what_does_not_match_and_marks_the_first_match():
    text = menu()._grid("anime")
    assert "Animeunity" in text.plain and "Animeworld" in text.plain and "La7" not in text.plain
    assert any(span.style == "reverse" for span in text.spans)


def test_output_is_drawn_in_the_normal_buffer_without_clearing_the_screen(monkeypatch):
    press(monkeypatch, "a", "ENTER")
    sut = menu()
    sut.run()
    output = sut.console.file.getvalue()
    assert "Streamingcommunity" in output
    assert "\x1b[2J" not in output and "\x1b[?1049h" not in output  # no screen clear, no alternate screen
