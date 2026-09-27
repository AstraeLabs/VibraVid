# 06.06.25

import json
import logging
import os
import time

from django.http import HttpRequest, JsonResponse
from django.views.decorators.http import require_http_methods

from VibraVid.utils import config_manager

from ._shared import _UPDATE_CHECK_TTL, _update_check_cache

logger = logging.getLogger(__name__)


@require_http_methods(["GET"])
def registry_status(request: HttpRequest) -> JsonResponse:
    """Diagnostic endpoint: report what the GUI service registry currently knows."""
    from GUI.searchapp import api as gui_api_module

    api_dir = os.path.dirname(gui_api_module.__file__)
    static_stubs = sorted(
        f[:-3] for f in os.listdir(api_dir)
        if f.endswith(".py") and f not in ("base.py", "__init__.py")
    )

    services_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__)))), "VibraVid", "services")
    services_on_disk = []
    if os.path.isdir(services_dir):
        for entry in sorted(os.listdir(services_dir)):
            full = os.path.join(services_dir, entry)
            if entry.startswith("_") or entry.startswith("."):
                continue
            if os.path.isdir(full) and os.path.isfile(os.path.join(full, "__init__.py")):
                services_on_disk.append(entry.lower())

    loaded = sorted(gui_api_module.get_available_sites())
    missing_from_dropdown = sorted(set(services_on_disk) - set(loaded))

    return JsonResponse({
        "loaded_in_dropdown": loaded,
        "services_on_disk": services_on_disk,
        "static_gui_stubs": static_stubs,
        "missing_from_dropdown": missing_from_dropdown,
        "load_errors": gui_api_module.get_load_errors(),
        "db_dir": os.environ.get("DJANGO_DB_DIR", "<unset>"),
        "hint": (
            "If 'loaded_in_dropdown' contains fewer entries than "
            "'services_on_disk', check 'load_errors' "
            "to see why the others aren't loading."
        ),
    })


@require_http_methods(["GET"])
def site_cli_options_schema(request: HttpRequest) -> JsonResponse:
    """Describe the custom CLI options a given site's register_cli_args() exposes."""
    from ._shared import get_site_extra_args_schema

    site = (request.GET.get("site") or "").strip()
    if not site:
        return JsonResponse({"error": "Missing 'site' parameter"}, status=400)

    try:
        args = get_site_extra_args_schema(site)
    except Exception as exc:
        logger.exception("Failed to load CLI options schema for site '%s'", site)
        return JsonResponse({"error": str(exc)}, status=500)

    return JsonResponse({"args": args})


@require_http_methods(["POST"])
def reload_config(request: HttpRequest) -> JsonResponse:
    try:
        file_type = None
        if request.content_type and "application/json" in request.content_type:
            try:
                data = json.loads(request.body.decode("utf-8"))
                file_type = data.get("file_type")
            except Exception:
                file_type = None

        if file_type == "login":
            config_manager.reload_login_only()
            message = "Login ricaricato"
        elif file_type == "config":
            config_manager.reload_config_only()
            message = "Config ricaricata"
        else:
            config_manager.reload()
            message = "Config ricaricata"
        return JsonResponse({
            "success": True,
            "message": message
        })
    except Exception as e:
        return JsonResponse({
            "success": False,
            "error": f"Errore nel reload: {str(e)}"
        }, status=500)


@require_http_methods(["GET"])
def check_updates(request: HttpRequest) -> JsonResponse:
    """Return current and latest version info, with a 1-hour in-memory cache."""
    import urllib.request

    from VibraVid.utils.upload.version import __version__

    cached = _update_check_cache
    if cached and time.monotonic() - cached.get("ts", 0) < _UPDATE_CHECK_TTL:
        return JsonResponse(cached["result"])

    try:
        api_url = "https://api.github.com/repos/AstraeLabs/VibraVid/releases/latest"
        req = urllib.request.Request(api_url, headers={"User-Agent": "VibraVid-update-check/1"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read())
        latest = data.get("tag_name", "").lstrip("v")
        html_url = data.get("html_url", "")
    except Exception as exc:
        logger.warning(f"Update check failed: {exc}")
        return JsonResponse({"error": str(exc)}, status=502)

    def _ver_tuple(v):
        try:
            return tuple(int(x) for x in v.split("."))
        except Exception:
            return (0,)

    update_available = _ver_tuple(latest) > _ver_tuple(__version__)
    result = {
        "current": __version__,
        "latest": latest,
        "update_available": update_available,
        "html_url": html_url,
    }
    _update_check_cache["ts"] = time.monotonic()
    _update_check_cache["result"] = result
    return JsonResponse(result)


@require_http_methods(["POST"])
def trigger_binaries_update(request: HttpRequest) -> JsonResponse:
    """Check FFmpeg, dovi_tool, MKVToolNix and Velora"""
    from VibraVid.utils.upload.update import check_all_binaries_update

    try:
        result = check_all_binaries_update()
    except Exception as exc:
        logger.exception("Binaries update failed")
        return JsonResponse({"success": False, "message": str(exc)}, status=500)

    return JsonResponse({"success": True, "results": result})


@require_http_methods(["POST"])
def trigger_update(request: HttpRequest) -> JsonResponse:
    """Perform an in-app update appropriate to how VibraVid is running."""
    from ..self_update import perform_update

    try:
        result = perform_update()
    except Exception as exc:
        logger.exception("Self-update failed")
        return JsonResponse({"success": False, "message": str(exc)}, status=500)

    return JsonResponse(result)

__all__ = [
    'registry_status', 'site_cli_options_schema', 'reload_config',
    'check_updates', 'trigger_binaries_update', 'trigger_update',
]
