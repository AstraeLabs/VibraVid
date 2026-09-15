# 06.06.25

import concurrent.futures
import inspect
import json
import logging
import os
import shutil
import threading
import time
from typing import Any

from django.contrib import messages
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect
from django.utils import timezone

from GUI.searchapp.api import get_api, get_available_sites, get_site_categories
from GUI.searchapp.api.base import Entries
from VibraVid.cli.run import equivalent_command_builder
from VibraVid.core.ui.tracker import context_tracker, download_tracker
from VibraVid.services._base.site_extra_args import (
    get_site_extra_args_schema as get_site_extra_args_schema,
)
from VibraVid.services._base.site_extra_args import (
    parse_site_extra_args as parse_site_extra_args,
)
from VibraVid.services._base.site_extra_args import (
    resolve_persisted_site_options as _resolve_persisted_site_options,
)

from .._download_infra import (
    _acquire_download_slot,
    _add_scheduled_download,
    _is_scheduled_cancelled,
    _release_download_slot,
    _remove_scheduled_download,
    _submit_download_task,
)
from ..library_index import forget
from ..models import WatchlistItem

logger = logging.getLogger(__name__)


_recent_webhooks = {}
_recent_webhooks_lock = threading.Lock()
_WEBHOOK_DEDUP_WINDOW = 300
_GLOBAL_SEARCH_TIMEOUT = 45


def _mark_task_failed(download_id: str, title: str, site: str, media_type: str, error: str) -> None:
    """Write the terminal 'failed' state for a GUI download task."""
    try:
        _remove_scheduled_download(download_id)
        already_in_history = any(
            item.get("id") == download_id for item in download_tracker.get_history()
        )

        if download_id not in download_tracker.downloads and not already_in_history:
            download_tracker.start_download(download_id, title, site, media_type)

        if not already_in_history:
            download_tracker.complete_download(download_id, success=False, error=error)
    except Exception as tracker_err:
        logger.exception("[_task] Failed to update download tracker: %s", tracker_err)


def _is_recent_webhook(tmdb_id, source=None, window_seconds=None, touch=True):
    """Return True if (source, tmdb_id) was processed in the last window_seconds."""
    if not tmdb_id:
        return False
    if window_seconds is None:
        window_seconds = _WEBHOOK_DEDUP_WINDOW
    now = time.time()
    key = (source, str(tmdb_id))
    with _recent_webhooks_lock:
        last_time = _recent_webhooks.get(key)
        if last_time and (now - last_time) < window_seconds:
            return True
        if touch:
            _recent_webhooks[key] = now
        # Clean old entries
        old_keys = [k for k, v in _recent_webhooks.items() if now - v > window_seconds]
        for k in old_keys:
            del _recent_webhooks[k]
        return False


def _mark_native_webhook_seen(tmdb_id, source: str):
    if not tmdb_id:
        return
    _is_recent_webhook(tmdb_id, source=source, window_seconds=_WEBHOOK_DEDUP_WINDOW, touch=True)


_MEDIA_KINDS = {
    'film': 'movie', 'movie': 'movie', 'ova': 'movie',
    'tv': 'series', 'serie': 'series', 'series': 'series', 'show': 'series',
    'ona': 'series', 'tv short': 'series', 'special': 'series',
    'song': 'song', 'track': 'song', 'music': 'song',
    'album': 'album',
    'book': 'book', 'ebook': 'book', 'audiobook': 'book',
    'live': 'live',
}
_DIRECT_KINDS = {'movie', 'song', 'book'}
_FILTER_GROUPS = {'album': 'song'}


def _known_series_tmdb_id(raw: Any) -> int | None:
    """Return a valid integer TMDB id from an item_payload value, or None."""
    if raw in (None, ""):
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def _is_anime_source(source_alias: str) -> bool:
    """True when the service is an anime one."""
    try:
        return str(get_site_categories().get(source_alias, "")).strip().lower() == 'anime'
    except Exception:
        return False


def _media_item_to_display_dict(item: Entries, source_alias: str) -> dict[str, Any]:
    """Convert Entries to template-friendly dictionary."""
    poster_url = item.poster if item.poster else "https://via.placeholder.com/300x450?text=Search"

    raw_type = str(item.type or "").strip().lower()
    media_kind = _MEDIA_KINDS.get(raw_type, 'movie' if item.is_movie else 'series')
    is_direct = media_kind in _DIRECT_KINDS

    filter_group = 'anime' if _is_anime_source(source_alias) else _FILTER_GROUPS.get(media_kind, media_kind)

    result = {
        'display_title': item.name,
        'display_type': item.type.capitalize(),
        'source': source_alias.capitalize(),
        'source_alias': source_alias,
        'bg_image_url': poster_url,
        'media_kind': media_kind,
        'filter_group': filter_group,
        'is_movie': is_direct,
        'year': item.year,
        'slug': item.slug,
        'tmdb_id': item.tmdb_id,
        'availability': item.desc,
    }

    # Ensure payload reflects the GUI behaviour (so start_download treats songs like single-item downloads)
    result['payload_json'] = json.dumps({**item.__dict__, 'is_movie': is_direct})
    return result


def _accepts_audio_format(func) -> bool:
    """True when *func* declares audio_format (or a **kwargs catch-all)."""
    try:
        params = inspect.signature(func).parameters
    except (TypeError, ValueError):
        return False
    return 'audio_format' in params or any(p.kind == p.VAR_KEYWORD for p in params.values())


def _to_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def _resolve_global_sites(site_token: str) -> list[str] | None:
    """Map a special dropdown token to a list of sites."""
    if site_token == "__all__":
        return get_available_sites()
    if site_token.startswith("__cat__:"):
        category = site_token.split(":", 1)[1]
        return [s for s, c in get_site_categories().items() if c == category]
    return None


def _run_global_search(query: str, sites: list[str]):
    """Search `query` across many sites in parallel."""
    results: list[dict[str, Any]] = []
    failed: list[str] = []
    if not sites:
        return results, failed

    def _one(site: str):
        api = get_api(site)
        return [_media_item_to_display_dict(item, site) for item in api.search(query)]

    executor = concurrent.futures.ThreadPoolExecutor(max_workers=min(len(sites), 8))
    future_map = {executor.submit(_one, s): s for s in sites}
    try:
        for fut in concurrent.futures.as_completed(future_map, timeout=_GLOBAL_SEARCH_TIMEOUT):
            site = future_map[fut]
            try:
                results.extend(fut.result())
            except Exception as e:
                logger.warning(f"[global_search] '{site}' failed: {e}")
                failed.append(site)

    except concurrent.futures.TimeoutError:
        for fut, site in future_map.items():
            if not fut.done():
                logger.warning(f"[global_search] '{site}' timed out after {_GLOBAL_SEARCH_TIMEOUT}s")
                failed.append(site)
    finally:
        executor.shutdown(wait=False, cancel_futures=True)

    results.sort(key=lambda r: (str(r.get("display_title") or "").lower(), r.get("source_alias") or ""))
    return results, failed


def _log_gui_equivalent_command(site: str, item_payload: dict[str, Any], season: str = None, episodes: str = None) -> None:
    """Log the CLI command equivalent to a GUI download."""
    try:
        equivalent_command_builder.log_equivalent_command_from_params(site=site, search=item_payload.get("name"), season=season, episode=episodes)
    except Exception:
        pass


def _run_download_in_thread(site: str, item_payload: dict[str, Any], season: str = None, episodes: str = None, media_type: str = "Film", output_path: str = None, audio_format: str = None, poster: str = None) -> "concurrent.futures.Future":
    """Run download in background thread. Returns a Future for callers that need to wait"""
    name = item_payload.get('name', 'Unknown')
    if season and episodes:
        title = f"{name} - S{season} E{episodes}"
    elif season:
        title = f"{name} - S{season}"
    else:
        title = name

    poster = poster or item_payload.get('poster')
    download_id = f"{site}_{int(time.time())}_{hash(title) % 10000}"
    _add_scheduled_download(download_id, title, site, media_type, season, episodes, poster=poster)

    def _task():
        _acquire_download_slot()
        try:
            if _is_scheduled_cancelled(download_id):
                logger.info("[_task] Download cancelled before start")
                _remove_scheduled_download(download_id)
                return

            # Set context for downloaders in this thread
            context_tracker.download_id = download_id
            context_tracker.site_name = site
            context_tracker.media_type = media_type
            context_tracker.fallback_poster_url = poster
            context_tracker.series_tmdb_id = _known_series_tmdb_id(item_payload.get("tmdb_id"))
            context_tracker.is_gui = True
            context_tracker.is_cancelled_callback = _is_scheduled_cancelled
            context_tracker.season = int(season) if str(season or "").isdigit() else 0
            context_tracker.episode = int(episodes) if str(episodes or "").isdigit() else 0
            context_tracker.site_options = _resolve_persisted_site_options(site)

            api = get_api(site)

            # Create Entries from payload
            entries_fields = {k: v for k, v in item_payload.items() if k in Entries.__dataclass_fields__}
            media_item = Entries(**entries_fields)

            # audio_format override for providers that support it (e.g. Spotify)
            if audio_format:
                media_item.audio_format = audio_format

            # output_path stored in context so downstream helpers can read it
            if output_path:
                context_tracker.output_path = output_path

            _log_gui_equivalent_command(site, item_payload, season, episodes)
            logger.debug("[_task] Calling api.start_download with: season=%s, episodes=%s, output_path=%s, audio_format=%s", season, episodes, output_path, audio_format,)
            if audio_format and _accepts_audio_format(api.start_download):
                api.start_download(media_item, season=season, episodes=episodes, audio_format=audio_format)
            else:
                api.start_download(media_item, season=season, episodes=episodes)

            final_state = next(
                (item for item in download_tracker.get_history() if item.get("id") == download_id),
                None,
            )
            if final_state and final_state.get("status") in {"failed", "cancelled", "timed_out"}:
                error_msg = final_state.get("error") or final_state.get("status") or "download_failed"
                raise RuntimeError(error_msg)

            logger.info("[_task] Download completed successfully")
            forget(name)

            # Clear the scheduled placeholder and guarantee a terminal history entry
            _remove_scheduled_download(download_id)
            already_in_history = any(
                item.get("id") == download_id
                for item in download_tracker.get_history()
            )
            if download_id not in download_tracker.downloads and not already_in_history:
                download_tracker.start_download(download_id, title, site, media_type)
                download_tracker.complete_download(download_id, success=True)
        except Exception as e:
            error_msg = str(e) or "Unknown error"
            logger.exception("[_task] Download task failed: %s", error_msg)
            _mark_task_failed(download_id, title, site, media_type, error_msg)
            raise
        finally:
            context_tracker.output_path = None
            context_tracker.season = 0
            context_tracker.episode = 0
            context_tracker.episode_name = None
            context_tracker.series_tmdb_id = None
            context_tracker.is_cancelled_callback = None
            context_tracker.site_options = None
            _release_download_slot()

    return _submit_download_task(_task)


def _handle_series_download(request: HttpRequest) -> HttpResponse:
    """Handle POST downloads from series_detail: full series, full season, or selected episodes."""
    source_alias = request.POST.get("source_alias")
    item_payload_raw = request.POST.get("item_payload")
    download_type = request.POST.get("download_type")
    season_number = request.POST.get("season_number")
    selected_episodes = request.POST.get("selected_episodes", "")

    if not all([source_alias, item_payload_raw]):
        messages.error(request, "Missing base parameters for the download.")
        return redirect("search_home")

    try:
        item_payload = json.loads(item_payload_raw)
    except (ValueError, TypeError):
        messages.error(request, "Error parsing the data.")
        return redirect("search_home")

    name = item_payload.get("name")
    media_type = (item_payload.get("type") or "tv").lower()

    # --- FULL SERIES DOWNLOAD (sequential, all seasons one after another) ---
    if download_type == "full_series":
        def _download_entire_series_task():
            try:
                api = get_api(source_alias)
                entries_fields = {k: v for k, v in item_payload.items() if k in Entries.__dataclass_fields__}
                media_item = Entries(**entries_fields)
                seasons = api.get_series_metadata(media_item)

                if not seasons:
                    return

                planned_seasons = []
                for season in seasons:
                    season_num = str(season.number)
                    season_title = f"{name} - S{season_num}"
                    planned_id = f"{source_alias}_{int(time.time())}_{hash(season_title + str(season_num)) % 10000}_{season_num}"
                    planned_seasons.append((planned_id, season_num))
                    _add_scheduled_download(
                        planned_id,
                        season_title,
                        source_alias,
                        media_type,
                        season=season_num,
                        episodes="*",
                    )

                for download_id, season_num in planned_seasons:
                    if _is_scheduled_cancelled(download_id):
                        _remove_scheduled_download(download_id)
                        continue

                    _acquire_download_slot()
                    try:
                        if _is_scheduled_cancelled(download_id):
                            _remove_scheduled_download(download_id)
                            continue

                        context_tracker.download_id = download_id
                        context_tracker.site_name = source_alias
                        context_tracker.media_type = media_type
                        context_tracker.fallback_poster_url = item_payload.get("poster")
                        context_tracker.series_tmdb_id = _known_series_tmdb_id(item_payload.get("tmdb_id"))
                        context_tracker.is_gui = True
                        context_tracker.is_cancelled_callback = _is_scheduled_cancelled
                        context_tracker.site_options = _resolve_persisted_site_options(source_alias)
                        _log_gui_equivalent_command(source_alias, item_payload, season_num, "*")

                        api.start_download(media_item, season=season_num, episodes="*")
                    except Exception as e:
                        error_msg = str(e) or "Unknown error"
                        logger.exception("[_task] Download season %s: %s", season_num, e)
                        _mark_task_failed(
                            download_id, f"{name} - S{season_num}", source_alias, media_type, error_msg
                        )
                    finally:
                        context_tracker.site_options = None
                        _release_download_slot()

            except Exception as e:
                logger.exception("[_task] Full series download task: %s", e)

        _submit_download_task(_download_entire_series_task)

        return redirect("download_dashboard")

    # --- FULL SEASON DOWNLOAD ---
    elif download_type == "full_season":
        if not season_number:
            messages.error(request, "Missing season number.")
            return redirect("search_home")

        _run_download_in_thread(
            site=source_alias,
            item_payload=item_payload,
            season=season_number,
            episodes="*",
            media_type=media_type,
        )

        return redirect("download_dashboard")

    # --- SELECTED SEASONS DOWNLOAD ---
    elif download_type == "selected_seasons":
        selected_seasons_raw = request.POST.get("selected_seasons", "")
        if not selected_seasons_raw:
            messages.error(request, "No season selected.")
            return redirect("search_home")

        selected_seasons = [s.strip() for s in selected_seasons_raw.split(",") if s.strip()]

        def _download_selected_seasons_task():
            try:
                api = get_api(source_alias)
                entries_fields = {k: v for k, v in item_payload.items() if k in Entries.__dataclass_fields__}
                media_item = Entries(**entries_fields)

                planned_seasons = []
                for season_num in selected_seasons:
                    season_title = f"{name} - S{season_num}"
                    planned_id = f"{source_alias}_{int(time.time())}_{hash(season_title + str(season_num)) % 10000}_{season_num}"
                    planned_seasons.append((planned_id, season_num))
                    _add_scheduled_download(
                        planned_id,
                        season_title,
                        source_alias,
                        media_type,
                        season=season_num,
                        episodes="*",
                    )

                for download_id, season_num in planned_seasons:
                    if _is_scheduled_cancelled(download_id):
                        _remove_scheduled_download(download_id)
                        continue

                    _acquire_download_slot()
                    try:
                        if _is_scheduled_cancelled(download_id):
                            _remove_scheduled_download(download_id)
                            continue

                        context_tracker.download_id = download_id
                        context_tracker.site_name = source_alias
                        context_tracker.media_type = media_type
                        context_tracker.fallback_poster_url = item_payload.get("poster")
                        context_tracker.series_tmdb_id = _known_series_tmdb_id(item_payload.get("tmdb_id"))
                        context_tracker.is_gui = True
                        context_tracker.is_cancelled_callback = _is_scheduled_cancelled
                        context_tracker.site_options = _resolve_persisted_site_options(source_alias)
                        _log_gui_equivalent_command(source_alias, item_payload, season_num, "*")

                        api.start_download(media_item, season=season_num, episodes="*")
                    except Exception as e:
                        error_msg = str(e) or "Unknown error"
                        logger.exception("[_task] Download season %s: %s", season_num, e)
                        _mark_task_failed(
                            download_id, f"{name} - S{season_num}", source_alias, media_type, error_msg
                        )
                    finally:
                        context_tracker.site_options = None
                        _release_download_slot()

            except Exception as e:
                logger.exception("[_task] Selected seasons download task: %s", e)

        _submit_download_task(_download_selected_seasons_task)

        return redirect("download_dashboard")

    else:
        if not season_number:
            messages.error(request, "Missing season number.")
            return redirect("search_home")

        episode_param = selected_episodes.strip() if selected_episodes else None
        logger.debug("episode_param after strip: '%s'", episode_param)

        if not episode_param:
            logger.error("episode_param is empty/None!")
            messages.error(request, "No episode selected.")
            from django.urls import reverse
            url = reverse('series_detail') + f"?source_alias={source_alias}&item_payload={item_payload_raw}"
            return redirect(url)

        logger.debug("Proceeding with episodes: %s", episode_param)
        _run_download_in_thread(
            site=source_alias,
            item_payload=item_payload,
            season=season_number,
            episodes=episode_param,
            media_type=media_type,
        )
        logger.debug("Download thread started for S%s E%s", season_number, episode_param)

        return redirect("download_dashboard")


def _tag_anime_entries(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Flag downloads coming from an anime service, for display only."""
    for entry in entries:
        try:
            if _is_anime_source(str(entry.get("site") or "").strip().lower()):
                entry["is_anime"] = True
        except Exception:
            continue
    return entries


def _update_single_item(item: WatchlistItem) -> bool:
    """Internal helper to update a single watchlist item."""
    try:
        if item.is_movie:
            item.last_checked_at = timezone.now()
            item.has_new_seasons = False
            item.has_new_episodes = False
            item.save(update_fields=["last_checked_at", "has_new_seasons", "has_new_episodes"])
            return False

        api = get_api(item.source_alias)
        item_payload = json.loads(item.item_payload)
        entries_fields = {k: v for k, v in item_payload.items() if k in Entries.__dataclass_fields__}
        media_item = Entries(**entries_fields)

        if media_item.is_movie:
            item.is_movie = True
            item.last_checked_at = timezone.now()
            item.has_new_seasons = False
            item.has_new_episodes = False
            item.save(update_fields=["is_movie", "last_checked_at", "has_new_seasons", "has_new_episodes"])
            return False

        seasons = api.get_series_metadata(media_item)

        if not seasons:
            return False

        current_num_seasons = len(seasons)
        last_season = seasons[-1]
        current_last_season_episodes = last_season.episode_count

        changed = False

        # If item has 0 seasons (first add), just set the initial values without marking as "new"
        if item.num_seasons == 0:
            item.num_seasons = current_num_seasons
            item.last_season_episodes = current_last_season_episodes
            changed = True
        else:
            if current_num_seasons > item.num_seasons:
                item.has_new_seasons = True
                item.num_seasons = current_num_seasons
                changed = True

            if current_last_season_episodes > item.last_season_episodes:
                item.has_new_episodes = True
                item.last_season_episodes = current_last_season_episodes
                changed = True

        item.last_checked_at = timezone.now()
        item.save()
        return changed
    except Exception as e:
        logger.exception("Error updating %s: %s", item.name, e)
        return False


def _conf_dir() -> str:
    return os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__)))), "Conf")


def _read_json(path: str) -> dict:
    """Read JSON file at `path`. Returns {} if the file is missing or empty, or if it contains invalid JSON."""
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f) or {}
    except (ValueError, TypeError):
        return {}


def _read_json_strict(path: str) -> dict:
    """Read JSON file at `path`. Returns {} if the file is missing, but raises if the file contains invalid JSON."""
    if not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8") as f:
        return json.load(f) or {}


def _write_json_backup(path: str, data: dict) -> None:
    """Write JSON file at `path`, making a backup of the previous version if it exists."""
    if os.path.exists(path):
        try:
            shutil.copy2(path, path + ".backup")
        except OSError as e:
            logger.exception("[settings] backup failed: %s", e)
    with open(path, "w", encoding="utf-8") as f:
        f.write(json.dumps(data, indent=4, ensure_ascii=False))


def _mask_secret(val: str) -> str:
    """Mask a secret string for display in logs/UI. Shows only the last 4 characters, or "••••" if shorter."""
    val = str(val or "")
    if not val:
        return ""
    return ("…" + val[-4:]) if len(val) > 4 else "••••"


_update_check_cache: dict = {}
_UPDATE_CHECK_TTL = 3600
__all__ = ['_recent_webhooks', '_recent_webhooks_lock', '_WEBHOOK_DEDUP_WINDOW', '_GLOBAL_SEARCH_TIMEOUT', '_is_recent_webhook', '_mark_native_webhook_seen', '_MEDIA_KINDS', '_DIRECT_KINDS', '_FILTER_GROUPS', '_is_anime_source', '_media_item_to_display_dict', '_accepts_audio_format', '_to_bool', '_resolve_global_sites', '_run_global_search', '_log_gui_equivalent_command', '_run_download_in_thread', '_handle_series_download', '_tag_anime_entries', '_update_single_item', '_conf_dir', '_read_json', '_read_json_strict', '_write_json_backup', '_mask_secret', '_update_check_cache', '_UPDATE_CHECK_TTL']
