# 26.10.26
# ruff: noqa: E402

import sys
from pathlib import Path

repo_root = Path(__file__).resolve().parents[2]
if str(repo_root) not in sys.path:
    sys.path.insert(0, str(repo_root))

import pytest

from VibraVid.tui.screens.detail import compact_ranges


@pytest.mark.parametrize(
    "numbers,total,expected",
    [
        ({1, 2, 3, 5}, 9, "1-3,5"),
        ({"2", "1"}, 5, "1-2"),  # services such as Crunchyroll give episode numbers as str
        ({"10", "2", "3"}, 20, "2-3,10"),
        ({"1", "2", "3", "SP1", "12.5"}, 9, "1-3,12.5,SP1"),  # specials and half episodes are kept as they are
        ({1, 2, 3}, 3, "*"),
        ({1, "1"}, 5, "1"),
        (set(), 3, ""),
    ],
)
def test_compact_ranges(numbers, total, expected):
    assert compact_ranges(numbers, total) == expected
