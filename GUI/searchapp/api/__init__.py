# 06.06.25

import importlib
import logging
import os

from .base import BaseStreamingAPI

logger = logging.getLogger(__name__)


def _is_hidden(module_name: str) -> bool:
    """Whether the underlying service module declares `_hide = True`."""
    for candidate in (f"VibraVid.services.{module_name}", module_name):
        try:
            service_module = importlib.import_module(candidate)
        except Exception:
            continue
        return bool(getattr(service_module, "_hide", False))
    return False


_API_REGISTRY: dict[str, type] = {}
_LOAD_ERRORS: list[str] = []
_INITIALIZED = False
_PREFERRED_ORDER = [
    "streamingcommunity",
    "animeunity",
    "animeworld",
    "crunchyroll",
    "primevideo",
    "mediasetinfinity",
    "raiplay",
    "discoveryplus",
    "discovery",
    "dmax",
    "nove",
    "realtime",
    "homegardentv",
    "foodnetwork",
    "tubitv",
    "cinezo",
    "altadefinizione",
    "eurostreaming",
    "amazon_music",
]
_OPTIONAL_EXTERNAL = {"primevideo", "appletv", "paramountplus"}
_SITE_CATEGORIES: dict[str, str] = {}


def _disabled_sites() -> set:
    """Site module names to exclude from the GUI, via VIBRAVID_DISABLED_SITES (comma-separated)."""
    raw = os.environ.get("VIBRAVID_DISABLED_SITES", "")
    return {s.strip().lower() for s in raw.split(",") if s.strip()}


def _initialize_registry():
    global _INITIALIZED
    if _INITIALIZED:
        return

    package_dir = os.path.dirname(__file__)
    disabled = _disabled_sites()
    api_files = [
        f[:-3]
        for f in os.listdir(package_dir)
        if f.endswith(".py") and not f.startswith("_")
        and f not in ("base.py", "generic.py")
        and f[:-3].lower() not in disabled
    ]

    # Use preferred order first, then any remaining files
    sorted_files = [f for f in _PREFERRED_ORDER if f in api_files]
    sorted_files.extend([f for f in api_files if f not in _PREFERRED_ORDER])

    # Build into a local dict first; only commit to _API_REGISTRY if we end up
    # with at least as many services as we already had.
    previous_count = len(_API_REGISTRY)
    new_registry: dict[str, type] = {}
    load_errors: list[str] = []
    _LOAD_ERRORS.clear()

    for idx, module_name in enumerate(sorted_files):
        if _is_hidden(module_name):
            continue

        try:
            module = importlib.import_module(f".{module_name}", package=__package__)
            api_cls = None
            for _name, obj in module.__dict__.items():
                if (
                    isinstance(obj, type)
                    and issubclass(obj, BaseStreamingAPI)
                    and obj is not BaseStreamingAPI
                    and obj.__module__ == module.__name__
                ):
                    api_cls = obj
                    break

            if api_cls is None:
                raise RuntimeError("no BaseStreamingAPI subclass found in module")

            api_cls._indice = idx
            new_registry[module_name] = api_cls
        except Exception as e:
            # Un servizio esterno assente è la norma, non un guasto: non deve
            # finire in load_errors (che la GUI mostra come problema da risolvere).
            if module_name in _OPTIONAL_EXTERNAL and isinstance(e, ModuleNotFoundError):
                logger.info("'%s' non disponibile in questo checkout (servizio esterno)", module_name)
                continue

            err = f"{module_name}: {type(e).__name__}: {e}"
            load_errors.append(err)
            logger.warning("Could not load API '%s': %s", module_name, e)

    if len(new_registry) >= previous_count:
        _API_REGISTRY.clear()
        _API_REGISTRY.update(new_registry)
    else:
        for k, v in new_registry.items():
            _API_REGISTRY[k] = v
        logger.warning("Reload produced fewer APIs (%d) than before (%d); kept old entries to avoid losing services.", len(new_registry), previous_count,)

    _LOAD_ERRORS.extend(load_errors)

    if not _API_REGISTRY:
        logger.critical("No streaming APIs could be loaded! Check that all dependencies are installed (pip install -r requirements.txt).")
        if load_errors:
            logger.critical("Load errors:\n%s", "\n".join(f"  - {err}" for err in load_errors))
    elif load_errors:
        logger.warning("%d API(s) failed to load:\n%s", len(load_errors), "\n".join(f"  - {err}" for err in load_errors))

    _INITIALIZED = True


def get_load_errors() -> list[str]:
    """Return the list of import errors from the last _initialize_registry() call."""
    return list(_LOAD_ERRORS)


_initialize_registry()


def get_available_sites() -> list[str]:
    """
    Get list of all available streaming sites.

    Returns:
        List of site identifiers
    """
    return list(_API_REGISTRY.keys())


def get_site_categories() -> dict[str, str]:
    """
    Map each available GUI site to its content category (the service's ``_useFor``, e.g. 'Film_Serie', 'Anime', 'Serie', 'song').
    """
    global _SITE_CATEGORIES
    if _SITE_CATEGORIES:
        return dict(_SITE_CATEGORIES)

    try:
        from VibraVid.services._base.site_loader import load_search_functions

        fns = load_search_functions()
    except Exception:
        return {}

    result: dict[str, str] = {}
    for alias, lazy in fns.items():
        site = alias[: -len("_search")] if alias.endswith("_search") else alias
        if site not in _API_REGISTRY:
            continue
        try:
            category = lazy.use_for
        except Exception:
            continue
        if category:
            result[site] = category

    _SITE_CATEGORIES = result
    return dict(result)


def reset_site_categories_cache() -> None:
    """Clear the cached site->category map so the next get_site_categories() call recomputes it."""
    global _SITE_CATEGORIES
    _SITE_CATEGORIES = {}


def get_api(site: str) -> BaseStreamingAPI:
    """
    Get API instance for specified site.

    Args:
        site: Site identifier (e.g., 'streamingcommunity', 'animeunity', 'mostraguarda')

    Returns:
        API instance

    Raises:
        ValueError: If site is not supported
    """
    site_lower = site.lower().strip()

    if site_lower not in _API_REGISTRY:
        available = ", ".join(_API_REGISTRY.keys())
        raise ValueError(f"Site '{site}' not supported. Available sites: {available}")

    api_class = _API_REGISTRY[site_lower]
    return api_class()


def is_site_available(site: str) -> bool:
    """
    Check if a site is available.

    Args:
        site: Site identifier

    Returns:
        True if site is available
    """
    return site.lower().strip() in _API_REGISTRY


__all__ = [
    "get_available_sites",
    "get_api",
    "is_site_available",
    "get_load_errors",
    "reset_site_categories_cache",
]
