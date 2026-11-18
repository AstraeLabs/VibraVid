# 26.11.2025

import logging
import re
import threading

from VibraVid.services._base.object import Episode, Season, SeasonManager
from VibraVid.utils.http_client import create_client, get_headers

from .client import get_api

_BUILD_ID_RE = re.compile(r'"buildId":"([^"]+)"')


class GetSerieInfo:
    def __init__(self, url):
        """
        Initialize series scraper for Pluto TV

        Args:
            url (str): The full URL to the seasons endpoint
        """
        self.api = get_api()
        self.url = url
        self.series_name = ""
        self.seasons_manager = SeasonManager()
        self.seasons_data = {}
        self._get_series_info()
        self._collect_lock = threading.Lock()

    def _get_series_info(self):
        """Get series information including seasons"""
        try:
            params = {"offset": "1000", "page": "1"}
            response = create_client(headers=self.api.get_request_headers()).get(self.url, params=params)
            response.raise_for_status()
            json_response = response.json()

            self.series_name = json_response.get("name", "Unknown Series")
            seasons_array = json_response.get("seasons", [])

            if not seasons_array:
                logging.warning("No seasons found in JSON response")
                return

            # Process each season
            for season_obj in seasons_array:
                season_number = season_obj.get("number")
                if season_number is None:
                    logging.warning("Season without number found, skipping")
                    continue

                # Store season data
                self.seasons_data[str(season_number)] = season_obj

                # Add season to manager
                season = self.seasons_manager.add(
                    Season(number=season_number, name=f"Season {season_number}", id=f"season-{season_number}")
                )

                # Process episodes for this season
                episodes = season_obj.get("episodes", [])
                for episode in episodes:
                    season.episodes.add(
                        Episode(
                            id=episode.get("_id"),
                            video_id=episode.get("_id"),
                            name=episode.get("name", f"Episode {episode.get('number')}"),
                            number=episode.get("number"),
                            duration=round(episode.get("duration", 0) / 1000 / 60) if episode.get("duration") else 0,
                        )
                    )

        except Exception as e:
            logging.error(f"Error collecting series info: {e}")
            raise

    # ------------- FOR GUI -------------
    def getNumberSeason(self) -> int:
        """Get total number of seasons"""
        return len(self.seasons_manager.seasons)

    def getEpisodeSeasons(self, season_number: int) -> list:
        """Get all episodes for a specific season"""
        season = self.seasons_manager.get_season_by_number(season_number)
        if season:
            return season.episodes.episodes

        return []


class GetSerieInfoBySlug:
    def __init__(self, slug: str):
        self.api = get_api()
        self.slug = slug
        self.series_name = ""
        self.seasons_manager = SeasonManager()
        self._get_series_info()

    def _get_build_id(self) -> str:
        response = create_client(headers=get_headers()).get(f"https://pluto.tv/it/shows/{self.slug}/")
        response.raise_for_status()

        match = _BUILD_ID_RE.search(response.text)
        if not match:
            raise RuntimeError("Could not resolve Pluto TV Next.js build id")

        return match.group(1)

    def _fetch_season_page(self, build_id: str, season_number: int) -> dict:
        from VibraVid.utils import disk_cache

        cache_name = f"slugpage_{self.slug}_s{season_number}_{build_id}"
        cached = disk_cache.load("plutotv", cache_name)
        if isinstance(cached, dict) and cached.get("pageProps"):
            return cached["pageProps"]

        url = f"https://pluto.tv/_next/data/{build_id}/it/shows/{self.slug}/season/{season_number}.json"
        params = {"path": ["it", "shows", self.slug, "season", str(season_number)]}
        response = create_client(headers=get_headers()).get(url, params=params)
        response.raise_for_status()

        page_props = response.json().get("pageProps", {})
        try:
            disk_cache.save("plutotv", cache_name, {"pageProps": page_props}, ttl=12 * 3600)
        except Exception as e:
            logging.debug(f"Pluto TV season page cache save failed: {e}")
        return page_props

    def _get_num_seasons(self, page_props: dict) -> int:
        queries = (page_props.get("dehydratedState") or {}).get("queries", [])
        for query in queries:
            if query.get("queryKey", [None])[0] == "show-home":
                show = query.get("state", {}).get("data", {}).get("showHome", {}).get("show", {})
                return show.get("numSeasons", 1)

        return 1

    def _get_show_title(self, page_props: dict) -> str:
        queries = (page_props.get("dehydratedState") or {}).get("queries", [])
        for query in queries:
            if query.get("queryKey", [None])[0] == "show-home":
                show = query.get("state", {}).get("data", {}).get("showHome", {}).get("show", {})
                if show.get("title"):
                    return show["title"]
        return self.slug

    def _extract_episodes(self, page_props: dict, season_number: int) -> list:
        """Extract episodes for a given season from the page properties."""
        legacy = page_props.get("initialEpisodes") or []
        if legacy:
            return legacy

        queries = (page_props.get("dehydratedState") or {}).get("queries", [])
        for query in queries:
            key = query.get("queryKey") or []
            if not key or key[0] != "full-episodes-pagination":
                continue

            params = key[1] if len(key) > 1 and isinstance(key[1], dict) else {}
            if str(params.get("seasonNum", season_number)) != str(season_number):
                continue

            pages = query.get("state", {}).get("data", {}).get("pages", [])
            collected: list = []
            for page in pages:
                collected.extend(((page or {}).get("fullEpisodes") or {}).get("episodes", []) or [])
            if collected:
                return collected

        return []

    def _get_series_info(self):
        try:
            build_id = self._get_build_id()

            page_props = self._fetch_season_page(build_id, 1)
            self.series_name = self._get_show_title(page_props) or self.slug

            legacy_first = page_props.get("initialEpisodes") or []
            if legacy_first and not self.series_name:
                self.series_name = legacy_first[0].get("seriesTitle", self.slug)

            num_seasons = self._get_num_seasons(page_props)

            for season_number in range(1, num_seasons + 1):
                if season_number != 1:
                    page_props = self._fetch_season_page(build_id, season_number)
                episodes = self._extract_episodes(page_props, season_number)

                season = self.seasons_manager.add(
                    Season(number=season_number, name=f"Season {season_number}", id=f"season-{season_number}")
                )

                for index, episode in enumerate(episodes, start=1):
                    season.episodes.add(
                        Episode(
                            id=episode.get("contentId"),
                            video_id=episode.get("contentId"),
                            name=episode.get("label") or episode.get("title") or f"Episode {index}",
                            number=index,
                            duration=round(episode.get("duration", 0) / 60) if episode.get("duration") else 0,
                        )
                    )

        except Exception as e:
            logging.error(f"Error collecting series info by slug: {e}")
            raise

    # ------------- FOR GUI -------------
    def getNumberSeason(self) -> int:
        """Get total number of seasons"""
        return len(self.seasons_manager.seasons)

    def getEpisodeSeasons(self, season_number: int) -> list:
        """Get all episodes for a specific season"""
        season = self.seasons_manager.get_season_by_number(season_number)
        if season:
            return season.episodes.episodes

        return []
