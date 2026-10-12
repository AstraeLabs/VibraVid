# 15.09.26

import argparse
import logging
import shlex

from VibraVid.utils import config_manager

from .site_loader import load_search_functions

logger = logging.getLogger(__name__)
_LOGIN_NON_CREDENTIAL_KEYS = {"cdm", "extra_args", "country", "region", "locale"}


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


def _filled(value) -> bool:
    return value is not None and str(value).strip() != ""


def get_site_login_status(site: str) -> dict | None:
    """Determine whether this site has a login.json section, and if so whether all its required keys are present. """
    lazy = load_search_functions().get(f"{site}_search")
    module = lazy.get_module() if lazy else None
    spec = getattr(module, "LOGIN", None) if module else None
    if isinstance(spec, dict):
        return _login_status_from_spec(site, spec)

    if not config_manager.login.has_section(site):
        return None
    values = config_manager.login.get_section(site)

    credentials = {k: v for k, v in values.items() if str(k).strip().lower() not in _LOGIN_NON_CREDENTIAL_KEYS}
    keys = [{"key": str(k).strip(), "present": _filled(v), "optional": False} for k, v in credentials.items()]
    present = any(k["present"] for k in keys)
    return {
        "need": "required",
        "present": present,
        "missing": [] if present else [k["key"] for k in keys],
        "keys": keys,
    }


def _login_status_from_spec(site: str, spec: dict) -> dict:
    section = spec.get("section") or site

    def has(key: str) -> bool:
        return _filled(config_manager.login.get(section, key, default=None))

    keys = [{"key": k, "present": has(k), "optional": False} for k in spec.get("keys") or []]
    any_of = [{"key": k, "present": has(k), "optional": False} for k in spec.get("any_of") or []]
    optional = [{"key": k, "present": has(k), "optional": True} for k in spec.get("optional_keys") or []]

    satisfied = all(k["present"] for k in keys) and (not any_of or any(k["present"] for k in any_of))
    missing = [k["key"] for k in keys if not k["present"]]
    if any_of and not any(k["present"] for k in any_of):
        missing.append(" or ".join(k["key"] for k in any_of))

    return {
        "need": "optional" if spec.get("need") == "optional" else "required",
        "present": satisfied,
        "missing": missing,
        "keys": keys + any_of + optional,
    }


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
