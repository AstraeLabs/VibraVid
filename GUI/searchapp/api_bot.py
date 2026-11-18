# 26.07.26

import hmac
import json
import logging
import os
import shutil
from collections import Counter
from functools import wraps
from pathlib import Path

from django.conf import settings
from django.http import HttpRequest, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods
from searchapp.api import get_api
from searchapp.api.base import Entries

from VibraVid.utils import config_manager

from . import _download_infra as _dl
from ._download_infra import scheduled_downloads, scheduled_downloads_lock
from .views import (
    _media_item_to_display_dict,
    _resolve_global_sites,
    _run_download_in_thread,
    _run_global_search,
)

log = logging.getLogger("searchapp.api_bot")
BOT_TOKEN_HEADER = "X-VibraVid-Token"


def _bot_secret() -> str:
    return (os.environ.get("VIBRAVID_BOT_SECRET") or "").strip()


def require_bot_secret(view):
    """Rejects the call if the shared secret is missing — but only if one is set."""
    @wraps(view)
    def _guard(request: HttpRequest, *args, **kwargs):
        expected = _bot_secret()
        if not expected:
            return view(request, *args, **kwargs)

        given = request.headers.get(BOT_TOKEN_HEADER, "")
        if not (given and hmac.compare_digest(given, expected)):
            log.warning("[bot] chiamata rifiutata su %s: token assente o errato", request.path)
            return JsonResponse({"error": "token non valido"}, status=403)
        return view(request, *args, **kwargs)

    return _guard


def _json_body(request: HttpRequest) -> dict:
    try:
        return json.loads(request.body.decode("utf-8"))
    except (ValueError, TypeError):
        return {}


@csrf_exempt
@require_bot_secret
@require_http_methods(["POST"])
def bot_search(request: HttpRequest) -> JsonResponse:
    """{"query": "...", "site": "__all__" | "__cat__:<cat>" | "<sito>"} ->
    {"results": [{title, type, year, site, is_movie, payload}], "failed": [...]}"""
    body = _json_body(request)
    query = str(body.get("query") or "").strip()
    site = str(body.get("site") or "__all__").strip()
    if not query:
        return JsonResponse({"error": "query mancante"}, status=400)

    try:
        sites = _resolve_global_sites(site)
        if sites is not None:
            log.info("[bot] ricerca %r su %d siti (%s): %s", query, len(sites), site, ", ".join(sites))
            results, failed = _run_global_search(query, sites)
        else:
            log.info("[bot] ricerca %r sul singolo sito '%s'", query, site)
            api = get_api(site)
            results = [_media_item_to_display_dict(i, site) for i in api.search(query)]
            failed = []
    except Exception as e:
        log.exception("[bot] ricerca fallita per %r su '%s'", query, site)
        return JsonResponse({"error": str(e)}, status=500)

    # Diagnostics for /log: how many results each site returned and who didn't respond.
    per_site = Counter(str(r.get("source_alias") or "?") for r in results)
    breakdown = ", ".join(f"{s}={n}" for s, n in sorted(per_site.items())) or "nessuno"
    log.info("[bot] risultati %r: %d totali (%s)%s", query, len(results), breakdown,
              f" — non hanno risposto: {', '.join(failed)}" if failed else "")

    out = []
    for r in results:
        try:
            payload = json.loads(r.get("payload_json") or "{}")
        except (ValueError, TypeError):
            payload = {}
        out.append({
            "title": r.get("display_title"),
            "type": r.get("display_type"),
            "media_kind": r.get("media_kind"),
            "year": r.get("year"),
            "site": r.get("source_alias"),
            "is_movie": bool(r.get("is_movie")),
            "payload": payload,
        })
    return JsonResponse({"results": out, "failed": failed})


@csrf_exempt
@require_bot_secret
@require_http_methods(["POST"])
def bot_seasons(request: HttpRequest) -> JsonResponse:
    """{"site": "...", "payload": {...}} -> {"seasons": [{number, episodes}]}"""
    body = _json_body(request)
    site = str(body.get("site") or "").strip()
    payload = body.get("payload") or {}
    if not site or not payload:
        return JsonResponse({"error": "parametri mancanti"}, status=400)
    try:
        api = get_api(site)
        fields = {k: v for k, v in payload.items() if k in Entries.__dataclass_fields__}
        seasons = api.get_series_metadata(Entries(**fields)) or []
        return JsonResponse(
            {"seasons": [{"number": s.number, "episodes": s.episode_count} for s in seasons]}
        )
    except Exception as e:  # noqa: BLE001 - Django resilience boundary
        return JsonResponse({"error": str(e)}, status=500)


@csrf_exempt
@require_bot_secret
@require_http_methods(["POST"])
def bot_download(request: HttpRequest) -> JsonResponse:
    """{"site": "...", "payload": {...}, "season": "2"?, "episodes": "*"?} ->
    {"queued": true, "download_id": "...", "title": "..."}
    Same semantics as the GUI's start_download/full_season."""
    body = _json_body(request)
    site = str(body.get("site") or "").strip()
    payload = body.get("payload") or {}
    season = str(body.get("season") or "").strip() or None
    episodes = str(body.get("episodes") or "").strip() or None
    if not site or not payload:
        return JsonResponse({"error": "parametri mancanti"}, status=400)

    item_type = str(payload.get("type") or "").lower()
    if item_type in ("song", "track", "music"):
        media_type = "Musica"
    elif item_type == "album":
        media_type = "Album"
    elif item_type in ("book", "ebook", "audiobook"):
        media_type = "Libro"
    elif payload.get("is_movie"):
        media_type = "Film"
    else:
        media_type = "Serie"

    if media_type == "Serie":
        if not season:
            return JsonResponse({"error": "per le serie indica la stagione"}, status=400)
        episodes = episodes or "*"

    # Same title format as _run_download_in_thread: needed to find the
    # (internally generated) download_id in the queue entry just created.
    name = payload.get("name", "Unknown")
    if season and episodes:
        title = f"{name} - S{season} E{episodes}"
    elif season:
        title = f"{name} - S{season}"
    else:
        title = name

    _run_download_in_thread(site, payload, season, episodes, media_type)

    download_id = None
    newest = 0.0
    with scheduled_downloads_lock:
        for did, info in scheduled_downloads.items():
            if info.get("title") == title and info.get("site") == site:
                ts = info.get("scheduled_at", 0.0)
                if ts >= newest:
                    newest = ts
                    download_id = did
    return JsonResponse({"queued": True, "download_id": download_id, "title": title})


@csrf_exempt
@require_bot_secret
@require_http_methods(["GET"])
def bot_sites(request: HttpRequest) -> JsonResponse:
    """List of selectable sites, identical to the GUI dropdown (bot's /sito).
    -> {"groups": [{"label": "...", "options": [{"value", "label"}]}]}
    The `value`s are the tokens accepted by bot_search: "__all__",
    "__cat__:<cat>", or a single site's identifier."""
    try:
        from .forms import get_site_choices
        groups = get_site_choices()  # [(group_label, [(value, label), ...]), ...]
    except Exception as e:
        log.exception("[bot] elenco siti fallito")
        return JsonResponse({"error": str(e)}, status=500)
    out = [
        {"label": glabel, "options": [{"value": v, "label": lbl} for v, lbl in opts]}
        for glabel, opts in groups
    ]
    return JsonResponse({"groups": out})


@csrf_exempt
@require_bot_secret
@require_http_methods(["POST"])
def bot_cancel(request: HttpRequest) -> JsonResponse:
    """Cancels an active/queued download (and the other episodes of the same
    series), like the GUI's "kill & clear" button. Used by the bot's /annulla.

    Body: {"download_id": "...", "series_name": "..."?} -> {"cancelled": true}."""
    from ._download_infra import (
        _cancel_scheduled_download,
        _extract_series_base_title,
        _same_series,
        cancelled_scheduled_downloads,
    )

    body = _json_body(request)
    download_id = str(body.get("download_id") or "").strip() or None
    series_name = str(body.get("series_name") or "").strip()
    if not download_id and not series_name:
        return JsonResponse({"error": "download_id o series_name mancante"}, status=400)

    target_site = ""
    target_series = _extract_series_base_title(series_name)
    try:
        if download_id:
            with scheduled_downloads_lock:
                info = scheduled_downloads.get(download_id)
            if info:
                target_site = str(info.get("site") or "").strip()
                if not target_series:
                    target_series = _extract_series_base_title(info.get("title", ""))
            else:
                active_info = next(
                    (d for d in _dl.download_tracker.get_active_downloads() if d.get("id") == download_id), None
                )
                if active_info:
                    target_site = str(active_info.get("site") or "").strip()
                    if not target_series:
                        target_series = _extract_series_base_title(active_info.get("title", ""))
            _cancel_scheduled_download(download_id)
            _dl.download_tracker.request_stop(download_id)

        # Stop the other active episodes of the same series (same site + base title).
        if target_series:
            for item in _dl.download_tracker.get_active_downloads():
                cid = item.get("id")
                if not cid:
                    continue
                if target_site and str(item.get("site") or "").strip() != target_site:
                    continue
                if _same_series(item.get("title", ""), target_series):
                    _cancel_scheduled_download(cid)
                    _dl.download_tracker.request_stop(cid)

            # Clear the queue for the same series.
            with scheduled_downloads_lock:
                to_remove = [
                    d_id for d_id, d_info in scheduled_downloads.items()
                    if (not target_site or str(d_info.get("site") or "").strip() == target_site)
                    and _same_series(d_info.get("title", ""), target_series)
                ]
                for d_id in to_remove:
                    cancelled_scheduled_downloads.add(d_id)
                    scheduled_downloads.pop(d_id, None)
    except Exception as e:
        log.exception("[bot] annullo download fallito")
        return JsonResponse({"error": str(e)}, status=500)
    return JsonResponse({"cancelled": True})


@csrf_exempt
@require_bot_secret
@require_http_methods(["GET"])
def bot_status(request: HttpRequest) -> JsonResponse:
    """VibraVid health for the bot's /stato: library disk space, active/max
    download slots. The mere fact that it responds means the container is up."""
    root = config_manager.config.get("OUTPUT", "root_path", default="Video")
    disk = {}
    try:
        total, used, free = shutil.disk_usage(root)
        disk = {"total": total, "used": used, "free": free, "path": str(root)}
    except Exception as e:  # noqa: BLE001 - Django resilience boundary
        disk = {"error": str(e), "path": str(root)}

    try:
        active = len(_dl.download_tracker.get_active_downloads())
    except Exception:  # noqa: BLE001 - Django resilience boundary
        active = 0
    with scheduled_downloads_lock:
        queued = len(scheduled_downloads)

    return JsonResponse({
        "disk": disk,
        "slots": {"active": active, "max": getattr(_dl, "_max_download_slots", 1)},
        "queued": queued,
    })


def _log_dir() -> Path:
    """VibraVid log directory (RotatingFileHandler in settings.LOGGING)."""
    root = getattr(settings, "PROJECT_ROOT", None)
    if root is None:
        base = getattr(settings, "BASE_DIR", None)
        root = Path(base).parent if base else Path("/app")
    return Path(root) / ".cache" / "logs"


@csrf_exempt
@require_bot_secret
@require_http_methods(["GET"])
def bot_logs(request: HttpRequest) -> JsonResponse:
    """Tail of the VibraVid logs (for the bot's /log).
    Query: lines=<1..500> (default 200), level=all|err.
    -> {"file": "<name>", "lines": [...]}"""
    try:
        lines = int(request.GET.get("lines") or 200)
    except (TypeError, ValueError):
        lines = 200
    lines = max(1, min(lines, 500))
    level = (request.GET.get("level") or "all").strip().lower()
    only_err = level in ("err", "error", "errors", "errori", "warn", "warning")

    d = _log_dir()
    files = (
        sorted(d.glob("*.log"), key=lambda p: p.stat().st_mtime, reverse=True)
        if d.is_dir() else []
    )
    if not files:
        return JsonResponse({"file": None, "lines": [], "note": "nessun file di log trovato"})

    # Start from the most recent file; walk back to older ones only if more lines are needed.
    need = lines * 4 if only_err else lines
    collected: list[str] = []
    used = None
    for f in files:
        try:
            with f.open(encoding="utf-8", errors="replace") as fh:
                collected = fh.read().splitlines() + collected
            used = used or f.name
        except Exception:  # noqa: BLE001 - Django resilience boundary
            continue
        if len(collected) >= need:
            break

    if only_err:
        keys = ("[ERROR]", "[WARNING]", "[CRITICAL]", "Traceback")
        collected = [ln for ln in collected if any(k in ln for k in keys)]

    return JsonResponse({"file": used, "lines": collected[-lines:]})


__all__ = [
    'bot_search', 'bot_seasons', 'bot_download', 'bot_sites', 'bot_cancel',
    'bot_status', 'bot_logs',
]
