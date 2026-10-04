# 07.05.26


import json
import logging
import os
import threading
import time
from datetime import datetime, timedelta
from datetime import timezone as dt_timezone

from django.db import close_old_connections
from django.utils import timezone

logger = logging.getLogger("ARR")

# Module-level lock for thread-safe enqueue operations
_enqueue_lock = threading.Lock()


def _load_arr_config() -> dict:
    """Load the 'arr' section from Conf/config.json, with env-var overrides."""
    conf_dir = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__)))),
        "Conf",
    )
    config_path = os.path.join(conf_dir, "config.json")

    arr_cfg = {
        "enabled": False,
        "enable_polling": True,
        "enable_seerr_webhook": False,
        "enable_sonarr_webhook": False,
        "enable_radarr_webhook": False,
        "polling_interval": 300,
        "full_resync_interval": 21600,
        "max_concurrent_downloads": 1,
        "download_timeout": 7200,
        "webhook_priority_enabled": True,
        "native_webhook_priority_window_seconds": 120,
        "seerr_fallback_delay_seconds": 20,
        "tags_mode": "BLACKLIST",
        "active_tag_ids": [],
        "sonarr": {"url": "", "api_key": ""},
        "radarr": {"url": "", "api_key": ""},
        "seerr": {"webhook_secret": ""},
        "sonarr_webhook": {"webhook_secret": ""},
        "radarr_webhook": {"webhook_secret": ""},
    }

    try:
        with open(config_path, encoding="utf-8") as f:
            full_cfg = json.load(f)
        file_arr = full_cfg.get("ARR", {})
        if file_arr:
            arr_cfg.update(file_arr)
    except Exception as exc:
        logger.warning(f"Could not read ARR config from config.json: {exc}")

    # Environment variable overrides (higher priority)
    def _env(key, default=None):
        return os.environ.get(key, default)

    def _env_bool(key, default=False):
        val = _env(key)
        if val is None:
            return default
        return val.strip().lower() in {"1", "true", "yes", "on"}

    def _env_int(key, default=0):
        val = _env(key)
        if val is None:
            return default
        try:
            return int(val)
        except (ValueError, TypeError):
            return default

    if _env("USE_ARR_SERVICES") is not None:
        arr_cfg["enabled"] = _env_bool("USE_ARR_SERVICES")
    if _env("ENABLE_ARR_POLLING") is not None:
        arr_cfg["enable_polling"] = _env_bool("ENABLE_ARR_POLLING")
    if _env("ENABLE_SEERR_WEBHOOK") is not None:
        arr_cfg["enable_seerr_webhook"] = _env_bool("ENABLE_SEERR_WEBHOOK")
    if _env("ARR_POLLING_INTERVAL"):
        arr_cfg["polling_interval"] = _env_int("ARR_POLLING_INTERVAL", 300)
    if _env("ARR_FULL_RESYNC_INTERVAL"):
        arr_cfg["full_resync_interval"] = _env_int("ARR_FULL_RESYNC_INTERVAL", 21600)

    sonarr_url = _env("SONARR_URL")
    sonarr_key = _env("SONARR_API_KEY")
    if sonarr_url:
        arr_cfg["sonarr"]["url"] = sonarr_url
    if sonarr_key:
        arr_cfg["sonarr"]["api_key"] = sonarr_key

    radarr_url = _env("RADARR_URL")
    radarr_key = _env("RADARR_API_KEY")
    if radarr_url:
        arr_cfg["radarr"]["url"] = radarr_url
    if radarr_key:
        arr_cfg["radarr"]["api_key"] = radarr_key

    webhook_secret = _env("SEERR_WEBHOOK_SECRET")
    if webhook_secret:
        arr_cfg["seerr"]["webhook_secret"] = webhook_secret

    if _env("ENABLE_SONARR_WEBHOOK") is not None:
        arr_cfg["enable_sonarr_webhook"] = _env_bool("ENABLE_SONARR_WEBHOOK")
    if _env("ENABLE_RADARR_WEBHOOK") is not None:
        arr_cfg["enable_radarr_webhook"] = _env_bool("ENABLE_RADARR_WEBHOOK")
    if _env("ARR_MAX_CONCURRENT_DOWNLOADS"):
        arr_cfg["max_concurrent_downloads"] = _env_int("ARR_MAX_CONCURRENT_DOWNLOADS", 1)
    if _env("ARR_WEBHOOK_PRIORITY_ENABLED") is not None:
        arr_cfg["webhook_priority_enabled"] = _env_bool("ARR_WEBHOOK_PRIORITY_ENABLED")
    if _env("ARR_NATIVE_WEBHOOK_PRIORITY_WINDOW_SECONDS"):
        arr_cfg["native_webhook_priority_window_seconds"] = _env_int("ARR_NATIVE_WEBHOOK_PRIORITY_WINDOW_SECONDS", 120)
    if _env("ARR_SEERR_FALLBACK_DELAY_SECONDS"):
        arr_cfg["seerr_fallback_delay_seconds"] = _env_int("ARR_SEERR_FALLBACK_DELAY_SECONDS", 20)
    if _env("SONARR_WEBHOOK_SECRET"):
        arr_cfg["sonarr_webhook"]["webhook_secret"] = _env("SONARR_WEBHOOK_SECRET")
    if _env("RADARR_WEBHOOK_SECRET"):
        arr_cfg["radarr_webhook"]["webhook_secret"] = _env("RADARR_WEBHOOK_SECRET")

    return arr_cfg


def _build_clients(cfg: dict):
    """Construct SonarrClient and RadarrClient from config."""
    from .clients.radarr_client import RadarrClient
    from .clients.sonarr_client import SonarrClient

    sonarr = None
    radarr = None

    sonarr_cfg = cfg.get("sonarr", {})
    if sonarr_cfg.get("url") and sonarr_cfg.get("api_key"):
        sonarr = SonarrClient(sonarr_cfg["url"], sonarr_cfg["api_key"])

    radarr_cfg = cfg.get("radarr", {})
    if radarr_cfg.get("url") and radarr_cfg.get("api_key"):
        radarr = RadarrClient(radarr_cfg["url"], radarr_cfg["api_key"])

    return sonarr, radarr


def _extract_provider_from_tags(client, tag_ids: list) -> str:
    """Return provider override from provider-<service> tags, or empty for config order."""
    if not client or not tag_ids:
        return ""
    try:
        tags_map = client.get_tags_map()
    except Exception as exc:
        logger.warning(f"Could not read ARR tags map for provider selection: {exc}")
        return ""

    for tag_id in tag_ids:
        label = (tags_map.get(tag_id, "") or "").strip().lower()
        if label.startswith("provider-"):
            return label.replace("provider-", "", 1).strip()
    return ""


def _dedup_key(item: dict, season_num: int | None = None, ep_num: int | None = None) -> str:
    """Build a dedup key for an ARR item."""
    content_type = item.get("content_type", "unknown")
    arr_id = item.get("id", 0)

    if content_type == "movie":
        return f"radarr_{arr_id}"
    else:
        return f"sonarr_{arr_id}_s{season_num}_e{ep_num}"


def _normalize_external_id(value) -> str | None:
    if value in (None, "") or isinstance(value, bool):
        return None
    value = str(value).strip()

    # Sonarr/Radarr use 0 for "no id"; it is never a real TMDB/TVDB/IMDB id.
    return value if value and value != "0" else None


def _series_tmdb_id(series: dict, fallback_tmdb_id=None) -> str | None:
    """Return a TMDB id for Sonarr data, converting TVDB via TMDB when needed."""
    direct = _normalize_external_id(series.get("tmdbId") or fallback_tmdb_id)
    if direct:
        return direct

    tvdb_id = _normalize_external_id(series.get("tvdbId"))
    if not tvdb_id:
        return None

    try:
        from VibraVid.provider.tmdb import tmdb_client

        if not tmdb_client.api_key:
            return None
        resolved = tmdb_client.get_tmdb_id_by_external_id(tvdb_id, "tvdb_id", "tv")
        return _normalize_external_id(resolved)
    except Exception as exc:
        logger.warning(f"Could not convert Sonarr tvdbId={tvdb_id} to TMDB: {exc}")
        return None


def _tvdb_id_for_tmdb(tmdb_id) -> str | None:
    """Resolve Seerr's TMDB id to TVDB for Sonarr versions lacking tmdbId."""
    normalized = _normalize_external_id(tmdb_id)
    if not normalized:
        return None
    try:
        from VibraVid.provider.tmdb import tmdb_client

        if not tmdb_client.api_key:
            return None
        return _normalize_external_id(tmdb_client.get_external_id(int(normalized), "tv", "tvdb_id"))
    except Exception as exc:
        logger.warning(f"Could not resolve TVDB id for TMDB tv/{normalized}: {exc}")
        return None


def _enqueue_if_new(
    item: dict,
    sync_source: str,
    season_num: int | None = None,
    ep_num: int | None = None,
    episode_id: int | None = None,
) -> bool:
    """
    Create ArrMediaRequest + ArrProcessingQueue entries if not already present.
    Returns True if newly enqueued, False if duplicate.
    """
    from django.db import IntegrityError, transaction
    from searchapp.models import ArrMediaRequest, ArrProcessingQueue

    key = _dedup_key(item, season_num, ep_num)
    if item.get("content_type") != "movie" and not item.get("tmdbId"):
        resolved_tmdb_id = _series_tmdb_id(item)
        if resolved_tmdb_id:
            item["tmdbId"] = resolved_tmdb_id

    with _enqueue_lock:
        # Check for active (non-completed) queue entry
        existing = ArrProcessingQueue.objects.filter(
            dedup_key=key,
            completed_at__isnull=True,
        ).first()

        if existing:
            logger.debug(f"Skipping duplicate enqueue: {key}")
            return False

        # Also skip if recently completed successfully (within last 6 hours)
        recent = ArrProcessingQueue.objects.filter(
            dedup_key=key,
            success=True,
            completed_at__gte=timezone.now() - timedelta(hours=6),
        ).first()

        if recent:
            logger.debug(f"Skipping recently completed: {key}")
            return False

        # Re-open a previously failed/non-successful queue row for retry.
        reusable = (
            ArrProcessingQueue.objects.filter(
                dedup_key=key,
                completed_at__isnull=False,
            )
            .exclude(success=True)
            .first()
        )
        if reusable:
            reusable.completed_at = None
            reusable.started_at = None
            reusable.success = None
            reusable.save(update_fields=["completed_at", "started_at", "success"])
            reusable.media_request.status = ArrMediaRequest.Status.PENDING
            reusable.media_request.sync_source = sync_source
            reusable.media_request.last_synced_at = timezone.now()
            reusable.media_request.tmdb_id = str(item.get("tmdbId") or "") or None
            reusable.media_request.tvdb_id = str(item.get("tvdbId") or item.get("tvdb_id") or "") or None
            reusable.media_request.imdb_id = str(item.get("imdbId") or item.get("imdb_id") or "") or None
            reusable.media_request.save(
                update_fields=["status", "sync_source", "last_synced_at", "tmdb_id", "tvdb_id", "imdb_id"]
            )
            logger.info(f"Re-enqueued for retry: {key}")
            return True

        content_type = item.get("content_type", "serie")
        arr_source = "radarr" if content_type == "movie" else "sonarr"
        try:
            with transaction.atomic():
                media_req = ArrMediaRequest.objects.create(
                    arr_id=item.get("id", 0),
                    arr_source=arr_source,
                    title=item.get("title", "Unknown"),
                    content_type=content_type,
                    season_number=season_num,
                    episode_number=ep_num,
                    episode_id=episode_id,
                    year=item.get("year"),
                    provider=item.get("provider", ""),
                    status=ArrMediaRequest.Status.PENDING,
                    sync_source=sync_source,
                    imdb_id=str(item.get("imdbId") or item.get("imdb_id") or "") or None,
                    tmdb_id=str(item.get("tmdbId") or item.get("tmdb_id") or "") or None,
                    tvdb_id=str(item.get("tvdbId") or item.get("tvdb_id") or "") or None,
                    last_synced_at=timezone.now(),
                )

                ArrProcessingQueue.objects.create(
                    dedup_key=key,
                    media_request=media_req,
                )
        except IntegrityError:
            logger.debug(f"Concurrent enqueue detected, skipping duplicate: {key}")
            return False

        logger.info(f"Enqueued: {key}")
        return True


def _mark_downloading(item: dict, season_num=None, ep_num=None) -> None:
    """Mark an active queue row as started so unmonitor cleanup leaves it alone."""
    from searchapp.models import ArrMediaRequest, ArrProcessingQueue

    key = _dedup_key(item, season_num, ep_num)
    try:
        queue_entry = ArrProcessingQueue.objects.filter(dedup_key=key, completed_at__isnull=True).first()
        if queue_entry:
            update_fields = []
            if queue_entry.started_at is None:
                queue_entry.started_at = timezone.now()
                update_fields.append("started_at")
            if update_fields:
                queue_entry.save(update_fields=update_fields)
            queue_entry.media_request.status = ArrMediaRequest.Status.DOWNLOADING
            queue_entry.media_request.save(update_fields=["status"])
    except Exception as exc:
        logger.error(f"Failed to mark downloading {key}: {exc}")


def _skip_pending_queue_entry(queue_entry, reason: str) -> bool:
    """Close a not-yet-started queue row as skipped."""
    from searchapp.models import ArrMediaRequest

    if queue_entry.started_at or queue_entry.completed_at:
        return False

    queue_entry.completed_at = timezone.now()
    queue_entry.success = False
    queue_entry.save(update_fields=["completed_at", "success"])
    queue_entry.media_request.status = ArrMediaRequest.Status.SKIPPED
    queue_entry.media_request.save(update_fields=["status"])
    logger.info(f"Skipped pending ARR queue '{queue_entry.dedup_key}': {reason}")
    return True


def _skip_pending_queue(item: dict, reason: str, season_num=None, ep_num=None) -> bool:
    """Skip a matching pending queue row if it has not started yet."""
    from searchapp.models import ArrProcessingQueue

    key = _dedup_key(item, season_num, ep_num)
    queue_entry = (
        ArrProcessingQueue.objects.filter(
            dedup_key=key,
            completed_at__isnull=True,
            started_at__isnull=True,
        )
        .select_related("media_request")
        .first()
    )
    if not queue_entry:
        return False
    return _skip_pending_queue_entry(queue_entry, reason)


def _is_radarr_movie_monitored(radarr, item: dict) -> bool:
    """Return False when Radarr currently marks the movie as unmonitored."""
    if item.get("monitored") is False:
        return False
    if not radarr or not item.get("id"):
        return True
    try:
        movie = radarr.get_movie_by_id(item["id"])
        return movie.get("monitored", True) is not False
    except Exception as exc:
        logger.warning(f"Could not verify Radarr monitored state for movie {item.get('id')}: {exc}")
        return True


def _is_sonarr_episode_monitored(sonarr, item: dict, episode: dict) -> bool:
    """Return False when Sonarr currently marks the series/episode as unmonitored."""
    if item.get("monitored") is False or episode.get("monitored") is False:
        return False
    if sonarr and item.get("id"):
        try:
            series = sonarr.get_series_by_id(item["id"])
            if series.get("monitored", True) is False:
                return False
        except Exception as exc:
            logger.warning(f"Could not verify Sonarr monitored state for series {item.get('id')}: {exc}")
    episode_id = episode.get("id")
    if not sonarr or not episode_id:
        return True
    try:
        current = sonarr.get_episode(episode_id)
        return current.get("monitored", True) is not False
    except Exception as exc:
        logger.warning(f"Could not verify Sonarr monitored state for episode {episode_id}: {exc}")
        return True


def _skip_unmonitored_movie(radarr, item: dict, context: str) -> bool:
    """Skip a pending movie queue row if Radarr currently marks it unmonitored."""
    if _is_radarr_movie_monitored(radarr, item):
        return False

    _skip_pending_queue(item, "Radarr movie is unmonitored")
    logger.info(f"{context} Movie '{item.get('title')}' is unmonitored in Radarr, skipping")
    return True


def _episode_has_aired(episode: dict) -> bool:
    """True when Sonarr gives the episode an air date that is not in the future."""
    air_date = episode.get("airDateUtc")
    if not air_date:
        return False
    
    try:
        aired_at = datetime.fromisoformat(str(air_date).replace("Z", "+00:00"))
    except ValueError:
        return False
    
    if aired_at.tzinfo is None:
        aired_at = aired_at.replace(tzinfo=dt_timezone.utc)
    
    return aired_at <= timezone.now()


def _skip_unmonitored_episode(sonarr, item: dict, season: dict, episode: dict, context: str) -> bool:
    """Skip a pending episode queue row if Sonarr currently marks it unmonitored."""
    if _is_sonarr_episode_monitored(sonarr, item, episode):
        return False

    _skip_pending_queue(
        item,
        "Sonarr episode is unmonitored",
        season["number"],
        episode["episodeNumber"],
    )
    logger.info(f"{context} Skipping '{item.get('title')}' S{season['number']}E{episode['episodeNumber']} because Sonarr marks it unmonitored")
    return True


def _reconcile_unmonitored_pending(sonarr=None, radarr=None) -> int:
    """Skip pending queue rows whose ARR item is now unmonitored.

    Rows already marked as started are intentionally left untouched so an
    in-flight download can finish.
    """
    from searchapp.models import ArrMediaRequest, ArrProcessingQueue

    candidates = ArrProcessingQueue.objects.filter(
        completed_at__isnull=True,
        started_at__isnull=True,
        media_request__status=ArrMediaRequest.Status.PENDING,
    ).select_related("media_request")

    skipped = 0
    series_cache = {}
    for queue_entry in candidates:
        req = queue_entry.media_request
        try:
            if req.arr_source == "radarr" and radarr:
                movie = radarr.get_movie_by_id(req.arr_id)
                if movie.get("monitored", True) is False:
                    skipped += int(_skip_pending_queue_entry(queue_entry, "Radarr movie is unmonitored"))

            elif req.arr_source == "sonarr" and sonarr:
                series = series_cache.get(req.arr_id)
                if series is None:
                    series = sonarr.get_series_by_id(req.arr_id)
                    series_cache[req.arr_id] = series
                if series.get("monitored", True) is False:
                    skipped += int(_skip_pending_queue_entry(queue_entry, "Sonarr series is unmonitored"))
                    continue

                episode = None
                if req.episode_id:
                    episode = sonarr.get_episode(req.episode_id)
                elif req.season_number is not None and req.episode_number is not None:
                    episodes = sonarr.get_episodes_for_series(req.arr_id)
                    episode = next(
                        (
                            ep
                            for ep in episodes
                            if ep.get("seasonNumber") == req.season_number
                            and ep.get("episodeNumber") == req.episode_number
                        ),
                        None,
                    )
                if episode and episode.get("monitored", True) is False:
                    skipped += int(_skip_pending_queue_entry(queue_entry, "Sonarr episode is unmonitored"))
        except Exception as exc:
            logger.warning(f"Could not reconcile monitored state for '{queue_entry.dedup_key}': {exc}")

    if skipped:
        logger.info(f"Skipped {skipped} pending ARR queue item(s) now marked unmonitored")
    return skipped


def _should_skip_seerr_event(event_data: dict, cfg: dict) -> bool:
    """Native webhook priority: Sonarr/Radarr events win over Seerr."""
    from searchapp.models import ArrWebhookEvent

    if not cfg.get("webhook_priority_enabled", True):
        return False

    media = event_data.get("media", {}) or {}
    media_type = str(media.get("media_type") or "").lower()
    tmdb_id = media.get("tmdbId")
    if not media_type or not tmdb_id:
        return False

    preferred_source = "radarr" if media_type == "movie" else "sonarr"
    window_seconds = max(0, int(cfg.get("native_webhook_priority_window_seconds", 120)))
    if window_seconds == 0:
        return False

    cutoff = timezone.now() - timedelta(seconds=window_seconds)
    return ArrWebhookEvent.objects.filter(
        source=preferred_source,
        tmdb_id=str(tmdb_id),
        received_at__gte=cutoff,
    ).exists()


def _targeted_sync_with_retry(
    lookup_fn,
    source_label: str,
    max_retries: int = 3,
    base_delay: float = 30.0,
) -> int:
    """
    Try lookup_fn() to sync a specific item from a webhook.
    Retries with exponential backoff if the item isn't found.

    Retry schedule (default): +30s, +60s, +120s → then full sync.
    Returns total enqueued count.
    """
    for attempt in range(max_retries):
        count = lookup_fn()
        if count > 0:
            logger.info(f"[{source_label}] Targeted sync found {count} item(s) on attempt {attempt + 1}")
            return count
        if attempt < max_retries - 1:
            delay = base_delay * (2**attempt)
            logger.info(f"[{source_label}] Target media not found, retrying in {delay:.0f}s (attempt {attempt + 1}/{max_retries})")
            time.sleep(delay)

    logger.info(f"[{source_label}] All {max_retries} targeted retries exhausted, falling back to full polling sync")
    return trigger_polling_sync(full_resync=True)


def trigger_polling_sync(full_resync: bool = False) -> int:
    """
    Run a polling sync against Sonarr/Radarr.
    Returns the number of newly enqueued items.
    """
    close_old_connections()

    cfg = _load_arr_config()
    if not cfg.get("enabled"):
        return 0

    sonarr, radarr = _build_clients(cfg)
    if not sonarr and not radarr:
        logger.warning("No Sonarr/Radarr clients configured, skipping polling")
        return 0

    import searchapp.views as arr_views_mod

    arr_views_mod.set_max_download_slots(cfg.get("max_concurrent_downloads", 1))

    from .downloader_service import ArrDownloaderService
    from .processor_service import ArrProcessorService

    _reconcile_orphaned_downloads(older_than_seconds=cfg.get("download_timeout", 7200))
    _reconcile_unmonitored_pending(sonarr=sonarr, radarr=radarr)

    processor = ArrProcessorService(
        sonarr=sonarr,
        radarr=radarr,
        tags_mode=cfg.get("tags_mode", "BLACKLIST"),
        active_tag_ids=cfg.get("active_tag_ids", []),
    )

    missing_items = processor.get_missing_items()
    if not missing_items:
        logger.info("No missing items found during polling")
        return 0

    logger.info(f"Found {len(missing_items)} missing items from ARR")

    enqueued = 0
    downloader = ArrDownloaderService(sonarr, radarr)
    for item in missing_items:
        content_type = item.get("content_type")

        if content_type == "movie":
            if _skip_unmonitored_movie(radarr, item, "[polling]"):
                continue
            if _enqueue_if_new(item, "polling"):
                enqueued += 1
                try:
                    _mark_downloading(item)
                    if downloader._process_movie(item):
                        _mark_completed(item)
                    else:
                        _mark_failed(item, downloader.last_error or "download_failed")
                except Exception as exc:
                    logger.error(f"Download failed for movie '{item.get('title')}': {exc}")
                    _mark_failed(item, str(exc))

        elif content_type == "serie":
            for season in item.get("seasons", []):
                for episode in season.get("episodes", []):
                    ep_item = {**item, "seasons": [{"number": season["number"], "episodes": [episode]}]}
                    if _skip_unmonitored_episode(sonarr, item, season, episode, "[polling]"):
                        continue
                    if _enqueue_if_new(
                        item,
                        "polling",
                        season_num=season["number"],
                        ep_num=episode["episodeNumber"],
                        episode_id=episode["id"],
                    ):
                        enqueued += 1
                        try:
                            _mark_downloading(item, season["number"], episode["episodeNumber"])
                            if downloader._process_serie(ep_item):
                                _mark_completed(item, season["number"], episode["episodeNumber"])
                            else:
                                _mark_failed(
                                    item,
                                    downloader.last_error or "download_failed",
                                    season["number"],
                                    episode["episodeNumber"],
                                )
                        except Exception as exc:
                            logger.error(f"Download failed for '{item.get('title')}' S{season['number']}E{episode['episodeNumber']}: {exc}")
                            _mark_failed(item, str(exc), season["number"], episode["episodeNumber"])

    logger.info(f"Polling sync complete: {enqueued} new items enqueued")
    return enqueued


def _reconcile_orphaned_downloads(older_than_seconds: int | None = None) -> int:
    """
    Recover queue rows stuck in 'Downloading' (started but never completed).

    When a download crashes, hangs, or the server is restarted mid-download, the
    queue row keeps ``started_at`` set with ``completed_at`` NULL. The dedup check in
    ``_enqueue_if_new`` then treats it as an active entry forever, so the item is
    never retried and stays visible as 'Downloading' in the UI. Mark such rows as
    FAILED (``completed_at`` set, ``success=False``) so the reusable-retry branch of
    ``_enqueue_if_new`` re-attempts them on the next polling cycle.

    Returns the count of queue rows recovered.
    """
    from searchapp.models import ArrMediaRequest, ArrProcessingQueue

    candidates = ArrProcessingQueue.objects.filter(
        started_at__isnull=False,
        completed_at__isnull=True,
    ).select_related("media_request")
    if older_than_seconds is not None:
        candidates = candidates.filter(started_at__lte=timezone.now() - timedelta(seconds=older_than_seconds))

    recovered = 0
    for queue_entry in candidates:
        queue_entry.completed_at = timezone.now()
        queue_entry.success = False
        queue_entry.save(update_fields=["completed_at", "success"])
        queue_entry.media_request.status = ArrMediaRequest.Status.FAILED
        queue_entry.media_request.save(update_fields=["status"])
        recovered += 1

    if recovered:
        logger.warning(f"Recovered {recovered} orphaned 'Downloading' queue entr{'y' if recovered == 1 else 'ies'} (stuck mid-download); will retry on next poll")
    return recovered


def trigger_webhook_sync(event_data: dict) -> int:
    """
    Handle an incoming Seerr/Overseerr webhook and trigger immediate sync.

    Gets media info directly from Sonarr/Radarr by TMDB ID, then enqueues
    and downloads missing episodes/movies for that specific title.
    No longer relies on get_missing_items() which may be empty when the webhook fires
    before Sonarr/Radarr have synced the media.
    """
    logger.info("[trigger_webhook_sync] ===== WEBHOOK SYNC START =====")
    logger.info(f"[trigger_webhook_sync] Event data keys: {list(event_data.keys())}")
    logger.info(f"[trigger_webhook_sync] Full event data: {json.dumps(event_data, indent=2, ensure_ascii=False)[:2000]}")

    close_old_connections()

    cfg = _load_arr_config()
    logger.info(f"[trigger_webhook_sync] ARR enabled: {cfg.get('enabled')}")
    logger.info(f"[trigger_webhook_sync] Sonarr configured: {bool(cfg.get('sonarr', {}).get('url'))}")
    logger.info(f"[trigger_webhook_sync] Radarr configured: {bool(cfg.get('radarr', {}).get('url'))}")

    if not cfg.get("enabled"):
        logger.warning("[trigger_webhook_sync] ARR services disabled, skipping")
        return 0

    if _should_skip_seerr_event(event_data, cfg):
        logger.info("[trigger_webhook_sync] Skipping Seerr event due to native webhook priority")
        return 0

    sonarr, radarr = _build_clients(cfg)
    if not sonarr and not radarr:
        logger.warning("[trigger_webhook_sync] No Sonarr/Radarr clients configured, skipping")
        return 0
    _reconcile_unmonitored_pending(sonarr=sonarr, radarr=radarr)

    import searchapp.views as arr_views_mod

    arr_views_mod.set_max_download_slots(cfg.get("max_concurrent_downloads", 1))

    # Parse the webhook payload - handle both Seerr and Sonarr formats
    media = event_data.get("media", {})
    series = event_data.get("series", {})
    tvdb_id = None

    if media:
        # Seerr/Overseerr payload format
        media_type = media.get("media_type", "").lower()  # "movie" or "tv"
        tmdb_id = media.get("tmdbId")
        # Seerr sends the TVDB id too: use it, TMDB often has no TVDB link for the show and
        # Sonarr may have no TMDB id for it.
        tvdb_id = _normalize_external_id(media.get("tvdbId"))
        media_title = media.get("title", "")
        logger.info("[trigger_webhook_sync] Detected Seerr payload format")
    elif series:
        # Sonarr SeriesAdd payload format
        media_type = "tv"  # Sonarr series events are always TV
        tmdb_id = _series_tmdb_id(series)
        tvdb_id = _normalize_external_id(series.get("tvdbId"))
        media_title = series.get("title", "")
        logger.info("[trigger_webhook_sync] Detected Sonarr payload format (SeriesAdd)")
    else:
        logger.warning("[trigger_webhook_sync] Unknown payload format - no 'media' or 'series' key")
        return trigger_polling_sync()

    logger.info(f"[trigger_webhook_sync] media_type={media_type}, tmdbId={tmdb_id}")
    logger.info(f"[trigger_webhook_sync] media title: {media_title}")

    if not tmdb_id and not (media_type == "tv" and series.get("id")):
        logger.warning("[trigger_webhook_sync] Webhook payload missing tmdbId, falling back to full polling sync")
        return trigger_polling_sync()

    logger.info(f"[trigger_webhook_sync] Webhook received for {media_type} (tmdbId={tmdb_id})")

    from .downloader_service import ArrDownloaderService

    # -- Movie webhook --
    if media_type == "movie" and radarr:
        logger.info("[trigger_webhook_sync] Processing MOVIE webhook")
        try:
            logger.info("[trigger_webhook_sync] Fetching movies from Radarr...")
            movies = radarr.get_movies()
            logger.info(f"[trigger_webhook_sync] Got {len(movies)} movies from Radarr")
        except Exception as exc:
            logger.error(f"[trigger_webhook_sync] Failed to get movies from Radarr: {exc}", exc_info=True)
            return trigger_polling_sync()

        matched = None
        for m in movies:
            if str(m.get("tmdbId")) == str(tmdb_id):
                matched = m
                break

        if not matched:
            logger.warning(f"[trigger_webhook_sync] Movie tmdbId={tmdb_id} not found in Radarr, falling back to full sync")
            return trigger_polling_sync()

        logger.info(f"[trigger_webhook_sync] Found movie in Radarr: {matched.get('title')} (id={matched.get('id')})")

        # Apply tag filtering (hold/pause check)
        radarr_tags = radarr.get_tags_map()
        tag_names = [radarr_tags.get(t, "") for t in matched.get("tags", [])]
        logger.info(f"[trigger_webhook_sync] Movie tags: {tag_names}")

        if "hold" in tag_names or "pausa" in tag_names:
            logger.info(f"[trigger_webhook_sync] Movie '{matched['title']}' on hold/pause, skipping webhook")
            return -1  # signal to stop retrying

        provider = _extract_provider_from_tags(radarr, matched.get("tags", []))
        logger.info(f"[trigger_webhook_sync] Using provider: {provider}")

        item = {
            "content_type": "movie",
            "id": matched["id"],
            "title": matched["title"],
            "year": matched.get("year"),
            "path": matched.get("path", ""),
            "tags": matched.get("tags", []),
            "tmdbId": matched.get("tmdbId"),
            "imdbId": matched.get("imdbId"),
            "monitored": matched.get("monitored", True),
            "provider": provider,
        }

        if _skip_unmonitored_movie(radarr, item, "[trigger_webhook_sync]"):
            return -1

        if not _enqueue_if_new(item, "webhook"):
            logger.info(f"[trigger_webhook_sync] Movie '{matched['title']}' already enqueued, skipping")
            return -1  # already enqueued, stop retrying

        logger.info(f"[trigger_webhook_sync] Starting download for movie '{matched['title']}'")
        downloader = ArrDownloaderService(sonarr, radarr)
        _mark_downloading(item)
        if downloader._process_movie(item):
            _mark_completed(item)
            logger.info(f"[trigger_webhook_sync] Movie '{matched['title']}' processed successfully")
            return 1
        else:
            _mark_failed(item, downloader.last_error or "download_failed")
            logger.warning(f"[trigger_webhook_sync] Movie '{matched['title']}' download failed")
            return -1  # definitive failure, stop retrying

    # -- TV webhook --
    elif media_type == "tv" and sonarr:
        logger.info("[trigger_webhook_sync] Processing TV webhook")

        matched = None

        # If we have a Sonarr payload with series.id, use it directly (most reliable)
        if series and series.get("id"):
            series_id = series.get("id")
            logger.info(f"[trigger_webhook_sync] Using series.id={series_id} from Sonarr payload")

            # Retry up to 3 times with delay (Sonarr might still be processing the series)
            for attempt in range(3):
                try:
                    matched = sonarr.get_series_by_id(series_id)
                    if matched:
                        logger.info(f"[trigger_webhook_sync] Found series by id: '{matched.get('title')}'")
                        break
                except Exception as exc:
                    logger.warning(f"[trigger_webhook_sync] Attempt {attempt + 1}/3: Failed to get series by id {series_id}: {exc}")

                if attempt < 2:
                    delay = 5 * (attempt + 1)
                    logger.info(f"[trigger_webhook_sync] Retrying in {delay}s...")
                    time.sleep(delay)

            if not matched:
                logger.warning(f"[trigger_webhook_sync] Series id={series_id} not found after retries, trying by tmdbId/tvdbId...")

        # Search each external-id namespace independently. Seerr gives us a
        # TMDB id; older Sonarr payloads may expose only TVDB.
        if not matched:
            target_tmdb = _normalize_external_id(tmdb_id)
            target_tvdb = tvdb_id or _tvdb_id_for_tmdb(target_tmdb)
            identity_conflict = False
            try:
                logger.info(
                    f"[trigger_webhook_sync] Searching Sonarr for tmdbId={target_tmdb}, tvdbId={target_tvdb}..."
                )
                series_list = sonarr.get_series()
                logger.info(f"[trigger_webhook_sync] Got {len(series_list)} series from Sonarr")
            except Exception as exc:
                logger.error(f"[trigger_webhook_sync] Failed to get series from Sonarr: {exc}", exc_info=True)
                return trigger_polling_sync()

            # Try multiple times as Sonarr might still be processing
            for attempt in range(3):
                for s in series_list:
                    s_tmdb = _normalize_external_id(s.get("tmdbId"))
                    s_tvdb = _normalize_external_id(s.get("tvdbId"))
                    tmdb_match = bool(target_tmdb and s_tmdb == target_tmdb)
                    tvdb_match = bool(target_tvdb and s_tvdb == target_tvdb)
                    if tvdb_match and target_tmdb and s_tmdb and s_tmdb != target_tmdb:
                        identity_conflict = True
                        logger.warning(
                            f"[trigger_webhook_sync] Skipping '{s.get('title')}' because its Sonarr "
                            f"tmdbId={s_tmdb} conflicts with requested tmdbId={target_tmdb}, "
                            f"despite matching tvdbId={target_tvdb}"
                        )
                        continue
                    if tmdb_match or tvdb_match:
                        matched = s
                        logger.info(
                            f"[trigger_webhook_sync] Found series by "
                            f"{'tmdbId' if tmdb_match else 'tvdbId'}: '{s.get('title')}'"
                        )
                        break

                if matched:
                    break

                if attempt < 2:
                    logger.info(f"[trigger_webhook_sync] Series not found, retrying in {5 * (attempt + 1)}s... (attempt {attempt + 1}/3)")
                    time.sleep(5 * (attempt + 1))
                    # Refresh series list on retry
                    try:
                        series_list = sonarr.get_series()
                    except Exception:
                        pass

            if not matched:
                if identity_conflict:
                    logger.error(
                        f"[trigger_webhook_sync] Sonarr external IDs conflict with requested TMDB tv/{target_tmdb}; "
                        "refusing full-polling fallback"
                    )
                    return -1
                logger.warning(
                    f"[trigger_webhook_sync] TV tmdbId={target_tmdb}/tvdbId={target_tvdb} "
                    "not found in Sonarr after retries, falling back to full sync"
                )
                return trigger_polling_sync()

        logger.info(f"[trigger_webhook_sync] Found series in Sonarr: {matched.get('title')} (id={matched.get('id')})")

        # Apply tag filtering via ArrProcessorService
        from .processor_service import ArrProcessorService

        processor = ArrProcessorService(
            sonarr=sonarr,
            radarr=radarr,
            tags_mode=cfg.get("tags_mode", "BLACKLIST"),
            active_tag_ids=cfg.get("active_tag_ids", []),
        )

        # Build item from Sonarr data
        serie_item = {
            "content_type": "serie",
            "id": matched["id"],
            "title": matched["title"],
            "year": matched.get("year"),
            "path": matched.get("path", ""),
            "tags": matched.get("tags", []),
            "tmdbId": _series_tmdb_id(matched, fallback_tmdb_id=tmdb_id),
            "tvdbId": matched.get("tvdbId"),
            "imdbId": matched.get("imdbId"),
            "monitored": matched.get("monitored", True),
            "provider": _extract_provider_from_tags(sonarr, matched.get("tags", [])),
        }

        if serie_item.get("monitored") is False:
            logger.info(f"[trigger_webhook_sync] Series '{matched['title']}' is unmonitored in Sonarr, skipping")
            return -1

        if not processor._check_tags_validity(serie_item["title"], serie_item["tags"]):
            logger.info(f"[trigger_webhook_sync] Series '{matched['title']}' filtered out by tag rules, skipping")
            return -1

        # Get missing episodes directly from Sonarr API (don't rely on processor.get_missing_items)
        logger.info(f"[trigger_webhook_sync] Getting episodes for series {matched['id']}...")
        try:
            episodes = sonarr.get_episodes_for_series(matched["id"])
        except Exception as exc:
            logger.error(f"[trigger_webhook_sync] Failed to get episodes: {exc}", exc_info=True)
            return trigger_polling_sync(full_resync=True)

        # Filter: monitored, already aired episodes without files
        missing_eps = [e for e in episodes if e.get("monitored") and not e.get("hasFile") and _episode_has_aired(e)]

        if not missing_eps:
            logger.info(f"[trigger_webhook_sync] Series '{matched['title']}' has no monitored episodes without files")
            return -1

        logger.info(f"[trigger_webhook_sync] Found {len(missing_eps)} monitored episodes without files")

        # Group by season
        seasons_dict = {}
        for ep in missing_eps:
            s_num = ep.get("seasonNumber")
            if s_num is None or s_num == 0:
                continue
            if s_num not in seasons_dict:
                seasons_dict[s_num] = {"number": s_num, "episodes": []}
            seasons_dict[s_num]["episodes"].append(
                {
                    "id": ep["id"],
                    "title": ep.get("title", ""),
                    "seasonNumber": s_num,
                    "episodeNumber": ep["episodeNumber"],
                    "monitored": ep.get("monitored", True),
                }
            )

        serie_item["seasons"] = list(seasons_dict.values())

        downloader = ArrDownloaderService(sonarr, radarr)
        local_enqueued = 0
        for season in serie_item.get("seasons", []):
            for episode in season.get("episodes", []):
                ep_item = {**serie_item, "seasons": [{"number": season["number"], "episodes": [episode]}]}
                if _skip_unmonitored_episode(sonarr, serie_item, season, episode, "[trigger_webhook_sync]"):
                    continue
                if _enqueue_if_new(
                    serie_item,
                    "webhook",
                    season_num=season["number"],
                    ep_num=episode["episodeNumber"],
                    episode_id=episode["id"],
                ):
                    local_enqueued += 1
                    try:
                        logger.info(f"[trigger_webhook_sync] Processing S{season['number']}E{episode['episodeNumber']}")
                        _mark_downloading(serie_item, season["number"], episode["episodeNumber"])
                        if downloader._process_serie(ep_item):
                            _mark_completed(serie_item, season["number"], episode["episodeNumber"])
                        else:
                            _mark_failed(
                                serie_item,
                                downloader.last_error or "download_failed",
                                season["number"],
                                episode["episodeNumber"],
                            )
                    except Exception as exc:
                        logger.error(f"[trigger_webhook_sync] Download failed for '{serie_item.get('title')}' S{season['number']}E{episode['episodeNumber']}: {exc}", exc_info=True,)
                        _mark_failed(serie_item, str(exc), season["number"], episode["episodeNumber"])

        logger.info(f"[trigger_webhook_sync] Series '{matched['title']}' processed: {local_enqueued} episodes enqueued")
        return local_enqueued

    else:
        # Unknown type or no client configured — full sync
        logger.warning(f"[trigger_webhook_sync] Unknown media_type '{media_type}' or missing client (sonarr={bool(sonarr)}, radarr={bool(radarr)})")
        return trigger_polling_sync()


def trigger_sonarr_webhook_sync(event_data: dict) -> int:
    """
    Handle a Sonarr native webhook. Syncs ONLY the series in the payload.

    For Download/Grab events: processes episodes from the webhook payload.
    For SeriesAdd events: queries Sonarr for missing episodes and syncs them.
    No longer relies on the missing items list (which may be empty when the webhook fires after import).
    """
    close_old_connections()

    cfg = _load_arr_config()
    if not cfg.get("enabled"):
        logger.warning("[Sonarr WH] ARR services disabled, ignoring webhook")
        return 0

    sonarr, _ = _build_clients(cfg)
    if not sonarr:
        logger.warning("[Sonarr WH] Sonarr not configured, ignoring webhook")
        return 0
    _reconcile_unmonitored_pending(sonarr=sonarr, radarr=None)

    import searchapp.views as arr_views_mod

    arr_views_mod.set_max_download_slots(cfg.get("max_concurrent_downloads", 1))

    series_data = event_data.get("series", {})
    series_id = series_data.get("id")
    if not series_id:
        logger.warning("[Sonarr WH] Webhook payload missing series.id — ignoring")
        return 0

    event_type = event_data.get("eventType", "").lower()
    logger.info(f"[Sonarr WH] eventType={event_type}, seriesId={series_id}, title='{series_data.get('title')}'")

    # Get series details directly from Sonarr (don't rely on missing items list)
    try:
        series = sonarr.get_series_by_id(series_id)
    except Exception as exc:
        logger.error(f"[Sonarr WH] Failed to get series {series_id} from Sonarr: {exc}")
        return 0

    if not series:
        logger.error(f"[Sonarr WH] Series {series_id} not found in Sonarr")
        return 0

    # Build item entry directly from Sonarr data
    serie = {
        "content_type": "serie",
        "id": series_id,
        "title": series.get("title", ""),
        "year": series.get("year"),
        "path": series.get("path", ""),
        "tags": series.get("tags", []),
        "tmdbId": _series_tmdb_id(series, fallback_tmdb_id=series_data.get("tmdbId")),
        "tvdbId": series.get("tvdbId"),
        "imdbId": series.get("imdbId"),
        "monitored": series.get("monitored", True),
        "provider": _extract_provider_from_tags(sonarr, series.get("tags", [])),
    }

    if serie.get("monitored") is False:
        logger.info(f"[Sonarr WH] Series '{serie['title']}' is unmonitored, skipping")
        return -1

    # Apply tag filtering
    from .processor_service import ArrProcessorService

    processor = ArrProcessorService(
        sonarr=sonarr,
        radarr=None,
        tags_mode=cfg.get("tags_mode", "BLACKLIST"),
        active_tag_ids=cfg.get("active_tag_ids", []),
    )

    if not processor._check_tags_validity(serie["title"], serie["tags"]):
        logger.info(f"[Sonarr WH] Series '{serie['title']}' filtered by tags, skipping")
        return -1

    # For SeriesAdd events: get episodes directly from Sonarr API
    if event_type == "seriesadd":
        logger.info(f"[Sonarr WH] SeriesAdd event — getting episodes for '{serie['title']}'")
        from .downloader_service import ArrDownloaderService

        downloader = ArrDownloaderService(sonarr, None)

        # Get all episodes for this series directly from Sonarr
        # (don't rely on wanted/missing which may not be updated yet)
        try:
            episodes = sonarr.get_episodes_for_series(series_id)
        except Exception as exc:
            logger.error(f"[Sonarr WH] Failed to get episodes for series {series_id}: {exc}", exc_info=True)
            return trigger_polling_sync(full_resync=True)

        # Filter: monitored, already aired episodes without files
        missing_eps = [e for e in episodes if e.get("monitored") and not e.get("hasFile") and _episode_has_aired(e)]

        if not missing_eps:
            logger.info(f"[Sonarr WH] Series '{serie['title']}' has no monitored episodes without files")
            return -1

        logger.info(f"[Sonarr WH] Found {len(missing_eps)} monitored episodes without files")

        # Group by season
        seasons_dict = {}
        for ep in missing_eps:
            s_num = ep.get("seasonNumber")
            if s_num is None or s_num == 0:
                continue
            if s_num not in seasons_dict:
                seasons_dict[s_num] = {"number": s_num, "episodes": []}
            seasons_dict[s_num]["episodes"].append(
                {
                    "id": ep["id"],
                    "title": ep.get("title", ""),
                    "seasonNumber": s_num,
                    "episodeNumber": ep["episodeNumber"],
                    "monitored": ep.get("monitored", True),
                }
            )

        serie["seasons"] = list(seasons_dict.values())

        enqueued = 0
        for season in serie.get("seasons", []):
            for episode in season.get("episodes", []):
                ep_item = {**serie, "seasons": [{"number": season["number"], "episodes": [episode]}]}
                if _skip_unmonitored_episode(sonarr, serie, season, episode, "[Sonarr WH]"):
                    continue
                if _enqueue_if_new(
                    serie,
                    "webhook",
                    season_num=season["number"],
                    ep_num=episode["episodeNumber"],
                    episode_id=episode["id"],
                ):
                    enqueued += 1
                    try:
                        logger.info(f"[Sonarr WH] Processing S{season['number']}E{episode['episodeNumber']}")
                        _mark_downloading(serie, season["number"], episode["episodeNumber"])
                        if downloader._process_serie(ep_item):
                            _mark_completed(serie, season["number"], episode["episodeNumber"])
                        else:
                            _mark_failed(
                                serie,
                                downloader.last_error or "download_failed",
                                season["number"],
                                episode["episodeNumber"],
                            )
                    except Exception as exc:
                        logger.error(f"[Sonarr WH] Download failed S{season['number']}E{episode['episodeNumber']}: {exc}", exc_info=True)
                        _mark_failed(serie, str(exc), season["number"], episode["episodeNumber"])

        logger.info(f"[Sonarr WH] Completed: {enqueued} episodes enqueued for '{serie['title']}'")
        return enqueued

    # For Download/Grab events: use episodes from webhook payload
    wh_episodes = event_data.get("episodes", [])
    if not wh_episodes:
        logger.info("[Sonarr WH] No episodes in webhook payload")
        return 0

    # Build seasons/episodes structure from webhook payload
    seasons_dict = {}
    for ep in wh_episodes:
        s_num = ep.get("seasonNumber")
        if s_num is None:
            continue
        if s_num not in seasons_dict:
            seasons_dict[s_num] = {"number": s_num, "episodes": []}
        seasons_dict[s_num]["episodes"].append(
            {
                "id": ep.get("id"),
                "title": ep.get("title", ""),
                "seasonNumber": s_num,
                "episodeNumber": ep.get("episodeNumber"),
                "monitored": ep.get("monitored", True),
            }
        )

    serie["seasons"] = list(seasons_dict.values())

    # Process episodes
    from .downloader_service import ArrDownloaderService

    downloader = ArrDownloaderService(sonarr, None)
    enqueued = 0
    for season in serie.get("seasons", []):
        for episode in season.get("episodes", []):
            ep_item = {**serie, "seasons": [{"number": season["number"], "episodes": [episode]}]}
            if _skip_unmonitored_episode(sonarr, serie, season, episode, "[Sonarr WH]"):
                continue
            if _enqueue_if_new(
                serie,
                "webhook",
                season_num=season["number"],
                ep_num=episode["episodeNumber"],
                episode_id=episode.get("id"),
            ):
                enqueued += 1
                try:
                    _mark_downloading(serie, season["number"], episode["episodeNumber"])
                    if downloader._process_serie(ep_item):
                        _mark_completed(serie, season["number"], episode["episodeNumber"])
                    else:
                        _mark_failed(
                            serie,
                            downloader.last_error or "download_failed",
                            season["number"],
                            episode["episodeNumber"],
                        )
                except Exception as exc:
                    logger.error(f"[Sonarr WH] Download failed S{season['number']}E{episode['episodeNumber']}: {exc}")
                    _mark_failed(serie, str(exc), season["number"], episode["episodeNumber"])

    logger.info(f"[Sonarr WH] Completed: {enqueued} episodes enqueued for '{serie['title']}'")
    return enqueued


def trigger_radarr_webhook_sync(event_data: dict) -> int:
    """
    Handle a Radarr native webhook. Syncs ONLY the movie in the payload.

    Gets movie info directly from Radarr and processes it.
    No longer relies on the missing items list (which may be empty when the webhook fires after import).
    """
    close_old_connections()

    cfg = _load_arr_config()
    if not cfg.get("enabled"):
        logger.warning("[Radarr WH] ARR services disabled, ignoring webhook")
        return 0

    _, radarr = _build_clients(cfg)
    if not radarr:
        logger.warning("[Radarr WH] Radarr not configured, ignoring webhook")
        return 0
    _reconcile_unmonitored_pending(sonarr=None, radarr=radarr)

    import searchapp.views as arr_views_mod

    arr_views_mod.set_max_download_slots(cfg.get("max_concurrent_downloads", 1))

    movie_data = event_data.get("movie", {})
    movie_id = movie_data.get("id")
    if not movie_id:
        logger.warning("[Radarr WH] Webhook payload missing movie.id — ignoring")
        return 0

    event_type = event_data.get("eventType", "").lower()
    logger.info(f"[Radarr WH] eventType={event_type}, movieId={movie_id}, title='{movie_data.get('title')}'")

    # Get movie details directly from Radarr (don't rely on missing items list)
    try:
        movie = radarr.get_movie_by_id(movie_id)
    except Exception as exc:
        logger.error(f"[Radarr WH] Failed to get movie {movie_id} from Radarr: {exc}")
        return 0

    if not movie:
        logger.error(f"[Radarr WH] Movie {movie_id} not found in Radarr")
        return 0

    # Build item entry directly from Radarr data
    movie_item = {
        "content_type": "movie",
        "id": movie_id,
        "title": movie.get("title", ""),
        "year": movie.get("year"),
        "path": movie.get("path", ""),
        "tags": movie.get("tags", []),
        "tmdbId": movie.get("tmdbId"),
        "imdbId": movie.get("imdbId"),
        "monitored": movie.get("monitored", True),
        "provider": _extract_provider_from_tags(radarr, movie.get("tags", [])),
    }

    if _skip_unmonitored_movie(radarr, movie_item, "[Radarr WH]"):
        return -1

    # Apply tag filtering
    from .processor_service import ArrProcessorService

    processor = ArrProcessorService(
        sonarr=None,
        radarr=radarr,
        tags_mode=cfg.get("tags_mode", "BLACKLIST"),
        active_tag_ids=cfg.get("active_tag_ids", []),
    )

    if not processor._check_tags_validity(movie_item["title"], movie_item["tags"]):
        logger.info(f"[Radarr WH] Movie '{movie_item['title']}' filtered by tags, skipping")
        return -1

    # Process movie
    if not _enqueue_if_new(movie_item, "webhook"):
        logger.info(f"[Radarr WH] Movie '{movie_item['title']}' already enqueued, skipping")
        return -1

    from .downloader_service import ArrDownloaderService

    downloader = ArrDownloaderService(None, radarr)
    try:
        _mark_downloading(movie_item)
        if downloader._process_movie(movie_item):
            _mark_completed(movie_item)
            logger.info(f"[Radarr WH] Movie '{movie_item['title']}' processed successfully")
            return 1
        else:
            _mark_failed(movie_item, downloader.last_error or "download_failed")
            return -1
    except Exception as exc:
        logger.error(f"[Radarr WH] Download failed for '{movie_item.get('title')}': {exc}")
        _mark_failed(movie_item, str(exc))
        return -1


def _mark_completed(item: dict, season_num=None, ep_num=None):
    """Mark queue entry as completed."""
    from searchapp.models import ArrMediaRequest, ArrProcessingQueue

    key = _dedup_key(item, season_num, ep_num)
    try:
        queue_entry = ArrProcessingQueue.objects.filter(dedup_key=key, completed_at__isnull=True).first()
        if queue_entry:
            queue_entry.completed_at = timezone.now()
            queue_entry.success = True
            queue_entry.save(update_fields=["completed_at", "success"])
            queue_entry.media_request.status = ArrMediaRequest.Status.COMPLETED
            queue_entry.media_request.save(update_fields=["status"])
    except Exception as exc:
        logger.error(f"Failed to mark completed {key}: {exc}")


def _mark_failed(item: dict, error: str, season_num=None, ep_num=None):
    """Mark queue entry as failed."""
    from searchapp.models import ArrMediaRequest, ArrProcessingQueue

    key = _dedup_key(item, season_num, ep_num)
    try:
        queue_entry = ArrProcessingQueue.objects.filter(dedup_key=key, completed_at__isnull=True).first()
        if queue_entry:
            queue_entry.completed_at = timezone.now()
            queue_entry.success = False
            queue_entry.save(update_fields=["completed_at", "success"])
            status = ArrMediaRequest.Status.FAILED
            normalized_error = (error or "").lower()
            if "import_not_confirmed" in normalized_error or "import not confirmed" in normalized_error:
                status = ArrMediaRequest.Status.IMPORT_PENDING
            queue_entry.media_request.status = status
            queue_entry.media_request.save(update_fields=["status"])
    except Exception as exc:
        logger.error(f"Failed to mark failed {key}: {exc}")
