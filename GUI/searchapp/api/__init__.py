# 06.06.25

import importlib
import logging
import os

from .base import BaseStreamingAPI

logger = logging.getLogger(__name__)


_API_REGISTRY: dict[str, type] = {}
_LOAD_ERRORS: list[str] = []
_INITIALIZED = False
_PREFERRED_ORDER = [
    "streamingcommunity",
    "animeunity",
    "animeworld",
    "crunchyroll",
    "primevideo",
    "disney",
    "hbomax",
    "paramountplus",
    "appletv",
    "nowtvit",
    "netflix",
    "mediasetinfinity",
    "raiplay",
    "la7",
    "discoveryplus",
    "aurora",
    "tubitv",
    "plutotv",
    "rakutentv",
    "chili",
    "timvision",
    "amazon_music",
]
_OPTIONAL_EXTERNAL = {"primevideo", "appletv", "paramountplus", "amazon_music", "disney", "hbomax", "nowtvit", "netflix"}
_SITE_CATEGORIES: dict[str, str] = {}
_SITE_ALIASES: dict[str, str] = {
    "discovery": "aurora",
    "nove": "aurora",
    "foodnetwork": "aurora",
    "homegardentv": "aurora",
    "realtime": "aurora",
    "dmax": "aurora",
}


def _disabled_sites() -> set:
    """Site module names switched off by the user (`hidden_services` in config.toml, VIBRAVID_HIDDEN_SITES env): no adapter is loaded for them."""
    from VibraVid.services._base.site_loader import hidden_services

    return hidden_services()


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
    previous_count = len([name for name in _API_REGISTRY if name not in disabled])  # hidden services are meant to disappear
    new_registry: dict[str, type] = {}
    load_errors: list[str] = []
    _LOAD_ERRORS.clear()

    for idx, module_name in enumerate(sorted_files):
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
            if module_name in _OPTIONAL_EXTERNAL and isinstance(e, ModuleNotFoundError):
                logger.info("'%s' not available in this checkout (optional external service)", module_name)
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
        for name in disabled:
            _API_REGISTRY.pop(name, None)
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


def normalize_site(site: str) -> str:
    """Return the canonical GUI provider identifier, accepting legacy aliases."""
    site_lower = site.lower().strip()
    return _SITE_ALIASES.get(site_lower, site_lower)


def get_api(site: str) -> BaseStreamingAPI:
    """
    Get API instance for specified site.

    Args:
        site: Site identifier (e.g., 'streamingcommunity', 'animeunity', 'raiplay')

    Returns:
        API instance

    Raises:
        ValueError: If site is not supported
    """
    site_lower = normalize_site(site)

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
    return normalize_site(site) in _API_REGISTRY


__all__ = [
    "get_available_sites",
    "get_api",
    "is_site_available",
    "normalize_site",
    "get_load_errors",
    "reset_site_categories_cache",
]
