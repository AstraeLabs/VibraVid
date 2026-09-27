# 15.09.26

import argparse
import logging
import shlex

from VibraVid.utils import config_manager

from .site_loader import load_search_functions

logger = logging.getLogger(__name__)


def parse_site_extra_args(site: str, raw: str) -> dict:
    """
    Parse a free-text CLI-style string  using the target site's own register_cli_args(parser), the same argparse definitions the CLI already uses.
    """
    if not raw or not raw.strip():
        return {}

    lazy = load_search_functions().get(f"{site}_search")
    module = lazy.get_module() if lazy else None
    register = getattr(module, "register_cli_args", None) if module else None
    if not callable(register):
        return {}

    mini_parser = argparse.ArgumentParser(add_help=False, exit_on_error=False)
    dests = list(register(mini_parser) or [])

    try:
        tokens = shlex.split(raw)
        parsed, _unknown = mini_parser.parse_known_args(tokens)
    except (SystemExit, argparse.ArgumentError, ValueError) as e:
        raise ValueError(f"Invalid custom option for '{site}': {e}") from e

    return {dest: getattr(parsed, dest) for dest in dests}


def get_site_extra_args_schema(site: str) -> list[dict]:
    """
    Introspect the target site's register_cli_args(parser)
    """
    lazy = load_search_functions().get(f"{site}_search")
    module = lazy.get_module() if lazy else None
    register = getattr(module, "register_cli_args", None) if module else None
    if not callable(register):
        return []

    mini_parser = argparse.ArgumentParser(add_help=False, exit_on_error=False)
    register(mini_parser)

    args = []
    for action in mini_parser._actions:
        flags = [f for f in action.option_strings if f.startswith("--")] or list(action.option_strings)
        if not flags:
            continue

        if action.choices:
            kind = "choice"
        elif action.nargs == 0:
            kind = "flag"
        elif action.type is int:
            kind = "int"
        else:
            kind = "text"

        args.append({
            "flag": flags[0],
            "dest": action.dest,
            "kind": kind,
            "choices": [c for c in action.choices] if action.choices else [],
            "default": action.default,
            "help": action.help or "",
        })

    return args


def resolve_persisted_site_options(site: str) -> dict:
    """
    Look up this site's persisted custom CLI args and parse them, so every future download for that site automatically picks them up without any per-download input.
    """
    if not site:
        return {}

    section = config_manager.login.get_section(site) or config_manager.login.get_section(site.lower())
    raw = (section or {}).get("extra_args", "")
    if not raw:
        return {}

    try:
        return parse_site_extra_args(site, raw)
    except ValueError:
        logger.warning("Invalid persisted custom CLI args for site '%s': %r", site, raw)
        return {}
