# 12.02.26

import json
import logging
import os
import sys
import threading

from django.db import close_old_connections
from django.utils import timezone

from .api import get_api
from .api.base import Entries
from .models import WatchlistItem

logger = logging.getLogger(__name__)

DEFAULT_INTERVAL_SECONDS = 14400

_loop_started = False
_loop_lock = threading.Lock()
_interval_changed = threading.Event()
WATCHLIST_INTERVALS = (15, 30, 60, 120, 240, 360, 720, 1440)


def _get_interval_seconds() -> int:
    from VibraVid.utils import config_manager
    # Saved from the GUI > WATCHLIST_AUTO_INTERVAL_SECONDS (e.g. docker-compose) > default,
    # so a choice made in the GUI survives a restart even when the env var is set.
    raw = (config_manager.config.get("DEFAULT", "watchlist_interval_seconds", default=None)
           or os.environ.get("WATCHLIST_AUTO_INTERVAL_SECONDS"))
    try:
        value = int(raw)
        if value > 0:
            return max(900, value)
    except Exception:
        pass
    return DEFAULT_INTERVAL_SECONDS


def set_interval_seconds(value: int) -> None:
    from VibraVid.utils import config_manager
    config_manager.config.set_key("DEFAULT", "watchlist_interval_seconds", value)
    config_manager.save_config()
    _interval_changed.set()


def _get_season_episode_count(seasons, season_number: int) -> int | None:
    for season in seasons:
        if int(season.number) == int(season_number):
            return season.episode_count
    return None


def _should_start_loop() -> bool:
    return any(cmd in sys.argv for cmd in ("runserver", "runserver_plus"))


_pending_downloads: set[tuple] = set()
_pending_lock = threading.Lock()
_scan_lock = threading.Lock()
_completion_lock = threading.Lock()


def _record_result(future, item_id, key, reservation, quality):
    close_old_connections()
    try:
        success = future.result() is True
        with _completion_lock:
            item = WatchlistItem.objects.filter(pk=item_id).first()
            if item is None:
                return
            if success:
                completed = dict(item.auto_completed or {})
                completed[key] = quality or "config"
                item.auto_completed = completed
                item.auto_last_downloaded_at = timezone.now()
                item.auto_status = "Downloaded; monitoring for new content"
                item.save(update_fields=["auto_completed", "auto_last_downloaded_at", "auto_status"])
            else:
                WatchlistItem.objects.filter(pk=item_id).update(auto_status="Not completed; waiting for next check")
    except Exception as exc:
        logger.warning("Watchlist download failed for %s: %s", item_id, exc)
        WatchlistItem.objects.filter(pk=item_id).update(auto_status="Download failed; will retry on next check")
    finally:
        with _pending_lock:
            _pending_downloads.discard(reservation)
        close_old_connections()


def _process_item(item: WatchlistItem, force: bool = False) -> None:
    """Recheck every unfinished video, including older releases awaiting quality.

    `force` requests a check now; local files still determine completed targets.
    """
    try:
        from .views import _run_download_in_thread
        api = get_api(item.source_alias)
        payload = json.loads(item.item_payload)
        media = Entries(**{k: v for k, v in payload.items() if k in Entries.__dataclass_fields__})
        quality = item.preferred_quality
        seasons = []
        if media.is_movie or item.is_movie:
            videos = [(None, None)]
        else:
            from .library_index import completed_episodes

            # Mark local final files before any per-episode provider requests.
            local = completed_episodes(media.name, quality)
            local_keys = {f"{s}:{e}:{quality or 'config'}": quality or "config"
                          for s, e in local if item.auto_all_seasons or s == item.auto_season}
            with _completion_lock:
                current = WatchlistItem.objects.filter(pk=item.pk).first()
                if current is None:
                    return
                completed = dict(current.auto_completed or {})
                # Only reconcile the scanned quality/scope. Preserve completions
                # recorded by a running download after this scan's snapshot.
                for key in item.auto_completed or {}:
                    parts = key.split(":")
                    if (len(parts) == 3 and parts[2] == (quality or "config")
                            and (item.auto_all_seasons or parts[0] == str(item.auto_season))
                            and key not in local_keys):
                        completed.pop(key, None)
                completed.update(local_keys)
                WatchlistItem.objects.filter(pk=item.pk).update(auto_completed=completed)
                if local_keys:
                    WatchlistItem.objects.filter(pk=item.pk, auto_enabled=True, preferred_quality=quality).update(
                        auto_status="Local episodes verified; monitoring for new content")
                item.auto_completed = completed
            # Don't let the metadata cache hide a newly published season/episode.
            api._scraper_cache.pop(api._get_cache_key(media), None)
            seasons = api.get_series_metadata(media) or []
            videos = [(s.number, ep.number) for s in seasons
                      if item.auto_all_seasons or s.number == item.auto_season for ep in s.episodes]
            fields = {"num_seasons": len(seasons),
                      "last_season_episodes": seasons[-1].episode_count if seasons else 0}
            # Same rule as _update_single_item: flags are only raised here and stay set
            # until the user clears them; the first fill (0 seasons) is not "new".
            if item.num_seasons and len(seasons) > item.num_seasons:
                fields["has_new_seasons"] = True
            if item.num_seasons and seasons and seasons[-1].episode_count > item.last_season_episodes:
                fields["has_new_episodes"] = True
            WatchlistItem.objects.filter(pk=item.pk).update(**fields)
        videos = list(dict.fromkeys(videos))
        now = timezone.now()
        WatchlistItem.objects.filter(pk=item.pk).update(auto_last_checked_at=now, last_checked_at=now)
        for season, episode in videos:
            key = f"{season}:{episode}:{quality or 'config'}"
            reservation = (item.pk, key)
            if key in (item.auto_completed or {}):
                continue
            with _pending_lock:
                if reservation in _pending_downloads:
                    continue
                _pending_downloads.add(reservation)
            handed_off = False
            try:
                try:
                    available = api.get_available_qualities(media, season, episode) if quality else None
                except NotImplementedError:
                    if quality:
                        WatchlistItem.objects.filter(pk=item.pk).update(
                            auto_status="Provider does not expose quality availability yet")
                        continue
                    available = None
                if available is not None and (not available or (quality and quality not in available)):
                    target = f"S{int(season):02d}E{int(episode):02d}: " if season is not None else ""
                    found = ", ".join(available) if available else "no video available"
                    WatchlistItem.objects.filter(pk=item.pk, auto_enabled=True, preferred_quality=quality,
                                                 auto_season=item.auto_season,
                                                 auto_all_seasons=item.auto_all_seasons).update(
                        auto_status=f"{target}Waiting for {quality or 'video availability'} (found: {found})")
                    continue
                # A user can pause/change the watchlist while the provider is responding.
                current = WatchlistItem.objects.filter(pk=item.pk, auto_enabled=True,
                    preferred_quality=quality, auto_season=item.auto_season,
                    auto_all_seasons=item.auto_all_seasons).first()
                if current is None:
                    return
                if key in (current.auto_completed or {}):
                    continue
                future = _run_download_in_thread(
                    item.source_alias, payload,
                    season=str(season) if season is not None else None,
                    episodes=str(episode) if episode is not None else None,
                    media_type="Film" if item.is_movie else "Serie", quality=quality or None,
                    _metadata=seasons,
                )
                WatchlistItem.objects.filter(pk=item.pk).update(auto_status="Queued / downloading")
                future.add_done_callback(lambda result, pk=item.pk, k=key, r=reservation, q=quality:
                                         _record_result(result, pk, k, r, q))
                handed_off = True
            except Exception as exc:
                logger.warning("Quality check failed for %s S%s E%s: %s", item.name, season, episode, exc)
                WatchlistItem.objects.filter(pk=item.pk).update(auto_status="Provider unavailable; will retry")
            finally:
                if not handed_off:
                    with _pending_lock:
                        _pending_downloads.discard(reservation)
    except Exception:
        logger.exception("Error processing watchlist item %s", item.pk)
        WatchlistItem.objects.filter(pk=item.pk).update(auto_status="Check failed; will retry")


def _scan_watchlist(force=False, *, _lock_held=False):
    if not _lock_held and not _scan_lock.acquire(blocking=False):
        return
    try:
        close_old_connections()
        for item in WatchlistItem.objects.filter(auto_enabled=True):
            _process_item(item, force=force)
    finally:
        close_old_connections()
        _scan_lock.release()


def _auto_loop(interval_seconds: int) -> None:
    while True:
        try:
            _scan_watchlist()
        except Exception:
            logger.exception("Watchlist scan failed")
        while True:
            _interval_changed.clear()
            if not _interval_changed.wait(_get_interval_seconds()):
                break


def run_watchlist_auto_once(force: bool = True) -> None:
    _scan_watchlist(force=force)


def watchlist_scan_running() -> bool:
    return _scan_lock.locked()


def start_watchlist_scan() -> bool:
    """Reserve the scan before reporting its start to the GUI."""
    if not _scan_lock.acquire(blocking=False):
        return False
    try:
        threading.Thread(target=_scan_watchlist, kwargs={"force": True, "_lock_held": True},
                         daemon=True, name="WatchlistManualScan").start()
    except Exception:
        _scan_lock.release()
        raise
    return True


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
