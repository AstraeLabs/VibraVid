# 31.07.26

"""Tests for TUI episode range selection and parsing."""

from textual.widgets._selection_list import Selection

from VibraVid.tui.widgets.range_selection_list import RangeSelectionList, parse_range_expression


def test_parse_range_expression_single_and_ranges():
    episodes = list(range(1, 21))
    assert parse_range_expression("1-5", episodes) == {1, 2, 3, 4, 5}
    assert parse_range_expression("1-3, 7, 10-12", episodes) == {1, 2, 3, 7, 10, 11, 12}
    assert parse_range_expression("even", episodes) == {e for e in episodes if e % 2 == 0}
    assert parse_range_expression("dispari", episodes) == {e for e in episodes if e % 2 != 0}
    assert parse_range_expression("*", episodes) == set(episodes)
    assert parse_range_expression("1-*", episodes) == set(episodes)


def test_range_selection_list_methods():
    rsl = RangeSelectionList(
        Selection("Ep 1", 1),
        Selection("Ep 2", 2),
        Selection("Ep 3", 3),
        Selection("Ep 4", 4),
        Selection("Ep 5", 5),
    )
    rsl.select_range(1, 3)
    assert set(rsl.selected) == {2, 3, 4}

    rsl.invert_selection()
    assert set(rsl.selected) == {1, 5}

    is_active, start_idx, end_idx = rsl.toggle_visual_anchor()
    assert is_active is True
    assert start_idx == 0
    assert rsl.anchor_index == 0

    is_active, start_idx, end_idx = rsl.toggle_visual_anchor()
    assert is_active is False
    assert rsl.anchor_index is None


def test_get_clicked_option_index():
    class DummyEvent:
        class Style:
            meta = {"option": 2}
        style = Style()
        x = 0
        y = 2

    rsl = RangeSelectionList(
        Selection("Ep 1", 1),
        Selection("Ep 2", 2),
        Selection("Ep 3", 3),
    )
    assert rsl._get_clicked_option_index(DummyEvent()) == 2
