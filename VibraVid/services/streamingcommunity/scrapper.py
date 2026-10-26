# 01.03.24

import json
import logging
import threading
from concurrent.futures import ThreadPoolExecutor

from bs4 import BeautifulSoup

from VibraVid.services._base.object import Episode, Season, SeasonManager
from VibraVid.utils.http_client import create_client, get_headers, get_with_retry

logger = logging.getLogger(__name__)
_EPISODE_IMAGE_TYPES = ("cover", "cover_mobile", "background", "poster")


class GetSerieInfo:
    def __init__(
        self,
        url,
        media_id: int = None,
        series_name: str = None,
        year: int = None,
        provider_language: str = "it",
        series_display_name: str = None,
        languages: list[str] | None = None,
    ):
        """
        Initialize the GetSerieInfo class for scraping TV series information.

        Args:
            url (str): The URL of the streaming site.
            media_id (int): Unique identifier for the media
            series_name (str): Slug of the TV series
            series_display_name (str): Name of the TV series
            languages (list[str]): Catalog(s) to query for season/episode info (["it"], ["en"], or ["it", "en"]). Defaults to both.
        """
        self.is_series = False
        self.headers = get_headers()
        self.url = url
        self.media_id = media_id
        self.year = year
        self.seasons_manager = SeasonManager()
        self.provider_language = provider_language
        self.languages = languages if languages is not None else ["it", "en"]
        self._loaded_catalogs = set()
        self._collect_lock = threading.Lock()
        if isinstance(self.url, str) and self.url.endswith(("/it", "/en")):
            self.base_url = self.url.rsplit("/", 1)[0]
        else:
            self.base_url = self.url

        if series_name is not None:
            self.is_series = True
            self.series_name = series_name  # slug, used for URL building
            self.series_display_name = series_display_name if series_display_name is not None else series_name

    def _episode_image_url(self, base_url: str, raw_episode: dict) -> str | None:
        """Url of the episode image, if available. Returns None if no image found."""
        images = raw_episode.get("images") or []
        filename = None
        for wanted in _EPISODE_IMAGE_TYPES:
            filename = next(
                (img.get("filename") for img in images if img.get("type") == wanted and img.get("filename")),
                None,
            )
            if filename:
                break

        if not filename and images:
            filename = images[0].get("filename")

        if not filename:
            return None
        return f"{base_url.replace('stream', 'cdn.stream')}/images/{filename}"

    def collect_info_title(self) -> None:
        """
        Retrieve title information and merge seasons from the selected catalogs.

        Raises:
            Exception: If there's an error fetching series information
        """
        languages = list(dict.fromkeys([self.provider_language, *self.languages]))
        seasons_by_number = {season.number: season for season in self.seasons_manager.seasons}
        last_error = None
        loaded = bool(self._loaded_catalogs)

        for lang in languages:
            if lang in self._loaded_catalogs:
                continue
            try:
                with create_client(headers=self.headers) as client:
                    response = get_with_retry(
                        client, f"{self.base_url}/{lang}/titles/{self.media_id}-{self.series_name}", retries=1,
                    )
                response.raise_for_status()
                soup = BeautifulSoup(response.text, "html.parser")
                json_response = json.loads(soup.find("div", {"id": "app"}).get("data-page"))
                title_data = json_response.get("props", {}).get("title", {})
                if not getattr(self, "title_info", None):
                    self.version = json_response["version"]
                    self.title_info = title_data

                for season_data in title_data.get("seasons") or []:
                    number = season_data.get("number")
                    if number is None:
                        continue
                    number = int(number)
                    season = seasons_by_number.get(number)
                    if season is None:
                        season = self.seasons_manager.add(
                            Season(
                                id=season_data.get("id"),
                                number=number,
                                name=f"Season {number}",
                                slug=season_data.get("slug"),
                                catalog_ids={},
                            )
                        )
                        seasons_by_number[number] = season
                    if lang not in season.catalog_ids:
                        # A recovered catalog may have newer episodes than the cached list.
                        season.episodes.clear()
                    season.catalog_ids[lang] = season_data.get("id")
                self._loaded_catalogs.add(lang)
                loaded = True
            except Exception as e:
                last_error = e
                logger.warning("Could not load title %s from %s catalog: %s", self.media_id, lang, e)

        if not loaded:
            logger.error("Error collecting series info: %s", last_error)
            raise last_error or RuntimeError("No catalog returned title information")

    def get_quality(self) -> str:
        """Return the quality label (e.g. 'TS', 'CAM', 'HD') for this title.

        Reuses title_info if already populated by collect_info_title(); otherwise
        fetches the title page on demand (needed for movies).
        """
        if not getattr(self, "title_info", None):
            try:
                self.collect_info_title()
            except Exception:
                return ""

        title_node = getattr(self, "title_info", None) or {}
        quality = str(title_node.get("quality") or "").upper()
        if quality:
            return quality

        for tag in title_node.get("tags", []):
            if isinstance(tag, dict):
                tag_name = str(tag.get("name", "")).upper()
                if tag_name in {"TS", "CAM", "CINEMA"}:
                    return tag_name

        return ""

    def is_cam(self) -> bool:
        """Return True if this title is a TS/CAM/Cinema release."""
        return self.get_quality() in {"TS", "CAM", "CINEMA"}

    def get_tmdb_id(self) -> int | None:
        """Return the provider's own TMDB id for this title, if it supplies one."""
        if not getattr(self, "title_info", None):
            try:
                self.collect_info_title()
            except Exception:
                return None

        title_node = getattr(self, "title_info", None) or {}
        tmdb_id = title_node.get("tmdb_id")
        return int(tmdb_id) if tmdb_id else None

    def collect_info_season(self, number_season: int) -> None:
        """
        Retrieve episode information for a specific season.

        Args:
            number_season (int): Season number to fetch episodes for

        Raises:
            Exception: If there's an error fetching episode information
        """
        try:
            # Get the season object from SeasonManager
            season = self.seasons_manager.get_season_by_number(number_season)
            if not season:
                logger.error(f"Season {number_season} not found")
                return

            # We'll aggregate episodes from both Italian and English catalogs
            episodes_by_lang = {}
            include_language = True

            def _fetch_lang_episodes(lang: str) -> list:
                """Fetch the episode list for a single language catalog. Returns [] on any failure."""
                try:
                    logger.info(f"Fetching episodes for season {number_season} in language '{lang}'")
                    with create_client(headers=self.headers) as client:
                        resp_ver = get_with_retry(client, f"{self.base_url}/{lang}", retries=1)
                    resp_ver.raise_for_status()
                    ver = BeautifulSoup(resp_ver.text, "html.parser")
                    ver = json.loads(ver.find("div", {"id": "app"}).get("data-page"))["version"]
                except Exception:
                    # Skip this language if we can't get version
                    return []

                custom_headers = self.headers.copy()
                custom_headers.update(
                    {
                        "x-inertia": "true",
                        "x-inertia-version": ver,
                    }
                )
                client = create_client(headers=custom_headers)
                try:
                    response = get_with_retry(
                        client, f"{self.base_url}/{lang}/titles/{self.media_id}-{self.series_name}/season-{number_season}",
                        retries=1,
                    )
                    response.raise_for_status()
                    loaded = response.json().get("props", {}).get("loadedSeason") or {}
                    # Missing seasons can return HTTP 200 with season 1's episodes.
                    if str(loaded.get("number")) != str(number_season):
                        logger.warning(
                            "Ignoring %s catalog season %s returned for requested season %s",
                            lang, loaded.get("number"), number_season,
                        )
                        return []
                    expected_id = catalog_ids.get(lang)
                    if expected_id is not None and str(loaded.get("id")) != str(expected_id):
                        logger.warning(
                            "Ignoring %s catalog season with unexpected id %s (expected %s)",
                            lang, loaded.get("id"), expected_id,
                        )
                        return []
                    return loaded.get("episodes") or []
                except Exception as e:
                    logger.debug(f"No season data for lang {lang}: {e}")
                    return []
                finally:
                    client.close()

            # Query only catalogs that actually list this season.
            catalog_ids = getattr(season, "catalog_ids", {})
            langs = [lang for lang in self.languages if lang in catalog_ids]
            if not langs:
                return
            with ThreadPoolExecutor(max_workers=len(langs)) as pool:
                results = list(pool.map(_fetch_lang_episodes, langs))
            for lang, json_response in zip(langs, results, strict=False):
                episodes_by_lang[lang] = json_response

            # Merge episodes from both languages
            def _merge_and_add(ep_lists_by_lang: dict, season_obj: Season, attach_language: bool = False):
                logger.info(f"Merging episodes for season {season_obj.number} with language attachment: {attach_language}")
                merged = {}
                for lang, ep_list in ep_lists_by_lang.items():
                    for ep in ep_list:
                        num = ep.get("number")
                        if num is None:
                            # fallback to id if number missing
                            num = ep.get("id")

                        if num in merged:
                            # already exists from other language -> mark both
                            existing = merged[num]
                            if attach_language:
                                prev_lang = getattr(existing, "language", None)
                                if prev_lang:
                                    # combine languages uniquely
                                    langs = set(str(prev_lang).split(",")) | {lang}
                                    existing.language = ",".join(sorted(langs))
                        else:
                            kwargs = {}
                            if attach_language:
                                kwargs["language"] = lang

                            merged[num] = Episode(
                                id=ep.get("id"),
                                video_id=ep.get("id"),
                                number=ep.get("number"),
                                name=ep.get("name"),
                                duration=ep.get("duration"),
                                image=self._episode_image_url(self.base_url, ep),
                                provider_language=lang,
                                **kwargs,
                            )

                # Replace any existing episodes with merged result
                season_obj.episodes.episodes.clear()
                for key in sorted(merged.keys()):
                    season_obj.episodes.add(merged[key])

            _merge_and_add(episodes_by_lang, season, include_language)

        except Exception as e:
            logger.error(f"Error collecting episodes for season {number_season}: {e}")
            raise

    # ------------- FOR GUI -------------
    def getNumberSeason(self) -> int:
        """Get the total number of seasons available for the series."""
        with self._collect_lock:
            catalogs = {self.provider_language, *self.languages}
            if not self.seasons_manager.seasons or not catalogs.issubset(self._loaded_catalogs):
                self.collect_info_title()

        return len(self.seasons_manager.seasons)

    def getEpisodeSeasons(self, season_number: int) -> list:
        """Get all episodes for a specific season."""
        season = self.seasons_manager.get_season_by_number(season_number)

        if not season:
            logger.error(f"Season {season_number} not found")
            return []

        with self._collect_lock:
            if not season.episodes.episodes:
                self.collect_info_season(season_number)

        return season.episodes.episodes
