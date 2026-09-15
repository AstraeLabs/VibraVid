# 12.02.26

import json
import logging
import os
import sys
import threading
import time

from django.db import close_old_connections
from django.utils import timezone

from .api import get_api
from .api.base import Entries
from .models import WatchlistItem

logger = logging.getLogger(__name__)

DEFAULT_INTERVAL_SECONDS = 14400

_loop_started = False
_loop_lock = threading.Lock()


def _get_interval_seconds() -> int:
    raw = os.environ.get("WATCHLIST_AUTO_INTERVAL_SECONDS", "")
    try:
        value = int(raw)
        if value > 0:
            return value
    except Exception:
        pass
    return DEFAULT_INTERVAL_SECONDS


def _get_season_episode_count(seasons, season_number: int) -> int | None:
    for season in seasons:
        if int(season.number) == int(season_number):
            return season.episode_count
    return None


def _should_start_loop() -> bool:
    return any(cmd in sys.argv for cmd in ("runserver", "runserver_plus"))


def _process_item(item: WatchlistItem, force: bool = False) -> None:
    """Process a single watchlist item for auto-download.

    Args:
        item: The watchlist item to process.
        force: If True, download even if episode count hasn't changed.
    """
    try:
        logger.info("Processing '%s' (id=%s, season=%s, last_count=%s)", item.name, item.id, item.auto_season, item.auto_last_episode_count,)

        api = get_api(item.source_alias)
        item_payload = json.loads(item.item_payload)
        entries_fields = {k: v for k, v in item_payload.items() if k in Entries.__dataclass_fields__}
        media_item = Entries(**entries_fields)

        if media_item.is_movie or item.is_movie:
            logger.info("'%s': movies are not eligible for auto-download", item.name)
            return

        seasons = api.get_series_metadata(media_item)

        if not seasons:
            logger.warning("'%s': no seasons returned from API", item.name)
            return

        season_number = item.auto_season
        if not season_number:
            logger.info("'%s': no auto_season set", item.name)
            return

        current_count = _get_season_episode_count(seasons, season_number)
        if current_count is None:
            logger.warning("'%s': season %s not found in metadata", item.name, season_number)
            return

        logger.info("'%s': season %s has %s episodes (stored: %s)", item.name, season_number, current_count, item.auto_last_episode_count,)

        now = timezone.now()

        # Always download the selected season — the downloader itself skips already-downloaded files
        from .views import _run_download_in_thread

        media_type = (item_payload.get("type") or "tv").lower()
        logger.info("'%s': starting download S%s episodes=* media_type=%s", item.name, season_number, media_type,)
        _run_download_in_thread(
            site=item.source_alias,
            item_payload=item_payload,
            season=str(season_number),
            episodes="*",
            media_type=media_type,
        )
        item.auto_last_episode_count = current_count
        item.auto_last_downloaded_at = now
        item.has_new_episodes = True

        # Keep display fields in sync with the live metadata already fetched
        item.num_seasons = len(seasons)
        item.last_season_episodes = seasons[-1].episode_count
        item.auto_last_checked_at = now
        item.last_checked_at = now
        item.save(
            update_fields=[
                "auto_last_episode_count",
                "auto_last_checked_at",
                "auto_last_downloaded_at",
                "has_new_episodes",
                "last_checked_at",
                "num_seasons",
                "last_season_episodes",
            ]
        )
    except Exception as exc:
        logger.exception("Error processing %s '%s': %s", item.id, item.name, exc)


def _auto_loop(interval_seconds: int) -> None:
    while True:
        try:
            close_old_connections()
            items = list(
                WatchlistItem.objects.filter(auto_enabled=True).exclude(auto_season__isnull=True).exclude(is_movie=True)
            )
            logger.info("Periodic check: %d items with auto-download enabled", len(items))
            for item in items:
                _process_item(item)
        except Exception as exc:
            logger.exception("Loop error: %s", exc)
        time.sleep(_get_interval_seconds())


def run_watchlist_auto_once(force: bool = True) -> None:
    """Run auto-download scan once. force=True downloads even without new episodes."""
    try:
        close_old_connections()
        items = list(
            WatchlistItem.objects.filter(auto_enabled=True).exclude(auto_season__isnull=True).exclude(is_movie=True)
        )
        logger.info("Manual trigger: %d items with auto-download enabled (force=%s)", len(items), force)
        if not items:
            logger.info("No items have auto-download enabled with a season selected.")
            return
        for item in items:
            _process_item(item, force=force)
    except Exception as exc:
        logger.exception("One-shot error: %s", exc)


def start_watchlist_auto_loop() -> None:
    global _loop_started
    if not _should_start_loop():
        return
    # Guard against Django autoreloader calling AppConfig.ready() twice
    # in the same process, which would start duplicate background threads.
    with _loop_lock:
        if _loop_started:
            logger.info("Loop already running in this process, skipping duplicate start.")
            return
        _loop_started = True

    interval_seconds = _get_interval_seconds()
    thread = threading.Thread(
        target=_auto_loop,
        args=(interval_seconds,),
        daemon=True,
        name="WatchlistAutoLoop",
    )
    thread.start()
