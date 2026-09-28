# 06.06.25

import json
import logging
import os
import threading

from django.contrib import messages
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import redirect
from django.views.decorators.http import require_http_methods

from ..models import WatchlistItem
from ._shared import _to_bool, _update_single_item

logger = logging.getLogger(__name__)


@require_http_methods(["POST"])
def set_watchlist_polling_interval(request: HttpRequest) -> HttpResponse:
    """Update the watchlist auto-check interval for this process."""
    raw = request.POST.get("poll_interval", "")
    try:
        value = int(raw)
    except (ValueError, TypeError):
        value = None

    allowed = {300, 900, 1800, 3600, 21600, 43200, 86400}
    if value not in allowed:
        messages.error(request, "Invalid interval.")
        return redirect("watchlist")

    os.environ["WATCHLIST_AUTO_INTERVAL_SECONDS"] = str(value)
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
        quality = normalize_quality(request.POST.get("quality"))

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
                auto_all_seasons=not is_movie and request.POST.get("auto_enabled") == "on",
                poster_url=poster,
                tmdb_id=tmdb_id,
                num_seasons=0,
                last_season_episodes=0
            )

            # Update metadata in background to keep GUI fast
            def _bg_update():
                _update_single_item(item)

            threading.Thread(target=_bg_update, daemon=True).start()

    except Exception as e:
        messages.error(request, f"Error adding to watchlist: {e}")

    # Redirect back to search results if we have the params
    if search_query and search_site:
        from django.urls import reverse
        return redirect(f"{reverse('search')}?site={search_site}&query={search_query}")

    return redirect(request.META.get('HTTP_REFERER', 'search_home'))


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
        quality = normalize_quality(request.POST.get("quality"))
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
    from ..watchlist_auto import run_watchlist_auto_once

    threading.Thread(target=run_watchlist_auto_once, daemon=True).start()
    messages.info(request, "Auto-download started immediately in background.")
    return redirect("watchlist")


def watchlist_status(request: HttpRequest) -> JsonResponse:
    """API endpoint to check if any watchlist item was updated recently."""
    last_update = WatchlistItem.objects.order_by('-last_checked_at').first()
    if last_update:
        return JsonResponse({
            "last_checked": last_update.last_checked_at.timestamp(),
            "items_count": WatchlistItem.objects.count()
        })
    return JsonResponse({"last_checked": 0, "items_count": 0})

__all__ = ['set_watchlist_polling_interval', 'add_to_watchlist', 'remove_from_watchlist', 'clear_watchlist', 'update_watchlist_auto', 'update_watchlist_item', 'update_all_watchlist', 'run_watchlist_auto_now', 'watchlist_status']
