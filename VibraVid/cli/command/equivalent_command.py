# 03.07.26

import logging
from collections.abc import Iterable
from typing import Any

logger = logging.getLogger(__name__)


class EquivalentCommandBuilder:
    _CONTEXT_TRACKER_FLAGS = (
        ("cli_search", "-s"),
        ("cli_item", "--item"),
        ("cli_season_selection", "--season"),
        ("cli_episode_selection", "--episode"),
    )
    _SHELL_UNSAFE = (
        " ",
        "\t",
        "*",
        "?",
        "[",
        "]",
        "(",
        ")",
        "{",
        "}",
        "&",
        "|",
        ";",
        "<",
        ">",
        "$",
        "`",
        "'",
        '"',
        "\\",
    )

    def __init__(self, excluded_dests: Iterable[str], program_name: str = "manual.py"):
        """
        Args:
            excluded_dests: dest da NON riproporre come flag standard perché già
                gestiti altrove (context_tracker) o non pertinenti a questo comando
                (es. --down, --update, --dep).
            program_name: nome dello script stampato all'inizio del comando.
        """
        self._excluded_dests = set(excluded_dests)
        self._program_name = program_name

    def build(self, args, parser, context_tracker, site_option_dests: Iterable[str] = None) -> str | None:
        """Build the equivalent command line for the given args and context_tracker, or None if no site is set."""
        site = getattr(context_tracker, "cli_site", None)
        if not site:
            return None

        parts = ["python", self._program_name, "-i", str(site)]
        parts += self._context_tracker_parts(context_tracker)
        parts += self._standard_flag_parts(args, parser, site_option_dests)
        parts += self._site_option_parts(context_tracker)

        return " ".join(parts)

    def log_equivalent_command(self, args, parser, context_tracker, site_option_dests: Iterable[str] = None) -> None:
        """Build and log the equivalent command line for the given args and context_tracker, if a site is set."""
        equivalent_cmd = self.build(args, parser, context_tracker, site_option_dests)
        if equivalent_cmd:
            logger.info(f"Equivalent command: {equivalent_cmd}")

    def build_from_params(self, site: Any, search: Any = None, item: Any = None, season: Any = None, episode: Any = None, options: dict = None) -> str | None:
        """Build the equivalent command from explicit params (e.g. a GUI download), without argparse."""
        if site is None or site == "":
            return None

        parts = ["python", self._program_name, "-i", str(site)]
        for flag, value in (("-s", search), ("--item", item), ("--season", season), ("--episode", episode)):
            if value is None or value == "":
                continue
            parts += [flag, self._quote_if_needed(value)]

        for dest, value in (options or {}).items():
            if value is None or value is False or value == "":
                continue
            flag = "--" + str(dest).replace("_", "-")
            parts += self._flag_and_value(flag, value)

        return " ".join(parts)

    def build_argv_from_params(self, site: Any, search: Any = None, item: Any = None, season: Any = None, episode: Any = None, options: dict = None) -> list[str]:
        """Build the equivalent command argument list from explicit params without 'python' or program name."""
        if site is None or site == "":
            return []

        parts = ["-i", str(site)]
        for flag, value in (("-s", search), ("--item", item), ("--season", season), ("--episode", episode)):
            if value is None or value == "":
                continue
            parts += [flag, str(value)]

        for dest, value in (options or {}).items():
            if value is None or value is False or value == "":
                continue
            flag = "--" + str(dest).replace("_", "-")
            if value is True:
                parts.append(flag)
            else:
                parts += [flag, str(value)]

        return parts

    def log_equivalent_command_from_params(self, *args, **kwargs) -> None:
        """Build and log the equivalent command from explicit params, if a site is set."""
        equivalent_cmd = self.build_from_params(*args, **kwargs)
        if equivalent_cmd:
            logger.info(f"Equivalent command: {equivalent_cmd}")

    def _context_tracker_parts(self, context_tracker) -> list[str]:
        parts = []
        for attr, flag in self._CONTEXT_TRACKER_FLAGS:
            value = getattr(context_tracker, attr, None)
            if value is None or value == "":
                continue
            parts += [flag, self._quote_if_needed(value)]
        return parts

    def _standard_flag_parts(self, args, parser, site_option_dests: Iterable[str] = None) -> list[str]:
        site_dests = set(site_option_dests or ())
        parts = []
        for action in parser._actions:
            dest = action.dest
            if dest in self._excluded_dests or dest in site_dests or not action.option_strings:
                continue
            default = action.default
            value = getattr(args, dest, default)
            if value == default:
                continue
            parts += self._flag_and_value(action.option_strings[-1], value)
        return parts

    def _site_option_parts(self, context_tracker) -> list[str]:
        parts = []
        for dest, value in (getattr(context_tracker, "site_options", None) or {}).items():
            if dest == "drm" or value in (None, False):
                continue
            flag = "--" + dest.replace("_", "-")
            parts += self._flag_and_value(flag, value)
        return parts

    @staticmethod
    def _flag_and_value(flag: str, value: Any) -> list[str]:
        if value is True:
            return [flag]
        return [flag, EquivalentCommandBuilder._quote_if_needed(value)]

    @staticmethod
    def _quote_if_needed(value: Any) -> str:
        text = str(value)
        if text == "" or any(ch in text for ch in EquivalentCommandBuilder._SHELL_UNSAFE):
            return f'"{text}"'
        return text
