# 25.07.26

import logging

from django.http import HttpRequest, HttpResponse
from django.shortcuts import render
from searchapp.api import get_available_sites, get_site_categories

from .. import services_admin
from .._download_infra import _get_scheduled_downloads
from .._library_paths import titleize_name
from ..models import WatchlistItem

logger = logging.getLogger(__name__)


def _duotone(text: str) -> tuple[str, str]:
    """Copy of the duotone logic from the old JS, to generate a consistent color for a given text."""
    hue = sum(ord(c) for c in (text or "?")) % 360
    return f"hsl({hue} 34% 26%)", f"hsl({hue} 40% 10%)"


def cinema_download(request: HttpRequest) -> HttpResponse:
    """Download: grid of scheduled downloads, with status and actions."""
    return render(request, "searchapp/cinema_download.html", {
        "nav_active": "download",
        "watchlist_items": _watchlist_tiles(limit=14),
    })


def _watchlist_tiles(limit: int | None = None) -> list[dict]:
    """Return a list of watchlist items with their status for display in the cinema view."""
    rows: list[dict] = []
    try:
        query = WatchlistItem.objects.all()
        for item in (query[:limit] if limit else query):
            p1, p2 = _duotone(item.name)
            rows.append({
                "id": item.id,
                "name": titleize_name(item.name),
                "site": item.source_alias,
                "poster": item.poster_url,
                "is_new": item.has_new_episodes or item.has_new_seasons,
                "is_movie": item.is_movie,
                "seasons": item.num_seasons,
                "season_numbers": list(range(1, item.num_seasons + 1)),
                "auto": item.auto_enabled,
                "auto_season": item.auto_season,
                "auto_all_seasons": item.auto_all_seasons,
                "quality": item.preferred_quality,
                "auto_status": item.auto_status,
                "item_payload": item.item_payload,
                "last_checked": item.auto_last_checked_at or item.last_checked_at,
                "p1": p1, "p2": p2,
            })
    except Exception:
        logger.exception("Watchlist non leggibile: si prosegue senza")
    return rows


def _site_extra_args_map() -> dict:
    """``{site: "custom CLI-style options"}`` for the providers that have ``extra_args`` set in login.json."""
    import json as _json

    try:
        login_data = _json.loads(_conf_text("login.json") or "{}")
        return {
            site_name: block["extra_args"]
            for site_name, block in login_data.items()
            if isinstance(block, dict) and block.get("extra_args")
        }
    except Exception:
        logger.exception("login.json non leggibile per site_extra_args")
        return {}


def cinema_search(request: HttpRequest) -> HttpResponse:
    """Search: form with scope and site selection, results in a grid."""
    from ..forms import _CATEGORY_LABELS, GLOBAL_ALL_TOKEN, GLOBAL_CATEGORY_PREFIX

    categories = get_site_categories()

    sites = []
    for name in get_available_sites():
        sites.append({"name": name, "category": categories.get(name, "")})
    sites.sort(key=lambda s: s["name"])

    scopes = []
    for cat in sorted({s["category"] for s in sites if s["category"]}):
        scopes.append({
            "value": f"{GLOBAL_CATEGORY_PREFIX}{cat}",
            "label": _CATEGORY_LABELS.get(cat, cat.replace("_", " ").title()),
            "category": cat,
            "count": sum(1 for s in sites if s["category"] == cat),
        })

    return render(request, "searchapp/cinema_search.html", {
        "nav_active": "search",
        "sites": sites,
        "scopes": scopes,
        "all_token": GLOBAL_ALL_TOKEN,
        "site_extra_args": _site_extra_args_map(),
    })


def cinema_watchlist(request: HttpRequest) -> HttpResponse:
    """Watchlist: grid of watchlist items with status and actions."""
    from ..watchlist_auto import WATCHLIST_INTERVALS, _get_interval_seconds, watchlist_scan_running

    items = _watchlist_tiles()
    interval = _get_interval_seconds()
    targets = {"1080p", "720p", "480p"}
    targets.update(i["quality"] for i in items
                   if i["quality"].endswith("p") and i["quality"][:-1].isdigit()
                   and 0 < int(i["quality"][:-1]) <= 1080 and i["quality"] != "360p")

    return render(request, "searchapp/cinema_watchlist.html", {
        "nav_active": "watchlist",
        "scanning": watchlist_scan_running(),
        "quality_targets": sorted(targets, key=lambda q: int(q[:-1]), reverse=True),
        "items": items,
        "new_count": sum(1 for i in items if i["is_new"]),
        "auto_count": sum(1 for i in items if i["auto"]),
        "interval_minutes": int(interval // 60) if interval else 0,
        "interval_options": [{"seconds": m * 60, "minutes": m} for m in WATCHLIST_INTERVALS],
    })


def _conf_text(filename: str) -> str:
    """Return the content of a config file as text, or an error message if it can't be read."""
    import os as _os

    from ._shared import _conf_dir

    try:
        with open(_os.path.join(_conf_dir(), filename), encoding="utf-8") as fh:
            return fh.read()
    except OSError as exc:
        logger.warning("Conf/%s non leggibile: %s", filename, exc)
        return f"# Non riesco a leggere {filename}: {exc}"


def cinema_system(request: HttpRequest) -> HttpResponse:
    """System: show providers, disabled sites, services, and config content."""
    import os

    from VibraVid.utils.upload.version import __version__

    from ._shared import get_site_extra_args_schema

    categories = get_site_categories()
    providers = []
    for name in sorted(get_available_sites()):
        providers.append({"name": name, "category": categories.get(name, "")})

    cli_option_providers = []
    for p in providers:
        try:
            if get_site_extra_args_schema(p["name"]):
                cli_option_providers.append(p)
        except Exception:
            logger.exception("Impossibile leggere le opzioni CLI per '%s'", p["name"])

    disabled = [
        s.strip() for s in (os.environ.get("VIBRAVID_DISABLED_SITES") or "").split(",") if s.strip()
    ]

    services = [
        {"name": "Bypasser", "detail": "Turnstile for Amazon Music",
         "on": bool(os.environ.get("BYPASSER_URL"))},
    ]

    arr = {}
    try:
        from ..arr.arr_service import _load_arr_config

        cfg = _load_arr_config()
        arr = {
            "enabled": bool(cfg.get("enabled")),
            "sonarr": (cfg.get("sonarr") or {}).get("url") or "",
            "radarr": (cfg.get("radarr") or {}).get("url") or "",
            "seerr": bool(cfg.get("enable_seerr_webhook")),
        }
    except Exception:
        logger.exception("Config ARR non leggibile")

    try:
        queued = len(_get_scheduled_downloads())
    except Exception:
        queued = 0

    return render(request, "searchapp/cinema_system.html", {
        "nav_active": "settings",
        "providers": providers,
        "cli_option_providers": cli_option_providers,
        "disabled_sites": disabled,
        "services": services,
        "uploaded_services": services_admin.removable_services(),
        "arr": arr,
        "queued": queued,
        "app_version": __version__,
        "config_content": _conf_text("config.json"),
        "login_content": _conf_text("login.json"),
        "site_extra_args": _site_extra_args_map(),
    })


def cinema_logs(request: HttpRequest) -> HttpResponse:
    """Logs: browse and view app / ARR log files."""
    return render(request, "searchapp/cinema_logs.html", {
        "nav_active": "logs",
    })


__all__ = [
    "cinema_download", "cinema_search", "cinema_watchlist", "cinema_system", "cinema_logs",
]
