# 07.05.26

import concurrent.futures
import datetime
import json
import logging
import os
import pathlib
import re
import time
from typing import Any

from VibraVid.utils import anime_id_map

from .clients.radarr_client import RadarrClient
from .clients.sonarr_client import SonarrClient

logger = logging.getLogger("ARR")
_ARR_SEARCH_TIMEOUT = 45


class ArrDownloaderService:
    """Downloads media by invoking VibraVid's native streaming API pipeline."""

    def __init__(self, sonarr: SonarrClient, radarr: RadarrClient):
        self.sonarr = sonarr
        self.radarr = radarr
        self.last_error: str | None = None
        self.download_timeout = self._load_download_timeout()
        self._sonarr_season_format: str | None = None
        self._provider_tmdb_cache: dict[tuple, str] = {}
        self._sonarr_rename_checked = False
        self._radarr_rename_checked = False

    @staticmethod
    def _load_download_timeout() -> int:
        """Max seconds to block on a single download before giving up (configurable)."""
        try:
            from .arr_service import _load_arr_config

            return int(_load_arr_config().get("download_timeout", 7200))
        except Exception:
            return 7200

    # ── public ───────────────────────────────────────────

    def download(self, item: dict) -> bool:
        """Dispatch a single missing item (serie or movie) to VibraVid's pipeline."""
        content_type = item.get("content_type")
        if content_type == "serie":
            return self._process_serie(item)
        elif content_type == "movie":
            return self._process_movie(item)
        else:
            logger.error(f"Unknown content_type: {content_type}")
            return False

    # ── serie ────────────────────────────────────────────

    def _process_serie(self, serie: dict) -> bool:
        from searchapp.views import _run_download_in_thread

        self.last_error = None

        title = serie["title"]
        series_id = serie.get("id")
        provider = (serie.get("provider") or "").strip()
        any_success = False

        tmdb_id = self._resolve_request_tmdb_id(serie, "tv")
        if self._strict_tmdb_matching_enabled() and not tmdb_id:
            logger.error(f"[_process_serie] '{title}' has no resolvable TMDB id; refusing title/year matching")
            self.last_error = self.last_error or "tmdb_id_missing"
            return False

        titles = self._resolve_sonarr_title(title, series_id, tmdb_id) or [title]
        logger.info(f"[_process_serie] Title='{title}', Title candidates={titles}, TMDB ID='{tmdb_id}'")

        year = serie.get("year")
        year_range = self._build_year_range(year, media_type="tv")

        for season in serie.get("seasons", []):
            season_num = season["number"]
            for episode in season.get("episodes", []):
                ep_num = episode["episodeNumber"]
                ep_id = episode.get("id")

                if not ep_id:
                    logger.warning(f"S{season_num}E{ep_num} of '{title}' has no episode ID, skipping")
                    continue

                if self.sonarr.is_episode_in_queue(ep_id):
                    logger.info(f"S{season_num}E{ep_num} of '{title}' already in Sonarr queue, skipping")
                    continue

                if not self._is_sonarr_episode_still_monitored(series_id, ep_id):
                    logger.info(f"S{season_num}E{ep_num} of '{title}' is now unmonitored in Sonarr, skipping")
                    self.last_error = "sonarr_unmonitored"
                    continue

                display_title = f"{titles[0]} - S{season_num} E{ep_num}"
                logger.info(f"⏳ Downloading '{display_title}' via {provider or 'configured fallback order'}")

                item_payload, provider, matched_title = self._search_with_fallback_titles(
                    titles,
                    provider,
                    year_range=year_range,
                    expected_year=year,
                    tmdb_id=tmdb_id,
                    media_type="tv",
                    season_number=season_num,
                    episode_number=ep_num,
                    imdb_id=serie.get("imdbId") or serie.get("imdb_id"),
                    tvdb_id=serie.get("tvdbId") or serie.get("tvdb_id"),
                )
                if not item_payload:
                    logger.error(f"✖️ Could not find '{title}' using candidates: {titles}")
                    self.last_error = "search_no_results"
                    continue

                # Anime seasons split across several provider entries ("Part 1",
                # "Part 2", ...) need the absolute Sonarr episode number translated
                # into that entry's own local numbering (see _disambiguate_split_cour).
                provider_episode = item_payload.pop("_provider_local_episode", None)
                download_episode = provider_episode if provider_episode is not None else ep_num
                if provider_episode is not None:
                    logger.info(f"[S{season_num}E{ep_num}] split-cour: downloading as local episode {provider_episode}")

                # Use Sonarr's path for the series, fallback to OUTPUT config root
                series_root = serie.get("path", "")
                if not series_root:
                    series_root = self._fallback_series_root(title)

                # Target folder: series root + season subfolder, named exactly as Sonarr
                # expects it (e.g. "Season 1"). A hardcoded "S01" did not match Sonarr's
                # seasonFolderFormat, so it left episodes in that folder instead of its own.
                season_folder = self._sonarr_season_folder(serie, season_num)
                if season_folder:
                    target_folder = str(pathlib.Path(series_root).joinpath(season_folder))
                else:
                    target_folder = str(series_root)
                logger.info(f"[S{season_num}E{ep_num}] Target folder (Sonarr's path): '{target_folder}'")

                download_folder = self._translate_path(target_folder, reverse=True)
                if download_folder != target_folder:
                    logger.info(f"[S{season_num}E{ep_num}] Download folder (VibraVid path): '{download_folder}'")

                # Download directly to VibraVid's equivalent path
                future = _run_download_in_thread(
                    site=provider,
                    item_payload=item_payload,
                    season=str(season_num),
                    episodes=str(download_episode),
                    media_type="Serie",
                    output_path=download_folder,
                )
                any_success = True

                try:
                    future.result(timeout=self.download_timeout)  # wait for download to actually finish
                    time.sleep(2)

                    # Get series root path for rescan
                    series_root = serie.get("path", "")
                    if not series_root:
                        series_root = self._fallback_series_root(title)
                    logger.info(f"[S{season_num}E{ep_num}] Using series root path: '{series_root}'")

                    # The download can run for hours; the series may have been removed meanwhile.
                    # RescanSeries on a stale id makes Sonarr raise ModelNotFoundException.
                    if not self.sonarr.series_exists(serie["id"]):
                        logger.error(f"Series '{title}' (id {serie['id']}) no longer exists in Sonarr, skipping import")
                        self.last_error = "series_removed"
                        any_success = False
                        continue

                    # A plain rescan is enough whenever the file name parses (i.e. carries SxxExx).
                    try:
                        self.sonarr.command_rescan_series(serie["id"])
                        logger.info(f"Rescan issued for S{season_num}E{ep_num}")
                    except Exception as scan_exc:
                        logger.warning(f"Rescan failed: {scan_exc}")

                    imported = self._wait_episode_has_file(ep_id, attempts=6)

                    # VibraVid names its output after the scraped title, which frequently has no
                    # SxxExx marker, so Sonarr's parser rejects it. Import explicitly by
                    # episodeId, which skips parsing entirely. Note: DownloadedEpisodesScan is
                    # useless here — Sonarr refuses folders mapped to an existing series.
                    if not imported:
                        result_name = item_payload.get("name", matched_title)
                        result_year = item_payload.get("year", year)
                        fallback_folder = self._get_vibrativo_serie_output(
                            series_root, result_name, season_num, result_year
                        )
                        scan_folders = [self._translate_path(target_folder)]
                        if fallback_folder and fallback_folder != target_folder:
                            scan_folders.append(self._translate_path(fallback_folder))
                        logger.warning(f"S{season_num}E{ep_num} not imported by rescan, falling back to manual import from {scan_folders}")
                        imported = self._confirm_episode_import(serie["id"], ep_id, scan_folders=scan_folders)

                    if not imported:
                        logger.error(f"S{season_num}E{ep_num} import not confirmed in Sonarr")
                        self.last_error = "import_not_confirmed"
                        any_success = False
                        continue

                    try:
                        self.sonarr.command_rename_series(serie["id"])
                        logger.info(f"Rename command issued for S{season_num}E{ep_num} of '{title}'")
                    except Exception as rename_exc:
                        logger.warning(f"Sonarr rename failed for '{title}': {rename_exc}")
                    self._warn_if_sonarr_rename_disabled()

                    logger.info(f"S{season_num}E{ep_num} of '{title}' completed and imported")
                except Exception as exc:
                    logger.error(f"S{season_num}E{ep_num} of '{title}' failed: {exc}")
                    self.last_error = str(exc)
                    # Don't unmonitor on failure -> stays in Sonarr's wanted list for retry
                    any_success = False

        return any_success

    # ── movie ────────────────────────────────────────────

    def _process_movie(self, movie: dict) -> bool:
        from searchapp.views import _run_download_in_thread

        self.last_error = None

        title = movie["title"]
        movie_id = movie["id"]
        tmdb_id = self._resolve_request_tmdb_id(movie, "movie")
        if self._strict_tmdb_matching_enabled() and not tmdb_id:
            logger.error(f"[_process_movie] '{title}' has no resolvable TMDB id; refusing title/year matching")
            self.last_error = self.last_error or "tmdb_id_missing"
            return False

        provider = (movie.get("provider") or "").strip()

        if self.radarr.is_movie_in_queue(movie_id):
            logger.info(f"'{title}' already in Radarr queue, skipping")
            return False

        if not self._is_radarr_movie_still_monitored(movie_id):
            logger.info(f"'{title}' is now unmonitored in Radarr, skipping")
            self.last_error = "radarr_unmonitored"
            return False

        titles = self._resolve_radarr_title(title, movie_id, tmdb_id) or [title]

        year = movie.get("year")
        year_range = self._build_year_range(year)

        logger.info(f"⏳ Downloading movie '{titles[0]}' ({year}) via {provider or 'configured fallback order'}; candidates={titles}")

        item_payload, provider, matched_title = self._search_with_fallback_titles(
            titles,
            provider,
            year_range=year_range,
            expected_year=year,
            tmdb_id=tmdb_id,
            media_type="movie",
            imdb_id=movie.get("imdbId") or movie.get("imdb_id"),
        )
        if not item_payload:
            logger.error(f"Could not find movie '{title}' using candidates: {titles}")
            self.last_error = "search_no_results"
            return False

        # Use Radarr's path for the movie, fallback to OUTPUT config root
        target_folder = movie.get("path", "")
        if not target_folder:
            target_folder = self._fallback_movie_root(title)
        logger.info(f"[_process_movie] Target folder (Radarr's path): '{target_folder}'")

        download_folder = self._translate_path(target_folder, reverse=True)
        if download_folder != target_folder:
            logger.info(f"[_process_movie] Download folder (VibraVid path): '{download_folder}'")

        future = _run_download_in_thread(
            site=provider,
            item_payload=item_payload,
            season=None,
            episodes=None,
            media_type="Film",
            output_path=download_folder,
        )

        try:
            future.result(timeout=self.download_timeout)  # wait for download to actually finish
            time.sleep(2)

            result_name = item_payload.get("name", matched_title)
            result_year = item_payload.get("year", year)
            fallback_folder = self._get_vibrativo_movie_output(target_folder, result_name, result_year)

            # The download can run for hours; the movie may have been removed meanwhile.
            # RescanMovie on a stale id makes Radarr raise ModelNotFoundException.
            if not self.radarr.movie_exists(movie_id):
                logger.error(f"Movie '{title}' (id {movie_id}) no longer exists in Radarr, skipping import")
                self.last_error = "movie_removed"
                return False

            # A plain rescan is enough whenever the file name parses (i.e. carries a year).
            try:
                self.radarr.command_rescan_movie(movie_id)
                logger.info(f"Rescan issued for '{title}'")
            except Exception as scan_exc:
                logger.warning(f"Rescan failed: {scan_exc}")

            imported = self._wait_movie_has_file(movie_id, attempts=6)

            # VibraVid names its output after the scraped title, often without a year
            # ("Parasite.mkv"), which Radarr's parser rejects as [Permanent] Unable to parse
            # file. Import those explicitly by movieId, which skips parsing entirely.
            # Note: DownloadedMoviesScan is useless here — Radarr always answers
            # "Unable to process folder that is mapped to an existing movie".
            if not imported:
                scan_folders = [self._translate_path(target_folder)]
                if fallback_folder and fallback_folder != target_folder:
                    scan_folders.append(self._translate_path(fallback_folder))
                logger.warning(f"Movie '{title}' not imported by rescan, falling back to manual import from {scan_folders}")
                imported = self._confirm_movie_import(movie_id, scan_folders=scan_folders)

            if not imported:
                logger.error(f"Movie '{title}' import not confirmed in Radarr")
                self.last_error = "import_not_confirmed"
                return False

            try:
                self.radarr.command_rename_movie(movie_id)
                logger.info(f"Rename command issued for '{title}'")
            except Exception as rename_exc:
                logger.warning(f"Radarr rename failed for '{title}': {rename_exc}")
            self._warn_if_radarr_rename_disabled()

            logger.info(f"'{title}' completed and imported")
            return True
        except Exception as exc:
            logger.error(f"'{title}' failed: {exc}")
            self.last_error = str(exc)
            # Don't unmonitor on failure -> stays in Radarr's wanted list for retry
            return False

    # ── helpers ──────────────────────────────────────────

    def _is_radarr_movie_still_monitored(self, movie_id: int | None) -> bool:
        """Read Radarr live state before starting a movie download."""
        if not self.radarr or not movie_id:
            return True
        try:
            movie = self.radarr.get_movie_by_id(movie_id)
            return movie.get("monitored", True) is not False
        except Exception as exc:
            logger.warning(f"Could not verify Radarr monitored state before download: {exc}")
            return True

    def _is_sonarr_episode_still_monitored(self, series_id: int | None, episode_id: int | None) -> bool:
        """Read Sonarr live state before starting an episode download."""
        if not self.sonarr:
            return True
        try:
            if series_id:
                series = self.sonarr.get_series_by_id(series_id)
                if series.get("monitored", True) is False:
                    return False
            if episode_id:
                episode = self.sonarr.get_episode(episode_id)
                if episode.get("monitored", True) is False:
                    return False
        except Exception as exc:
            logger.warning(f"Could not verify Sonarr monitored state before download: {exc}")
        return True

    @staticmethod
    def _translate_path(path: str, reverse: bool = False) -> str:
        """Translate paths between VibraVid and Radarr/Sonarr Docker containers.

        Reads path_mapping from ARR config. Each entry maps a host prefix to a container prefix.
        Example: {"/media/Media/Film": "/media/Film"}
        """
        if not path:
            return path
        conf_path = pathlib.Path(__file__).parent.parent.parent.parent / "Conf" / "config.json"
        try:
            with open(conf_path, encoding="utf-8") as _f:
                mapping = json.load(_f).get("ARR", {}).get("path_mapping", {})
                if not isinstance(mapping, dict):
                    return path
        except Exception:
            return path

        sort_index = 1 if reverse else 0
        for host_prefix, container_prefix in sorted(mapping.items(), key=lambda item: len(item[sort_index]), reverse=True):
            source_prefix, target_prefix = ((container_prefix, host_prefix) if reverse else (host_prefix, container_prefix))
            source_prefix = source_prefix.rstrip("/\\")
            target_prefix = target_prefix.rstrip("/\\")
            if path == source_prefix or path.startswith(source_prefix + "/") or path.startswith(source_prefix + "\\"):
                translated = target_prefix + path[len(source_prefix) :]
                logger.info(f"[path_map] '{path}' -> '{translated}'")
                return translated
        if reverse:
            logger.info(f"[path_map] No reverse mapping matched '{path}', leaving it unchanged")
        return path

    @staticmethod
    def _tmdb_client():
        try:
            from VibraVid.provider.tmdb import tmdb_client

            return tmdb_client
        except Exception as exc:
            logger.debug(f"TMDB client unavailable: {exc}")
            return None

    @classmethod
    def _strict_tmdb_matching_enabled(cls) -> bool:
        # If the operator explicitly configured strict matching through the
        # environment, an unrelated TMDB client import failure must not reopen
        # the legacy title/year path.
        if str(os.environ.get("TMDB_API_KEY") or "").strip():
            return True
        tmdb = cls._tmdb_client()
        return bool(tmdb and tmdb.api_key)

    @staticmethod
    def _normalize_tmdb_id(value: Any) -> str | None:
        """Normalize numeric TMDB ids so int/string payloads compare identically."""
        if value in (None, "") or isinstance(value, bool):
            return None
        try:
            normalized = int(str(value).strip())
        except (TypeError, ValueError):
            return None
        return str(normalized) if normalized > 0 else None

    def _resolve_request_tmdb_id(self, item: dict, media_type: str) -> int | None:
        """Resolve the ARR target id from TMDB or an authoritative external id."""
        direct = self._normalize_tmdb_id(item.get("tmdbId") or item.get("tmdb_id"))
        tmdb = self._tmdb_client()
        if direct and (media_type != "tv" or not tmdb or not tmdb.api_key):
            item["tmdbId"] = int(direct)
            return int(direct)
        if not direct and (not tmdb or not tmdb.api_key):
            return None

        arr_details: dict[str, Any] = {}
        item_tvdb_id = item.get("tvdbId") or item.get("tvdb_id")
        needs_arr_details = not direct or (media_type == "tv" and not item_tvdb_id)
        if needs_arr_details:
            try:
                if media_type == "tv" and self.sonarr and item.get("id"):
                    arr_details = self.sonarr.get_series_by_id(item["id"]) or {}
                elif media_type == "movie" and self.radarr and item.get("id"):
                    arr_details = self.radarr.get_movie_by_id(item["id"]) or {}
            except Exception as exc:
                logger.debug(f"Could not load ARR external ids for '{item.get('title')}': {exc}")

        direct = direct or self._normalize_tmdb_id(arr_details.get("tmdbId"))
        if direct:
            item["tmdbId"] = int(direct)

            # Sonarr may expose both namespaces. When TMDB confirms that its
            # TVDB id points to a different TV entry, the record is internally
            # inconsistent and must not be downloaded under either identity.
            if media_type == "tv":
                tvdb_id = item_tvdb_id or arr_details.get("tvdbId")
                if tvdb_id:
                    try:
                        resolved_from_tvdb = tmdb.get_tmdb_id_by_external_id(tvdb_id, "tvdb_id", "tv")
                    except Exception as exc:
                        logger.warning(
                            f"Could not validate tvdb_id={tvdb_id} for '{item.get('title')}': {exc}"
                        )
                    else:
                        normalized_tvdb_tmdb = self._normalize_tmdb_id(resolved_from_tvdb)
                        if normalized_tvdb_tmdb and normalized_tvdb_tmdb != direct:
                            logger.error(
                                f"[tmdb_request] Conflicting Sonarr identity for '{item.get('title')}': "
                                f"tmdbId={direct}, but tvdbId={tvdb_id} maps to TMDB tv/{normalized_tvdb_tmdb}"
                            )
                            self.last_error = "external_id_conflict"
                            return None

            return int(direct)

        external_candidates = []
        if media_type == "tv":
            external_candidates.append(
                (item.get("tvdbId") or item.get("tvdb_id") or arr_details.get("tvdbId"), "tvdb_id")
            )
        external_candidates.append(
            (item.get("imdbId") or item.get("imdb_id") or arr_details.get("imdbId"), "imdb_id")
        )

        for external_id, external_source in external_candidates:
            if not external_id:
                continue
            try:
                resolved = tmdb.get_tmdb_id_by_external_id(external_id, external_source, media_type)
            except Exception as exc:
                logger.warning(
                    f"Could not resolve {external_source}={external_id} for '{item.get('title')}': {exc}"
                )
                continue
            normalized = self._normalize_tmdb_id(resolved)
            if normalized:
                item["tmdbId"] = int(normalized)
                logger.info(
                    f"[tmdb_request] Resolved {external_source}={external_id} to {media_type} TMDB id={normalized}"
                )
                return int(normalized)

        return None

    @staticmethod
    def _result_media_type(result: Any) -> str | None:
        raw_type = str(getattr(result, "type", "") or "").strip().lower()
        if raw_type in {"film", "movie"}:
            return "movie"
        if raw_type in {"tv", "serie", "series", "show", "tv show", "ona", "ova", "tv short", "special"}:
            return "tv"
        return None

    def _resolve_provider_tmdb_id(self, api: Any, result: Any, provider: str, media_type: str) -> str | None:
        """Return only an id attested by the provider result/detail, never title inference."""
        stable_identifiers = (
            str(getattr(result, "id", "") or ""),
            str(getattr(result, "url", "") or ""),
            str(getattr(result, "path_id", "") or ""),
            str(getattr(result, "slug", "") or ""),
        )
        provider_language = str(getattr(result, "provider_language", "") or "")
        cache_key = (
            (provider, media_type, *stable_identifiers, provider_language)
            if any(stable_identifiers)
            else None
        )

        resolved = self._normalize_tmdb_id(getattr(result, "tmdb_id", None))
        if not resolved:
            raw_data = getattr(result, "raw_data", None)
            if isinstance(raw_data, dict):
                resolved = self._normalize_tmdb_id(raw_data.get("tmdb_id") or raw_data.get("tmdbId"))

        # A direct id on the current search result is stronger than any cached
        # detail response and prevents stale provider records from overriding it.
        if not resolved and cache_key is not None:
            resolved = self._provider_tmdb_cache.get(cache_key)

        if not resolved:
            resolver = getattr(api, "resolve_tmdb_id", None)
            if callable(resolver):
                try:
                    resolved = self._normalize_tmdb_id(resolver(result))
                except Exception as exc:
                    logger.warning(
                        f"[tmdb_check] Could not read provider TMDB id for '{getattr(result, 'name', '')}' "
                        f"on '{provider}': {exc}"
                    )

        if resolved:
            # Cache only verified identities. A temporary provider-detail
            # failure must remain retryable on the next ARR item. Anonymous
            # results are never cached because they have no collision-safe key.
            if cache_key is not None:
                self._provider_tmdb_cache[cache_key] = resolved
            result.tmdb_id = resolved
            raw_data = getattr(result, "raw_data", None)
            if isinstance(raw_data, dict):
                raw_data["tmdb_id"] = resolved
        return resolved

    def _resolve_provider_tmdb_id_via_request_external_id(
        self, result: Any, media_type: str, imdb_id: str | None = None, tvdb_id: str | None = None
    ) -> str | None:
        """Last-resort fallback: resolve TMDB id from the ARR request's own imdb/tvdb id.

        Used only when the provider itself could not attest an id.
        """
        tmdb = self._tmdb_client()
        if not tmdb or not tmdb.api_key:
            return None

        for external_id, external_source in ((tvdb_id, "tvdb_id"), (imdb_id, "imdb_id")):
            if not external_id:
                continue
            if external_source == "tvdb_id" and media_type != "tv":
                continue
            try:
                resolved = tmdb.get_tmdb_id_by_external_id(external_id, external_source, media_type)
            except Exception as exc:
                logger.warning(f"[tmdb_check] Could not resolve {external_source}={external_id} as fallback: {exc}")
                continue
            normalized = self._normalize_tmdb_id(resolved)
            if normalized:
                logger.info(
                    f"[tmdb_check] Resolved {external_source}={external_id} to {media_type} TMDB id={normalized} "
                    f"(ARR-request fallback, provider itself could not attest an id)"
                )
                result.tmdb_id = normalized
                return normalized
        return None

    @staticmethod
    def _strip_accents(text: str) -> str:
        """Replace accented characters with their ASCII base: à->a, è->e, ì->i, ò->o, ù->u, etc."""
        import unicodedata
        return "".join(c for c in unicodedata.normalize("NFKD", text) if unicodedata.category(c) != "Mn")

    @staticmethod
    def _titles_are_compatible(title: str, result_name: str) -> bool:
        """Check that result_name shares enough significant words with title."""
        import re

        def sig_words(s: str):
            s = ArrDownloaderService._strip_accents(s)
            return {w.lower() for w in re.split(r"\W+", s) if len(w) > 3}

        sw = sig_words(title)
        if not sw:
            return False
        rw = sig_words(result_name)
        overlap = sw & rw
        ratio = len(overlap) / len(sw)
        if len(sw) <= 2:
            return ratio == 1.0
        return ratio >= 0.5

    @staticmethod
    def _normalize_title(title: str) -> str:
        import re

        title = ArrDownloaderService._strip_accents(title or "").lower()
        title = re.sub(r"[^\w\s]", " ", title)
        title = re.sub(r"\s+", " ", title).strip()
        return title

    @staticmethod
    def _verify_title_match(
        result_name: str, expected_title: str, result_year: int | None = None, expected_year: int | None = None
    ) -> bool:
        """Verify a search result matches the expected title/year from ARR metadata.

        Uses normalized string comparison (lowercase, accents removed, punctuation stripped).
        """
        if not result_name or not expected_title:
            return False

        import re
        import unicodedata

        def normalize(s: str) -> str:
            """Normalize: lowercase, remove accents, remove punctuation, collapse spaces."""
            s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode("ascii")
            s = re.sub(r"[^\w\s]", " ", s.lower())
            s = re.sub(r"\s+", " ", s).strip()
            return s

        rn = normalize(result_name)
        et = normalize(expected_title)

        # Exact match or one contains the other (after normalization)
        if rn == et or et in rn or rn in et:
            # Year check with +/- 1 year tolerance
            if expected_year is not None and result_year is not None:
                try:
                    return abs(int(result_year) - int(expected_year)) <= 1
                except (ValueError, TypeError):
                    pass
            return True

        return False

    def _search_with_fallback(
        self,
        title: str,
        primary_provider: str,
        **kwargs,
    ) -> tuple[dict[str, Any] | None, str]:
        """Try primary_provider first, then the fallback list from ARR config.

        Returns (payload, used_provider). payload is None if nothing found anywhere.
        """
        providers = self._provider_order(primary_provider)
        logger.info(f"[fallback] Search '{title}' — provider order: {providers}")
        for index, provider in enumerate(providers):
            label = "primary" if index == 0 else "fallback"
            logger.info(f"[fallback] Trying '{provider}' for '{title}'")
            payload = self._search_and_build_payload(title, provider, **kwargs)
            if payload:
                logger.info(f"[fallback] Found on {label} '{provider}': name='{payload.get('name')}' year={payload.get('year')}")
                logger.debug(f"[fallback] Payload dump: {json.dumps(payload, default=str, ensure_ascii=False)}")
                return payload, provider
            logger.warning(f"[fallback] '{title}' not found on '{provider}' either")

        logger.error(f"[fallback] '{title}' not found on any provider (tried: {providers})")
        return None, primary_provider or (providers[0] if providers else "")

    def _provider_order(self, primary_provider: str) -> list[str]:
        """Return primary provider followed by configured ARR provider fallbacks."""
        conf_path = pathlib.Path(__file__).parent.parent.parent.parent / "Conf" / "config.json"
        try:
            with open(conf_path, encoding="utf-8") as _f:
                fallback_list: list = json.load(_f).get("ARR", {}).get("provider_fallback", [])
        except Exception as _exc:
            logger.warning(f"[fallback] Could not read provider_fallback from config: {_exc}")
            fallback_list = []

        providers = []
        if primary_provider:
            providers.append(primary_provider)
        providers.extend(provider for provider in fallback_list if provider and provider not in providers)
        if not providers:
            providers = ["streamingcommunity"]
        return providers

    def _search_with_fallback_titles(
        self,
        titles: list[str],
        primary_provider: str,
        **kwargs,
    ) -> tuple[dict[str, Any] | None, str, str]:
        """Try title candidates inside each provider, preserving provider order.

        Returns the accepted payload, provider, and title that produced the
        match. If every candidate fails, payload is None and the returned title
        is the first candidate for logging/path fallback purposes.
        """
        candidates = self._unique_titles(titles)
        providers = self._provider_order(primary_provider)
        logger.info(f"[title_fallback] Provider order: {providers}; title candidates: {candidates}")

        for provider_index, provider in enumerate(providers):
            label = "primary" if provider_index == 0 else "fallback"
            for title in candidates:
                logger.info(f"[title_fallback] Trying {label} '{provider}' with title '{title}'")
                payload = self._search_and_build_payload(
                    title,
                    provider,
                    expected_title=title,
                    **kwargs,
                )
                if payload:
                    logger.info(f"[title_fallback] Accepted title '{title}' on {label} '{provider}'")
                    return payload, provider, title

                logger.warning(f"[title_fallback] No match for title '{title}' on '{provider}'")

        fallback_provider = primary_provider or (providers[0] if providers else "")
        return None, fallback_provider, candidates[0] if candidates else ""

    def _disambiguate_split_cour(
        self, provider: str, candidates: list, season_int: int, episode_number: int | None
    ) -> tuple[list, int | None]:
        """Narrow `candidates` to the one provider entry that actually contains
        `episode_number` of TMDB season `season_int`, for anime seasons split
        across several provider entries ("Part 1", "Part 2", ...) that all share
        the same TMDB season number."""
        if provider != "animeunity" or episode_number is None or len(candidates) <= 1:
            return candidates, None

        lookup_candidates = [
            {
                "mal_id": (r.raw_data or {}).get("mal_id") if isinstance(r.raw_data, dict) else None,
                "anilist_id": (r.raw_data or {}).get("anilist_id") if isinstance(r.raw_data, dict) else None,
                "episodes_count": (r.raw_data or {}).get("episodes_count") if isinstance(r.raw_data, dict) else None,
            }
            for r in candidates
        ]
        resolved = anime_id_map.resolve_split_cour_episode(lookup_candidates, season_int, episode_number)
        if not resolved:
            return candidates, None

        matched = candidates[resolved["candidate_index"]]
        return [matched], resolved["local_episode"]

    def _search_and_build_payload(
        self,
        title: str,
        provider: str,
        year_range: str | None = None,
        expected_title: str | None = None,
        expected_year: int | None = None,
        tmdb_id: int | None = None,
        media_type: str = "tv",
        season_number: int | None = None,
        episode_number: int | None = None,
        imdb_id: str | None = None,
        tvdb_id: str | None = None,
    ) -> dict[str, Any] | None:
        """Search VibraVid's streaming API for a title and return an item_payload dict.

        With a TMDB key, verifies only the canonical media type + TMDB id
        identity. Title/year checks remain limited to keyless legacy mode.
        Localized title fallback is handled before this method.
        """
        try:
            from searchapp.api import get_api

            api = get_api(provider)

            # Strip accents from search query: à->a, è->e, ì->i, ò->o, ù->u ...
            search_query = self._strip_accents(title).strip()
            if search_query != title:
                logger.info(f"[search] Stripped accents: '{title}' -> '{search_query}'")

            logger.info(f"[search] provider='{provider}' query='{search_query}' expected_tmdb={tmdb_id} year_range={year_range}")

            # Search using the normalized title, bounded by a global timeout so a
            # hung/slow provider cannot stall the single-threaded polling loop.
            # shutdown(wait=False) is essential: the context-manager form would block
            # on a hung search at exit, defeating the timeout.
            ex = concurrent.futures.ThreadPoolExecutor(max_workers=1)
            try:
                results = ex.submit(api.search, search_query).result(timeout=_ARR_SEARCH_TIMEOUT)
            except concurrent.futures.TimeoutError:
                logger.warning(f"[search] '{provider}' timed out after {_ARR_SEARCH_TIMEOUT}s for '{search_query}', moving on")
                return None
            finally:
                ex.shutdown(wait=False, cancel_futures=True)

            if not results:
                logger.warning(f"[search] No results for '{search_query}' on '{provider}'")
                return None

            logger.info(f"[search] {len(results)} result(s) from '{provider}' for '{search_query}':")
            for i, r in enumerate(results[:5]):
                r_tmdb = getattr(r, "tmdb_id", None) or "N/A"
                logger.info(f"[search]   [{i}] '{r.name}' ({r.year}) type={r.type} tmdb_id={r_tmdb}")

            # Parse year range into integers
            year_start = None
            year_end = None
            if year_range:
                try:
                    parts = year_range.split("-")
                    year_start = int(parts[0])
                    year_end = int(parts[1])
                except (ValueError, IndexError):
                    logger.debug(f"[search] Could not parse year_range '{year_range}'")

            expected_tmdb_str = self._normalize_tmdb_id(tmdb_id)
            strict_tmdb = self._strict_tmdb_matching_enabled()

            _conf_path = pathlib.Path(__file__).parent.parent.parent.parent / "Conf" / "config.json"
            try:
                with open(_conf_path, encoding="utf-8") as _f:
                    _prefer_ita = json.load(_f).get("ARR", {}).get("download_italian_anime_default", True)
            except Exception:
                _prefer_ita = True

            best = None
            split_cour_local_episode = None
            if strict_tmdb:
                if not expected_tmdb_str:
                    logger.error(
                        f"[tmdb_check] No target TMDB id for '{expected_title or title}'; "
                        "strict matching refuses title/year fallback"
                    )
                    return None

                strict_candidates = list(results)
                season_int = 0
                if media_type == "tv" and provider in {"animeunity", "animeworld"} and season_number:
                    try:
                        season_int = int(season_number)
                    except (TypeError, ValueError):
                        season_int = 0
                    if season_int > 1:
                        expected_season_title = self._normalize_title(f"{title} {season_int}")
                        strict_candidates = [
                            r
                            for r in strict_candidates
                            if self._normalize_title(r.name or "") == expected_season_title
                            or self._normalize_title(r.name or "").startswith(expected_season_title + " ")
                        ]
                        if not strict_candidates:
                            logger.warning(
                                f"[search] No season-specific anime result for S{season_int} on '{provider}', "
                                "rejecting generic results"
                            )
                            return None

                # Preference only changes the order. Every candidate, including
                # an ITA variant, still has to pass the exact identity check. This
                # must run before split-cour disambiguation below: an ITA and a
                # non-ITA entry for the same "Part" share the same mal_id, so
                # whichever comes first here is the one the crosswalk will pick.
                if _prefer_ita:
                    strict_candidates.sort(key=lambda r: "(ITA)" not in (r.name or "").upper())

                if season_int > 1:
                    strict_candidates, split_cour_local_episode = self._disambiguate_split_cour(
                        provider, strict_candidates, season_int, episode_number
                    )
                    if split_cour_local_episode is not None:
                        logger.info(f"[split_cour] absolute episode {episode_number} of S{season_int} -> '{strict_candidates[0].name}' local episode {split_cour_local_episode}")

                for r in strict_candidates:
                    r_name = r.name or ""
                    r_year = r.year or ""
                    result_media_type = self._result_media_type(r)
                    if result_media_type != media_type:
                        logger.warning(
                            f"[type_check] SKIP '{r_name}' ({r_year}) — provider type={result_media_type or 'unknown'} "
                            f"cannot satisfy TMDB type={media_type}"
                        )
                        continue

                    result_tmdb = self._resolve_provider_tmdb_id(api, r, provider, media_type)
                    if not result_tmdb:
                        result_tmdb = self._resolve_provider_tmdb_id_via_request_external_id(
                            r, media_type, imdb_id=imdb_id, tvdb_id=tvdb_id
                        )
                    if not result_tmdb:
                        logger.warning(
                            f"[tmdb_check] SKIP '{r_name}' ({r_year}) — provider cannot attest a TMDB id"
                        )
                        continue
                    if result_tmdb != expected_tmdb_str:
                        logger.warning(
                            f"[tmdb_check] SKIP '{r_name}' ({r_year}) — "
                            f"{media_type} tmdb_id mismatch: got={result_tmdb} expected={expected_tmdb_str}"
                        )
                        continue

                    best = r
                    logger.info(
                        f"[tmdb_check] MATCH '{r_name}' ({r_year}) — "
                        f"identity=({media_type}, {result_tmdb})"
                    )
                    break
            else:
                # Legacy matching is intentionally retained only when no TMDB
                # key is configured.
                if media_type == "tv" and provider in {"animeunity", "animeworld"} and season_number:
                    try:
                        season_int = int(season_number)
                    except (TypeError, ValueError):
                        season_int = 0
                    if season_int > 1:
                        expected_season_title = self._normalize_title(f"{title} {season_int}")
                        best = next(
                            (
                                r
                                for r in results
                                if self._normalize_title(r.name or "") == expected_season_title
                                or self._normalize_title(r.name or "").startswith(expected_season_title + " ")
                            ),
                            None,
                        )
                        if best:
                            logger.info(f"[search] ACCEPT '{best.name}' — season-specific anime match for S{season_int}")
                        else:
                            logger.warning(
                                f"[search] No season-specific anime result for S{season_int} on '{provider}', "
                                "rejecting generic results"
                            )
                            return None

                for r in results:
                    if best is not None:
                        break
                    r_name = r.name or ""
                    r_year = r.year or ""
                    r_tmdb = self._normalize_tmdb_id(getattr(r, "tmdb_id", None))
                    r_type = str(getattr(r, "type", "") or "").lower()

                    if media_type == "tv" and r_type == "movie":
                        logger.warning(
                            f"[type_check] SKIP '{r_name}' ({r_year}) — movie result cannot satisfy a Sonarr TV request"
                        )
                        continue

                    if expected_tmdb_str and r_tmdb:
                        if r_tmdb != expected_tmdb_str:
                            logger.warning(
                                f"[tmdb_check] SKIP '{r_name}' ({r_year}) — "
                                f"tmdb_id mismatch: got={r_tmdb} expected={expected_tmdb_str}"
                            )
                            continue
                        best = r
                        logger.info(f"[tmdb_check] MATCH '{r_name}' ({r_year}) — tmdb_id={r_tmdb} OK")
                        break

                    if not self._titles_are_compatible(title, r_name):
                        logger.warning(
                            f"[title_check] SKIP '{r_name}' ({r_year}) — title too different from '{title}'"
                        )
                        continue

                    if year_start is not None and year_end is not None:
                        if not r_year:
                            best = r
                            logger.info(f"[search] ACCEPT '{r_name}' (no year) — title match, year unverifiable")
                            break
                        try:
                            if not (year_start <= int(r_year) <= year_end):
                                logger.debug(
                                    f"[search] SKIP '{r_name}' ({r_year}) — "
                                    f"year out of range [{year_start}-{year_end}]"
                                )
                                continue
                        except (ValueError, TypeError):
                            continue

                    best = r
                    logger.info(f"[search] ACCEPT '{r_name}' ({r_year}) — title+year match (legacy mode)")
                    break

                # Last-chance fallback is likewise legacy-only.
                if best is None and results:
                    first = results[0]
                    f_tmdb = self._normalize_tmdb_id(getattr(first, "tmdb_id", None))
                    if expected_tmdb_str and f_tmdb and f_tmdb != expected_tmdb_str:
                        logger.error(
                            f"[tmdb_check] HARD REJECT '{first.name}' ({first.year}) on '{provider}' — "
                            f"tmdb_id mismatch: got={f_tmdb} expected={expected_tmdb_str}. Trying next provider."
                        )
                        return None
                    if self._titles_are_compatible(title, first.name or ""):
                        f_year = first.year or ""
                        year_ok = True
                        if year_start is not None and year_end is not None and f_year:
                            try:
                                year_ok = year_start <= int(f_year) <= year_end
                            except (ValueError, TypeError):
                                year_ok = False
                        if year_ok:
                            best = first
                            logger.info(
                                f"[search] ACCEPT first result '{first.name}' "
                                f"({first.year or 'no year'}) — title match fallback"
                            )
                        else:
                            logger.warning(
                                f"[search] SKIP first result '{first.name}' ({first.year}) — "
                                f"year out of range [{year_start}-{year_end}]"
                            )
                    else:
                        logger.warning(
                            f"[title_check] SKIP first result '{first.name}' ({first.year}) — "
                            f"title too different from '{title}'"
                        )

            if best is None:
                logger.error(f"[search] No match for '{expected_title or title}' on '{provider}' (year_range={year_range}, expected_tmdb={tmdb_id}). Top result was: '{results[0].name}' ({results[0].year})")
                return None

            # ── ITA preference ────────────────────────────────────────────
            # If download_italian_anime_default=true and the best result is not
            # already an ITA version, look for one among the remaining results.
            season_int_for_ita = 0
            try:
                season_int_for_ita = int(season_number or 0)
            except (TypeError, ValueError):
                season_int_for_ita = 0

            if not strict_tmdb and _prefer_ita and "(ITA)" not in (best.name or "").upper():
                expected_season_title = (
                    self._normalize_title(f"{title} {season_int_for_ita}")
                    if media_type == "tv" and provider in {"animeunity", "animeworld"} and season_int_for_ita > 1
                    else None
                )
                ita = next(
                    (
                        r
                        for r in results
                        if "(ITA)" in (r.name or "").upper()
                        and self._titles_are_compatible(title, r.name or "")
                        and (
                            expected_season_title is None
                            or self._normalize_title((r.name or "").replace("(ITA)", "")) == expected_season_title
                            or self._normalize_title((r.name or "").replace("(ITA)", "")).startswith(
                                expected_season_title + " "
                            )
                        )
                    ),
                    None,
                )
                if ita:
                    logger.info(f"[ita] Preferring ITA version '{ita.name}' over '{best.name}' (download_italian_anime_default=true)")
                    best = ita
                else:
                    logger.info(f"[ita] No ITA version available, keeping '{best.name}'")

            payload = {
                **best.__dict__,
                # Once the strict (media type, TMDB id) identity has been
                # verified, preserve that canonical type in the payload too.
                "is_movie": media_type == "movie" if strict_tmdb else best.is_movie,
            }
            if split_cour_local_episode is not None:
                # `best` here is the provider's split "Part" entry, not the
                # combined TMDB season; the caller must download this local
                # episode number, not the absolute one it requested.
                payload["_provider_local_episode"] = split_cour_local_episode
            
            if strict_tmdb:
                canonical_type = "movie" if media_type == "movie" else "tv"
                payload["type"] = canonical_type
                payload["tmdb_id"] = expected_tmdb_str
                raw_data = payload.get("raw_data")
                if isinstance(raw_data, dict):
                    # Most adapters pass raw_data back into the service-level
                    # dispatcher, so it must carry the same verified identity.
                    raw_data["type"] = canonical_type
                    raw_data["tmdb_id"] = expected_tmdb_str
            
            logger.debug(f"[search] Payload: {json.dumps(payload, default=str, ensure_ascii=False)}")
            return payload

        except Exception as exc:
            logger.error(f"Search failed for '{title}' on {provider}: {exc}")
            return None

    @staticmethod
    def _unique_titles(titles: list[str | None]) -> list[str]:
        """Return non-empty title candidates deduplicated by normalized title."""
        unique: list[str] = []
        seen = set()
        for raw_title in titles:
            candidate = str(raw_title or "").strip()
            if not candidate:
                continue
            key = ArrDownloaderService._normalize_title(candidate)
            if not key or key in seen:
                continue
            seen.add(key)
            unique.append(candidate)
        return unique

    def _get_tmdb_title_candidates(self, tmdb_id: int | None, media_type: str) -> list[str]:
        """Return localized and alternative TMDB titles for search fallback."""
        if not tmdb_id:
            return []

        titles: list[str] = []
        try:
            from VibraVid.provider.tmdb import tmdb_client as tmdb

            endpoint = "movie" if media_type == "movie" else "tv"
            detail_key = "title" if media_type == "movie" else "name"
            for lang in ["it", "en"]:
                try:
                    details = tmdb._make_request(f"{endpoint}/{tmdb_id}", {"language": lang})
                    titles.append(details.get(detail_key, ""))
                except Exception as details_exc:
                    logger.debug(f"Failed to get TMDB {lang} title for {media_type}/{tmdb_id}: {details_exc}")

                try:
                    titles.extend(tmdb.get_alternative_titles(tmdb_id, media_type, lang))
                except Exception as alt_exc:
                    logger.debug(f"Failed to get TMDB {lang} alternative titles for {media_type}/{tmdb_id}: {alt_exc}")
        except Exception as tmdb_exc:
            logger.debug(f"Failed to collect TMDB title candidates for {media_type}/{tmdb_id}: {tmdb_exc}")

        return self._unique_titles(titles)

    def _resolve_sonarr_title(self, title: str, series_id: int | None, tmdb_id: int | None = None) -> list[str]:
        """Build ordered Sonarr title candidates for VibraVid searches."""
        titles: list[str | None] = list(self._get_tmdb_title_candidates(tmdb_id, "tv"))

        if series_id:
            try:
                series = self.sonarr.get_series_by_id(series_id)
                sonarr_title = series.get("title", "")
                sonarr_original = series.get("originalTitle", "")
                logger.info(f"[_resolve_sonarr_title] Sonarr title='{sonarr_title}', originalTitle='{sonarr_original}'")
                titles.extend([sonarr_title, sonarr_original])
            except Exception as exc:
                logger.debug(f"Sonarr series lookup by ID {series_id} failed: {exc}")

        if not self._unique_titles(titles):
            # Secondary lookup: if direct lookup by ID and TMDB lookup are
            # unavailable, scan Sonarr's series list and match by title/slug/originalTitle.
            try:
                series_list = self.sonarr.get_series()
                title_lower = title.lower()
                for s in series_list:
                    s_title = s.get("title", "").lower()
                    s_slug = s.get("titleSlug", "").lower()
                    s_original = s.get("originalTitle", "").lower()
                    if title_lower in (s_title, s_slug, s_original):
                        titles.extend([s.get("title"), s.get("originalTitle")])
                        break
            except Exception as exc:
                logger.debug(f"Sonarr series list fallback failed: {exc}")

        titles.append(title)
        return self._unique_titles(titles)

    def _resolve_radarr_title(self, title: str, movie_id: int, tmdb_id: int | None = None) -> list[str]:
        """Build ordered Radarr title candidates for VibraVid searches."""
        titles: list[str | None] = list(self._get_tmdb_title_candidates(tmdb_id, "movie"))

        try:
            movie = self.radarr.get_movie_by_id(movie_id)
            original = movie.get("originalTitle")
            radarr_title = movie.get("title")
            logger.info(f"[_resolve_radarr_title] Radarr title='{radarr_title}', originalTitle='{original}'")
            titles.extend([radarr_title, original])
        except Exception as exc:
            logger.debug(f"Radarr movie lookup by ID {movie_id} failed: {exc}")

        titles.append(title)
        return self._unique_titles(titles)

    @staticmethod
    def _build_year_range(year, media_type: str = "movie") -> str | None:
        if not year:
            return None
        try:
            y = int(year)
            now = datetime.datetime.now().year
            if media_type == "tv":
                return f"{max(0, y - 1)}-{now + 1}"
            if y >= (now - 1):
                return f"{y}-9999"
            else:
                return f"{y}-{y + 1}"
        except (ValueError, TypeError):
            return None

    def _fallback_series_root(self, title: str) -> str:
        from VibraVid.utils import config_manager

        base = config_manager.config.get("OUTPUT", "root_path")
        folder = config_manager.config.get("OUTPUT", "serie_folder_name")
        return str(pathlib.Path(base).joinpath(folder, title))

    def _fallback_movie_root(self, title: str) -> str:
        from VibraVid.utils import config_manager

        base = config_manager.config.get("OUTPUT", "root_path")
        folder = config_manager.config.get("OUTPUT", "movie_folder_name")
        return str(pathlib.Path(base).joinpath(folder, title))

    def _sonarr_season_folder(self, serie: dict, season_num: int) -> str:
        """Return the season subfolder name exactly as Sonarr lays it out on disk."""
        # Per-series toggle: when off there is no season subfolder at all.
        if serie.get("seasonFolder") is False:
            return ""

        return self._render_season_format(self._get_sonarr_season_format(), season_num)

    def _get_sonarr_season_format(self) -> str:
        """Fetch and cache Sonarr's seasonFolderFormat, defaulting to Sonarr's own default."""
        if self._sonarr_season_format is None:
            fmt = "Season {season:00}"
            try:
                fmt = self.sonarr.get_naming_config().get("seasonFolderFormat") or fmt
            except Exception as exc:
                logger.warning(f"Could not fetch Sonarr naming config, using default season folder format: {exc}")
            self._sonarr_season_format = fmt
        return self._sonarr_season_format or "Season {season:00}"

    def _warn_if_sonarr_rename_disabled(self) -> None:
        """Sonarr's own naming config decides the final on-disk filename after import."""
        if self._sonarr_rename_checked:
            return
        self._sonarr_rename_checked = True
        try:
            if not self.sonarr.get_naming_config().get("renameEpisodes", True):
                logger.warning(
                    "Sonarr has 'Rename Episodes' disabled (Settings -> Media Management): "
                    "imported episodes will keep VibraVid's raw filename instead of Sonarr's "
                    "configured naming format. Enable it in Sonarr to get clean filenames."
                )
        except Exception as exc:
            logger.debug(f"Could not check Sonarr naming config for rename warning: {exc}")

    def _warn_if_radarr_rename_disabled(self) -> None:
        """Same caveat as _warn_if_sonarr_rename_disabled, for Radarr's 'Rename Movies'."""
        if self._radarr_rename_checked:
            return
        self._radarr_rename_checked = True
        try:
            if not self.radarr.get_naming_config().get("renameMovies", True):
                logger.warning(
                    "Radarr has 'Rename Movies' disabled (Settings -> Media Management): "
                    "imported movies will keep VibraVid's raw filename instead of Radarr's "
                    "configured naming format. Enable it in Radarr to get clean filenames."
                )
        except Exception as exc:
            logger.debug(f"Could not check Radarr naming config for rename warning: {exc}")

    @staticmethod
    def _render_season_format(fmt: str, season_num: int) -> str:
        """Render a Sonarr ``seasonFolderFormat`` token into a concrete folder name.

        Handles ``{season}`` and zero-padded ``{season:00}`` style tokens.
        """

        def _sub(match) -> str:
            pad = match.group(1)
            return str(season_num).zfill(len(pad)) if pad else str(season_num)

        rendered = re.sub(r"\{season(?::(0+))?\}", _sub, fmt).strip()
        return rendered or f"Season {season_num:02d}"

    def _get_vibrativo_serie_output(
        self, arr_series_path: str, title: str, season_num: int, year: int | None = None
    ) -> str:
        """Compute the VibraVid output path relative to Sonarr's root folder."""
        if not arr_series_path:
            return ""
        try:
            import pathlib

            from VibraVid.services._base.tv_display_manager import map_episode_path

            # Pass the year as string if available to match VibraVid's exact logic
            series_year = str(year) if year else None
            path_components, _ = map_episode_path(series_name=title, series_year=series_year, season_number=season_num)

            if "\\" in arr_series_path:
                root = pathlib.PureWindowsPath(arr_series_path).parent
            else:
                root = pathlib.PurePosixPath(arr_series_path).parent

            # Append ONLY the series folder (path_components[0]), ignoring the season subfolder
            if path_components:
                root = root / path_components[0]

            return str(root)
        except Exception as exc:
            logger.debug(f"Could not compute VibraVid serie output path: {exc}")
        return ""

    def _get_vibrativo_movie_output(self, arr_movie_path: str, title: str, year: int | None = None) -> str:
        """Compute the VibraVid output path relative to Radarr's root folder."""
        if not arr_movie_path:
            return ""
        try:
            import pathlib

            from VibraVid.services._base.tv_display_manager import map_movie_path

            # Pass the year as string if available
            title_year = str(year) if year else None
            path_components, _ = map_movie_path(title_name=title, title_year=title_year)

            if "\\" in arr_movie_path:
                root = pathlib.PureWindowsPath(arr_movie_path).parent
            else:
                root = pathlib.PurePosixPath(arr_movie_path).parent

            for part in path_components:
                root = root / part.strip()  # strip trailing spaces from year-less format
            return str(root)
        except Exception as exc:
            logger.debug(f"Could not compute VibraVid movie output path: {exc}")
        return ""

    def _confirm_episode_import(
        self, series_id: int, episode_id: int, scan_folders: list | None = None, season_folder: str | None = None
    ) -> bool:
        """Try to import episode files from each candidate folder into Sonarr."""
        # Back-compat: accept the old season_folder kwarg
        if scan_folders is None:
            scan_folders = [season_folder] if season_folder else []

        for folder in scan_folders:
            if not folder:
                continue
            try:
                lookup_items = self.sonarr.manual_import_lookup(folder, series_id=series_id)
                import_payload = []
                for item in lookup_items:
                    path = str(item.get("path", "")).strip()
                    if not path:
                        continue

                    # Sonarr cannot infer the episode from a name without SxxExx: bind it
                    # to the episode we just downloaded.
                    episodes = [ep for ep in item.get("episodes", []) if ep.get("id")]
                    if not episodes:
                        episodes = [{"id": episode_id}]

                    post_item = dict(item)
                    post_item["seriesId"] = series_id
                    post_item["episodes"] = episodes
                    import_payload.append(post_item)

                if import_payload:
                    result = self.sonarr.manual_import(import_payload)
                    logger.info(f"Manual import submitted for {len(import_payload)} file(s) from '{folder}'")
                    if result.get("id"):
                        status = self.sonarr.wait_command(result["id"])
                        logger.info(f"Sonarr ManualImport command finished: {status}")
                    break
            except Exception as exc:
                logger.warning(f"Sonarr manual import from '{folder}' failed: {exc}")

        return self._wait_episode_has_file(episode_id, attempts=24)

    def _confirm_movie_import(
        self, movie_id: int, scan_folders: list | None = None, movie_root: str | None = None
    ) -> bool:
        """Try to import movie files from each candidate folder into Radarr."""
        # Back-compat: accept the old movie_root kwarg
        if scan_folders is None:
            scan_folders = [movie_root] if movie_root else []

        for folder in scan_folders:
            if not folder:
                continue
            try:
                lookup_items = self.radarr.manual_import_lookup(folder, movie_id=movie_id)
                import_payload = []
                for item in lookup_items:
                    path = str(item.get("path", "")).strip()
                    if not path:
                        continue

                    post_item = dict(item)
                    post_item["movieId"] = movie_id
                    import_payload.append(post_item)

                if import_payload:
                    result = self.radarr.manual_import(import_payload)
                    logger.info(f"Manual import submitted for {len(import_payload)} file(s) from '{folder}'")
                    if result.get("id"):
                        status = self.radarr.wait_command(result["id"])
                        logger.info(f"Radarr ManualImport command finished: {status}")
                    break
            except Exception as exc:
                logger.warning(f"Radarr manual import from '{folder}' failed: {exc}")

        return self._wait_movie_has_file(movie_id, attempts=24)

    # ── import polling ───────────────────────────────────

    def _wait_movie_has_file(self, movie_id: int, attempts: int, delay: int = 5) -> bool:
        """Poll Radarr until the movie has an attached file."""
        for attempt in range(attempts):
            try:
                movie = self.radarr.get_movie_by_id(movie_id)
                if movie.get("hasFile") or movie.get("movieFileId"):
                    return True
            except Exception as exc:
                logger.warning(f"Failed to verify Radarr movie import: {exc}")
            if attempt < attempts - 1:
                time.sleep(delay)
        return False

    def _wait_episode_has_file(self, episode_id: int, attempts: int, delay: int = 5) -> bool:
        """Poll Sonarr until the episode has an attached file."""
        for attempt in range(attempts):
            try:
                episode = self.sonarr.get_episode(episode_id)
                if episode.get("hasFile") or episode.get("episodeFileId"):
                    return True
            except Exception as exc:
                logger.warning(f"Failed to verify Sonarr episode import: {exc}")
            if attempt < attempts - 1:
                time.sleep(delay)
        return False
