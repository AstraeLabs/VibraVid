# 06.10.26

"""``DEFAULT.log_level`` in config.json: a value Python doesn't know must not stop the app from starting."""

import logging

import pytest

from VibraVid.utils.logger import resolve_log_level


@pytest.mark.parametrize(
    "name, expected",
    [("DEBUG", logging.DEBUG), ("info", logging.INFO), (" Warning ", logging.WARNING), ("ERROR", logging.ERROR), ("CRITICAL", logging.CRITICAL)],
)
def test_standard_levels_are_resolved_case_insensitively(name, expected):
    assert resolve_log_level(name) == expected


@pytest.mark.parametrize("name", ["FULL", "verbose", "", None, "5x", "TRACE"])
def test_unknown_levels_fall_back_to_info_and_say_so(name, capsys):
    assert resolve_log_level(name) == logging.INFO
    assert "Unknown DEFAULT.log_level" in capsys.readouterr().err
