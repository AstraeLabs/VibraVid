# 23-01-26

import json
import os
import threading
import time
from typing import Any


class SingletonMeta(type):
    _instances = {}
    _lock = threading.Lock()

    def __call__(cls, *args, **kwargs):
        with cls._lock:
            if cls not in cls._instances:
                cls._instances[cls] = super().__call__(*args, **kwargs)
            return cls._instances[cls]


class DownloadTracker(metaclass=SingletonMeta):
    def __init__(self):
        if not hasattr(self, "_initialized"):
            self._initialized = True
            self._init_tracker()

    def _init_tracker(self):
        self.downloads: dict[str, dict[str, Any]] = {}
        self.history: list[dict[str, Any]] = []
        self.stop_events: dict[str, threading.Event] = {}
        self.active_processes: dict[str, list[any]] = {}
        self.stale_timeout_seconds = 30 * 60
        self._lock = threading.Lock()
        self._load_persisted_history()

    def _django_enabled(self) -> bool:
        return bool(os.environ.get("DJANGO_SETTINGS_MODULE"))

    def _get_history_model(self):
        if not self._django_enabled():
            return None
        try:
            from GUI.searchapp.models import DownloadHistory

            return DownloadHistory
        except Exception:
            return None

    def _load_persisted_history(self) -> None:
        model = self._get_history_model()
        if model:
            try:
                rows = list(model.objects.order_by("-created_at")[:50])
                payloads: list[dict[str, Any]] = []
                for row in reversed(rows):
                    try:
                        payloads.append(json.loads(row.payload))
                    except Exception:
                        continue
                self.history = payloads
                return
            except Exception:
                pass

        try:
            from VibraVid.utils import config_manager

            path = os.path.join(config_manager.base_path, ".cache", "history.json")
            if os.path.exists(path):
                with open(path, encoding="utf-8") as fh:
                    data = json.load(fh)
                if isinstance(data, list):
                    self.history = data[-50:]
        except Exception:
            pass

    def _persist_history_entry(self, entry: dict[str, Any]) -> None:
        model = self._get_history_model()
        persisted_to_model = False
        if model:
            try:
                model.objects.create(
                    download_id=str(entry.get("id") or ""),
                    payload=json.dumps(entry),
                )
                persisted_to_model = True
            except Exception:
                pass

        if persisted_to_model:
            return

        try:
            from VibraVid.utils import config_manager
            path = os.path.join(config_manager.base_path, ".cache", "history.json")
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(self.history[-50:], fh, indent=2)
        except Exception:
            pass

    def start_download(
        self, download_id: str, title: str, site: str, media_type: str = "Film", path: str = None, poster: str = None
    ):
        hook_context = None
        poster = poster if poster is not None else context_tracker.poster_url
        with self._lock:
            self.stop_events[download_id] = threading.Event()
            self.active_processes[download_id] = []
            self.downloads[download_id] = {
                "id": download_id,
                "title": title,
                "site": site,
                "type": media_type,
                "status": "starting",
                "path": path,
                "poster": poster,
                "progress": 0,
                "speed": "0B/s",
                "size": "0B/0B",
                "segments": "0/0",
                "quality": "",
                "language": "",
                "start_time": time.time(),
                "last_update": time.time(),
                "tasks": {},  # For multi-stream downloads (video, audio, etc)
                "cli_search": context_tracker.cli_search,
                "cli_item": context_tracker.cli_item,
            }
            hook_context = {
                "download_id": download_id,
                "download_title": title,
                "download_site": site,
                "download_media_type": media_type,
                "download_status": "starting",
                "download_path": path,
                "success": "",
                "download_error": "",
            }

        try:
            from VibraVid.utils.hooks import execute_hooks

            execute_hooks("pre_download", context=hook_context)
        except Exception:
            pass

    def update_progress(
        self,
        download_id: str,
        task_key: str,
        progress: float = None,
        speed: str = None,
        size: str = None,
        segments: str = None,
        status: str = None,
        label: str = None,
        display_label: str = None,
        quality: str = None,
        language: str = None,
    ):
        with self._lock:
            if download_id in self.downloads:
                dl = self.downloads[download_id]
                dl["status"] = status or "downloading"
                dl["last_update"] = time.time()

                if quality:
                    dl["quality"] = quality
                if language:
                    dl["language"] = language

                # Get or create task state
                if task_key not in dl["tasks"]:
                    dl["tasks"][task_key] = {
                        "progress": 0.0,
                        "speed": "0B/s",
                        "size": "0B/0B",
                        "segments": "0/0",
                    }

                task = dl["tasks"][task_key]

                if label is not None:
                    task["label"] = label
                if display_label is not None:
                    task["display_label"] = display_label

                # Update task fields if new values are provided
                if progress is not None:
                    try:
                        task["progress"] = float(progress)
                    except (ValueError, TypeError):
                        pass

                if speed:
                    task["speed"] = speed
                if size:
                    task["size"] = size
                if segments:
                    task["segments"] = segments

                # Update main download state based on all active tasks
                video_audio_tasks = [
                    t
                    for k, t in dl["tasks"].items()
                    if "video" in k.lower() or "audio" in k.lower() or "vid" in k.lower() or "aud" in k.lower()
                ]

                if video_audio_tasks:
                    dl["progress"] = sum(t["progress"] for t in video_audio_tasks) / len(video_audio_tasks)
                    v_task = next(
                        (t for k, t in dl["tasks"].items() if "video" in k.lower() or "vid" in k.lower()),
                        video_audio_tasks[0],
                    )
                    dl["speed"] = v_task["speed"]
                    dl["size"] = v_task["size"]
                    dl["segments"] = v_task["segments"]
                else:
                    dl["progress"] = task["progress"]
                    dl["speed"] = task["speed"]
                    dl["size"] = task["size"]
                    dl["segments"] = task["segments"]

    def update_status(self, download_id: str, status: str):
        with self._lock:
            if download_id in self.downloads:
                self.downloads[download_id]["status"] = status
                self.downloads[download_id]["last_update"] = time.time()

    def request_stop(self, download_id: str):
        """Signal a download to stop and terminate its processes."""
        with self._lock:
            if download_id in self.stop_events:
                self.stop_events[download_id].set()

            if download_id in self.downloads:
                self.downloads[download_id]["status"] = "cancelling..."

            # Terminate registered processes
            if download_id in self.active_processes:
                for proc in self.active_processes[download_id]:
                    try:
                        if hasattr(proc, "terminate"):
                            proc.terminate()
                        elif hasattr(proc, "cancel"):
                            proc.cancel()
                    except Exception:
                        pass

    def is_stopped(self, download_id: str) -> bool:
        """Check if a stop has been requested for this download."""
        with self._lock:
            event = self.stop_events.get(download_id)
            return event.is_set() if event else False

    def shutdown(self):
        """Shutdown all active downloads and kill their processes."""
        print("Shutting down DownloadTracker, stopping all active downloads...")
        with self._lock:
            for download_id in list(self.downloads.keys()):
                self.request_stop(download_id)

            # Kill all registered processes
            for processes in self.active_processes.values():
                for proc in processes:
                    try:
                        if hasattr(proc, "terminate"):
                            proc.terminate()
                        elif hasattr(proc, "cancel"):
                            proc.cancel()
                    except Exception:
                        pass

    def complete_download(self, download_id: str, success: bool = True, error: str = None, path: str = None):
        hook_context = None
        with self._lock:
            dl = self.downloads.pop(download_id, None)

            # Cleanup signals and processes regardless of where the final state is stored.
            self.stop_events.pop(download_id, None)
            self.active_processes.pop(download_id, None)

            found_in_history = False
            if dl is None:
                # Recovery path: if a long-running download was temporarily marked timed_out,
                # allow final completion to overwrite that provisional state.
                for item in reversed(self.history):
                    if item.get("id") == download_id:
                        if item.get("status") != "timed_out":
                            return
                        dl = item
                        found_in_history = True
                        break

            if dl is None:
                return

            dl["status"] = "completed" if success else "failed"
            if error == "cancelled":
                dl["status"] = "cancelled"

            dl["end_time"] = time.time()
            dl["error"] = error
            if path is not None:
                dl["path"] = path
            dl["progress"] = 100 if success else dl.get("progress", 0)

            if found_in_history:
                try:
                    self.history.remove(dl)
                except ValueError:
                    pass
            self.history.append(dl)

            hook_context = {
                "download_id": dl.get("id"),
                "download_title": dl.get("title"),
                "download_site": dl.get("site"),
                "download_media_type": dl.get("type"),
                "download_status": dl.get("status"),
                "download_path": path or dl.get("path"),
                "success": success,
                "download_error": error or "",
            }

            # Limit history size
            if len(self.history) > 50:
                self.history.pop(0)

            self._persist_history_entry(dl)

        if hook_context:
            try:
                from VibraVid.utils.hooks import execute_hooks, remember_hook_context

                if hook_context.get("success") and hook_context.get("download_path"):
                    remember_hook_context("post_run", hook_context)
                execute_hooks("post_download", context=hook_context)
            except Exception:
                pass

    def get_active_downloads(self) -> list[dict[str, Any]]:
        with self._lock:
            # Clean up very old downloads with no tracker updates.
            now = time.time()
            timeout_seconds = int(getattr(self, "stale_timeout_seconds", 0) or 0)
            to_remove = []
            for did, dl in self.downloads.items():
                last_update = float(dl.get("last_update", now))
                stale_for = now - last_update
                if timeout_seconds > 0 and stale_for > timeout_seconds:
                    to_remove.append((did, int(stale_for)))

            for did, stale_for in to_remove:
                dl = self.downloads.pop(did)
                dl["status"] = "timed_out"
                dl["error"] = f"No tracker updates for {stale_for}s"
                dl["end_time"] = now
                self.stop_events.pop(did, None)
                self.active_processes.pop(did, None)
                self.history.append(dl)

            if len(self.history) > 50:
                self.history = self.history[-50:]

            return list(self.downloads.values())

    def get_history(self) -> list[dict[str, Any]]:
        with self._lock:
            return list(reversed(self.history))

    def clear_history(self):
        """Clear all download history."""
        with self._lock:
            self.history.clear()
        model = self._get_history_model()
        if model:
            try:
                model.objects.all().delete()
            except Exception:
                pass
        try:
            from VibraVid.utils import config_manager
            path = os.path.join(config_manager.base_path, ".cache", "history.json")
            if os.path.exists(path):
                os.remove(path)
        except Exception:
            pass


class ContextTracker:
    _global_is_gui = False

    def __init__(self):
        self.local = threading.local()

    @property
    def download_id(self):
        return getattr(self.local, "download_id", None)

    @download_id.setter
    def download_id(self, value):
        self.local.download_id = value

    @property
    def media_type(self):
        return getattr(self.local, "media_type", "Film")

    @media_type.setter
    def media_type(self, value):
        self.local.media_type = value

    @property
    def site_name(self):
        return getattr(self.local, "site_name", None)

    @site_name.setter
    def site_name(self, value):
        self.local.site_name = value

    @property
    def is_gui(self):
        return getattr(self.local, "is_gui", self._global_is_gui)

    @is_gui.setter
    def is_gui(self, value):
        self.local.is_gui = value
        if value:
            ContextTracker._global_is_gui = True

    @property
    def is_cancelled_callback(self):
        return getattr(self.local, "is_cancelled_callback", None)

    @is_cancelled_callback.setter
    def is_cancelled_callback(self, value):
        self.local.is_cancelled_callback = value

    @property
    def is_parallel_cli(self):
        return getattr(self.local, "is_parallel_cli", False)

    @is_parallel_cli.setter
    def is_parallel_cli(self, value):
        self.local.is_parallel_cli = value

    @property
    def hide_manifest_info(self):
        return getattr(self.local, "hide_manifest_info", False)

    @hide_manifest_info.setter
    def hide_manifest_info(self, value):
        self.local.hide_manifest_info = value

    @property
    def title(self):
        return getattr(self.local, "title", None)

    @title.setter
    def title(self, value):
        self.local.title = value

    @property
    def season(self):
        return getattr(self.local, "season", 0)

    @season.setter
    def season(self, value):
        self.local.season = value

    @property
    def episode(self):
        return getattr(self.local, "episode", 0)

    @episode.setter
    def episode(self, value):
        self.local.episode = value

    @property
    def episode_name(self):
        return getattr(self.local, "episode_name", None)

    @episode_name.setter
    def episode_name(self, value):
        self.local.episode_name = value

    @property
    def cli_site(self):
        return getattr(self.local, "cli_site", None)

    @cli_site.setter
    def cli_site(self, value):
        self.local.cli_site = value

    @property
    def cli_search(self):
        return getattr(self.local, "cli_search", None)

    @cli_search.setter
    def cli_search(self, value):
        self.local.cli_search = value

    @property
    def cli_item(self):
        return getattr(self.local, "cli_item", None)

    @cli_item.setter
    def cli_item(self, value):
        self.local.cli_item = value

    @property
    def cli_season_selection(self):
        return getattr(self.local, "cli_season_selection", None)

    @cli_season_selection.setter
    def cli_season_selection(self, value):
        self.local.cli_season_selection = value

    @property
    def cli_episode_selection(self):
        return getattr(self.local, "cli_episode_selection", None)

    @cli_episode_selection.setter
    def cli_episode_selection(self, value):
        self.local.cli_episode_selection = value

    @property
    def output_path(self):
        return getattr(self.local, "output_path", None)

    @output_path.setter
    def output_path(self, value):
        self.local.output_path = value

    @property
    def max_segments(self):
        return getattr(self.local, "max_segments", None)

    @max_segments.setter
    def max_segments(self, value):
        self.local.max_segments = value

    @property
    def max_time(self):
        return getattr(self.local, "max_time", None)

    @max_time.setter
    def max_time(self, value):
        self.local.max_time = value

    @property
    def bypass_vault_cache(self):
        return getattr(self.local, "bypass_vault_cache", None)

    @bypass_vault_cache.setter
    def bypass_vault_cache(self, value):
        self.local.bypass_vault_cache = value

    @property
    def skip_decrypt(self) -> bool:
        return bool(getattr(self.local, "skip_decrypt", False))

    @skip_decrypt.setter
    def skip_decrypt(self, value):
        self.local.skip_decrypt = value

    @property
    def no_livemux(self) -> bool:
        return bool(getattr(self.local, "no_livemux", False))

    @no_livemux.setter
    def no_livemux(self, value):
        self.local.no_livemux = value

    @property
    def force_livemux(self) -> bool:
        return bool(getattr(self.local, "force_livemux", False))

    @force_livemux.setter
    def force_livemux(self, value):
        self.local.force_livemux = value

    @property
    def no_concurrent(self) -> bool:
        return bool(getattr(self.local, "no_concurrent", False))

    @no_concurrent.setter
    def no_concurrent(self, value):
        self.local.no_concurrent = value

    @property
    def skip_no_match(self) -> bool:
        return bool(getattr(self.local, "skip_no_match", False))

    @skip_no_match.setter
    def skip_no_match(self, value):
        self.local.skip_no_match = value

    @property
    def log_engine_output(self):
        return getattr(self.local, "log_engine_output", None)

    @log_engine_output.setter
    def log_engine_output(self, value):
        self.local.log_engine_output = value

    @property
    def anonymize_keys(self):
        return getattr(self.local, "anonymize_keys", False)

    @anonymize_keys.setter
    def anonymize_keys(self, value):
        self.local.anonymize_keys = value

    @property
    def resolve_only(self):
        return getattr(self.local, "resolve_only", False)

    @resolve_only.setter
    def resolve_only(self, value):
        self.local.resolve_only = value

    @property
    def site_options(self):
        return getattr(self.local, "site_options", None) or {}

    @site_options.setter
    def site_options(self, value):
        self.local.site_options = value

    @property
    def chapters(self):
        return getattr(self.local, "chapters", None)

    @chapters.setter
    def chapters(self, value):
        self.local.chapters = value

    @property
    def poster_url(self):
        return getattr(self.local, "poster_url", None)

    @poster_url.setter
    def poster_url(self, value):
        self.local.poster_url = value

    @property
    def fallback_poster_url(self):
        return getattr(self.local, "fallback_poster_url", None)

    @fallback_poster_url.setter
    def fallback_poster_url(self, value):
        self.local.fallback_poster_url = value

    @property
    def series_tmdb_id(self):
        return getattr(self.local, "series_tmdb_id", None)

    @series_tmdb_id.setter
    def series_tmdb_id(self, value):
        self.local.series_tmdb_id = value

    @property
    def should_print(self) -> bool:
        """Returns False when console output should be suppressed (parallel CLI or GUI)."""
        return not self.is_gui and not self.is_parallel_cli

    @property
    def download_errors(self) -> list:
        lst = getattr(self.local, "download_errors", None)
        if lst is None:
            lst = []
            self.local.download_errors = lst
        return lst

    @property
    def download_ok_count(self) -> int:
        return getattr(self.local, "download_ok_count", 0)

    def reset_download_result(self) -> None:
        """Clear the per-download outcome before a new (GUI) download starts."""
        self.local.download_errors = []
        self.local.download_ok_count = 0

    def report_download_error(self, message) -> None:
        if message:
            self.download_errors.append(str(message))

    def report_download_success(self) -> None:
        self.local.download_ok_count = self.download_ok_count + 1

    def __str__(self):
        return f"ContextTracker(download_id={self.download_id}, title={self.title}, site_name={self.site_name}, media_type={self.media_type}, season={self.season}, episode={self.episode}, episode_name={self.episode_name}, is_gui={self.is_gui}, is_parallel_cli={self.is_parallel_cli})"


# Global instance
download_tracker = DownloadTracker()
context_tracker = ContextTracker()
