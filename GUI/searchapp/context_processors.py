# 06.06.25

from VibraVid.core.ui.tracker import DownloadTracker
from VibraVid.utils.upload.version import __version__


def audio_language_context(request):
    import json

    from VibraVid.core.utils.audio_selection import configured_audio_languages, normalize_audio_language

    choices = configured_audio_languages()
    data = request.POST if request.method == "POST" else request.GET
    selection = data.get("audio_language")
    if selection is None:
        try:
            selection = json.loads(data.get("item_payload") or "{}").get("_audio_language")
        except (ValueError, TypeError, AttributeError):
            pass
    try:
        selection = normalize_audio_language(selection)
    except ValueError:
        selection = None
    return {"audio_language_config": {
        "languages": choices,
        "selection": selection or "all",
        "explicit": selection is not None,
        "allLabel": "Both configured languages" if len(choices) == 2 else
                    "All configured languages" if choices else "All available audio",
    }}


def version_context(request):
    """Add version to template context."""
    return {
        "app_version": __version__,
    }


def active_downloads_context(request):
    """Add active downloads count to template context."""
    tracker = DownloadTracker()
    active_downloads = tracker.get_active_downloads()
    return {
        "active_downloads_count": len(active_downloads),
    }


def arr_stack_context(request):
    """Expose whether the ARR queue entry should appear in navigation."""
    try:
        from .arr.arr_service import _load_arr_config

        cfg = _load_arr_config()
        arr_enabled = bool(cfg.get("enabled"))
        sonarr_cfg = cfg.get("sonarr", {}) or {}
        radarr_cfg = cfg.get("radarr", {}) or {}

        has_sonarr = bool(sonarr_cfg.get("url") and sonarr_cfg.get("api_key"))
        has_radarr = bool(radarr_cfg.get("url") and radarr_cfg.get("api_key"))
        has_seerr = bool(cfg.get("enable_seerr_webhook"))
        has_native_webhook = bool(cfg.get("enable_sonarr_webhook") or cfg.get("enable_radarr_webhook"))

        show_arr_stack_nav = arr_enabled and (has_sonarr or has_radarr or has_seerr or has_native_webhook)
    except Exception:
        show_arr_stack_nav = False

    return {
        "show_arr_stack_nav": show_arr_stack_nav,
    }
