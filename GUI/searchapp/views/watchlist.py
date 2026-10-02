# 06.06.25

import json
import logging
import threading

from django.contrib import messages
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import redirect
from django.views.decorators.http import require_http_methods

from ..api import get_api
from ..models import WatchlistItem
from ._shared import _to_bool, _update_single_item

logger = logging.getLogger(__name__)


def _usable_quality(request: HttpRequest, source_alias: str, quality: str) -> str:
    """Drop a quality the provider can't discover or honor, telling the user why."""
    if quality and not get_api(source_alias).supports_quality_discovery:
        messages.warning(request, f"{source_alias} can't pick a video quality yet: the configured quality will be used.")
        return ""
    return quality


@require_http_methods(["POST"])
def set_watchlist_polling_interval(request: HttpRequest) -> HttpResponse:
    """Save the interval and reschedule the automatic check."""
    from ..watchlist_auto import WATCHLIST_INTERVALS, set_interval_seconds
    raw = request.POST.get("poll_interval", "")
    try:
        value = int(raw)
    except (ValueError, TypeError):
        value = None

    allowed = {minutes * 60 for minutes in WATCHLIST_INTERVALS}
    if value not in allowed:
        messages.error(request, "Invalid interval.")
        return redirect("watchlist")

    try:
        set_interval_seconds(value)
    except OSError:
        logger.exception("Could not save watchlist interval")
        messages.error(request, "Could not save the check interval. Please retry.")
        return redirect("watchlist")
    messages.success(request, "Check interval updated.")
    return redirect("watchlist")


@require_http_methods(["POST"])
def add_to_watchlist(request: HttpRequest) -> HttpResponse:
    """Add a media item to the watchlist."""
    source_alias = request.POST.get("source_alias")
    item_payload_raw = request.POST.get("item_payload")
    search_query = request.POST.get("search_query")
    search_site = request.POST.get("search_site")

    if not source_alias or not item_payload_raw:
        messages.error(request, "Missing parameters for the watchlist.")
        return redirect('search_home')

    try:
        item_payload = json.loads(item_payload_raw)
        name = item_payload.get("name")
        poster = item_payload.get("poster")
        tmdb_id = item_payload.get("tmdb_id")
        is_movie = _to_bool(item_payload.get("is_movie")) or str(item_payload.get("type", "")).lower() in {"film", "movie", "ova"}
        from VibraVid.core.utils.quality import normalize_quality
        quality = _usable_quality(request, source_alias, normalize_quality(request.POST.get("quality")))
        scope = request.POST.get("auto_season", "all")
        auto_season = 1
        if not is_movie and scope != "all":
            auto_season = int(scope)
            if auto_season < 0:
                raise ValueError("Invalid season")

        # Check if already in watchlist
        existing = WatchlistItem.objects.filter(name=name, source_alias=source_alias).first()

        if existing:
            messages.info(request, f"'{name}' is already in the watchlist.")
        else:
            item = WatchlistItem.objects.create(
                name=name,
                source_alias=source_alias,
                item_payload=item_payload_raw,
                is_movie=is_movie,
                preferred_quality=quality,
                auto_enabled=request.POST.get("auto_enabled") == "on",
                auto_all_seasons=not is_movie and scope == "all" and request.POST.get("auto_enabled") == "on",
                auto_season=auto_season,
                poster_url=poster,
                tmdb_id=tmdb_id,
                num_seasons=0,
                last_season_episodes=0
            )

            # Update metadata in background to keep GUI fast
            def _bg_update():
                _update_single_item(item)

            threading.Thread(target=_bg_update, daemon=True).start()
            messages.success(request, f"'{name}' added to the watchlist.")

    except Exception as e:
        messages.error(request, f"Error adding to watchlist: {e}")

    # Redirect back to search results if we have the params
    if search_query and search_site:
        from django.urls import reverse
        return redirect(f"{reverse('search')}?site={search_site}&query={search_query}")

    # Series details may have been opened by POST: redirecting to their bare
    # Referer loses the payload and produces a misleading "Missing parameters".
    return redirect("watchlist")


@require_http_methods(["POST"])
def remove_from_watchlist(request: HttpRequest, item_id: int) -> HttpResponse:
    """Remove an item from the watchlist."""
    try:
        item = WatchlistItem.objects.get(id=item_id)
        name = item.name
        item.delete()
        messages.success(request, f"'{name}' removed from the watchlist.")
    except WatchlistItem.DoesNotExist:
        messages.error(request, "Item not found.")

    return redirect("watchlist")


@require_http_methods(["POST"])
def clear_watchlist(request: HttpRequest) -> HttpResponse:
    """Remove all items from the watchlist."""
    WatchlistItem.objects.all().delete()
    messages.success(request, "Watchlist cleared.")
    return redirect("watchlist")


@require_http_methods(["POST"])
def update_watchlist_auto(request: HttpRequest, item_id: int) -> HttpResponse:
    """Update auto-download settings for a watchlist item."""
    try:
        item = WatchlistItem.objects.get(id=item_id)
    except WatchlistItem.DoesNotExist:
        messages.error(request, "Item not found.")
        return redirect("watchlist")

    from VibraVid.core.utils.quality import normalize_quality
    try:
        quality = _usable_quality(request, item.source_alias, normalize_quality(request.POST.get("quality")))
        scope = request.POST.get("auto_season", "")
        auto_enabled = request.POST.get("auto_enabled") == "on"
        all_seasons = scope == "all" and not item.is_movie
        season = int(scope) if scope and scope != "all" and not item.is_movie else None
        if auto_enabled and not item.is_movie and not (all_seasons or season is not None):
            raise ValueError("Select a season or all seasons.")
        
    except (ValueError, TypeError) as exc:
        messages.error(request, str(exc))
        return redirect("watchlist")
    
    item.preferred_quality = quality
    item.auto_enabled = auto_enabled
    item.auto_season = season if auto_enabled else None
    item.auto_all_seasons = all_seasons if auto_enabled else False
    item.auto_status = "Waiting for next check" if auto_enabled else "Paused"
    item.save(update_fields=["preferred_quality", "auto_enabled", "auto_season", "auto_all_seasons", "auto_status"])
    
    messages.success(request, "Watchlist quality and monitoring updated.")
    return redirect("watchlist")


@require_http_methods(["POST"])
def update_watchlist_item(request: HttpRequest, item_id: int) -> HttpResponse:
    """Update a specific watchlist item."""
    try:
        item = WatchlistItem.objects.get(id=item_id)
        threading.Thread(target=_update_single_item, args=(item,), daemon=True).start()
        messages.info(request, f"Update for '{item.name}' started in background.")
    except WatchlistItem.DoesNotExist:
        messages.error(request, "Item not found.")

    return redirect("watchlist")


@require_http_methods(["POST"])
def update_all_watchlist(request: HttpRequest) -> HttpResponse:
    """Update all items in the watchlist."""
    items = WatchlistItem.objects.all()

    def _update_all():
        for item in items:
            _update_single_item(item)

    threading.Thread(target=_update_all, daemon=True).start()
    messages.info(request, "Global update started in background. Reload in a moment.")
    return redirect("watchlist")


@require_http_methods(["POST"])
def run_watchlist_auto_now(request: HttpRequest) -> HttpResponse:
    """Trigger the auto-download scan immediately."""
    from ..watchlist_auto import start_watchlist_scan

    if start_watchlist_scan():
        messages.info(request, "Watchlist check started. Downloads will be queued when the requested quality is available.")
    else:
        messages.info(request, "A watchlist check is already running. Please wait for it to finish.")
    return redirect("watchlist")


def watchlist_status(request: HttpRequest) -> JsonResponse:
    """API endpoint to check if any watchlist item was updated recently."""
    from ..watchlist_auto import watchlist_scan_running

    last_update = WatchlistItem.objects.order_by('-last_checked_at').first()
    return JsonResponse({
        "last_checked": last_update.last_checked_at.timestamp() if last_update else 0,
        "items_count": WatchlistItem.objects.count(),
        "scanning": watchlist_scan_running(),
        "items": list(WatchlistItem.objects.values("id", "auto_status")),
    })

__all__ = ['set_watchlist_polling_interval', 'add_to_watchlist', 'remove_from_watchlist', 'clear_watchlist', 'update_watchlist_auto', 'update_watchlist_item', 'update_all_watchlist', 'run_watchlist_auto_now', 'watchlist_status']
