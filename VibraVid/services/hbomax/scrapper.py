# 22.12.25

import logging
import threading

from VibraVid.services._base.object import Episode, Season, SeasonManager
from VibraVid.utils.http_client import create_client

from .client import get_client

logger = logging.getLogger(__name__)


def _edit_id(video: dict, client, *, allow_video_id: bool = True) -> str | None:
    """Resolve Max's playable edit id from a CMS video resource."""
    relationships = video.get("relationships", {})
    edit = (relationships.get("edit") or {}).get("data") or {}
    if edit.get("id"):
        return edit["id"]

    video_id = video.get("id")
    if video_id:
        try:
            with create_client(headers=client.headers, cookies=client.cookies) as http_client:
                response = http_client.get(
                    f"{client.base_url}/content/videos/{video_id}/activeVideoForShow",
                    params={"include": "edit"},
                )
            response.raise_for_status()
            data = response.json()
            resources = [data.get("data")] + data.get("included", [])
            for resource in resources:
                if not isinstance(resource, dict):
                    continue
                candidate = ((resource.get("relationships") or {}).get("edit") or {}).get("data") or {}
                if candidate.get("id"):
                    return candidate["id"]
        except Exception:
            logger.debug("Could not resolve active edit for Max video %s", video_id, exc_info=True)

    return video_id if allow_video_id else None


class GetStandaloneInfo:
    def __init__(self, standalone_id: str):
        self.client = get_client()
        self.standalone_id = standalone_id
        self.content_info = None
        self._get_content_info()

    def _get_content_info(self):
        """Fetch standalone content information using /cms/routes/movie endpoint"""
        try:
            url = f"{self.client.base_url}/cms/routes/movie/{self.standalone_id}"
            params = {"include": "default", "decorators": "isFavorite,playbackAllowed,contentAction,badges"}
            with create_client(headers=self.client.headers, cookies=self.client.cookies) as client:
                response = client.get(url, params=params)
            response.raise_for_status()
            data = response.json()

            # Prefer the playable movie node over trailers/previews. Max uses
            # both MOVIE and STANDALONE depending on the catalog entry.
            playable = {"movie", "standalone"}
            self.content_info = next(
                (
                    x
                    for x in data.get("included", [])
                    if x.get("type") == "video"
                    and x.get("attributes", {}).get("videoType", "").lower() in playable
                ),
                None,
            )
            if not self.content_info:
                # Some catalog entries expose the feature video through the
                # show route even though they are classified as movies.
                fallback_url = f"{self.client.base_url}/cms/routes/show/{self.standalone_id}"
                with create_client(headers=self.client.headers, cookies=self.client.cookies) as client:
                    fallback_response = client.get(fallback_url, params=params)
                fallback_response.raise_for_status()
                fallback_data = fallback_response.json()
                self.content_info = next(
                    (
                        x
                        for x in fallback_data.get("included", [])
                        if x.get("type") == "video"
                        and x.get("attributes", {}).get("videoType", "").lower() in playable
                    ),
                    None,
                )

            if not self.content_info:
                logger.error(f"Standalone content not found for: {self.standalone_id}")
                return

            logger.debug(f"Loaded standalone info: {self.content_info.get('attributes', {}).get('name')}")

        except Exception as e:
            logger.error(f"Error in _get_content_info: {e}")

    def get_edit_id(self):
        """
        Get the edit ID for playback

        Returns:
            str: The edit ID for the standalone content
        """
        if not self.content_info:
            logger.error("Content info not loaded, cannot get edit_id")
            return None

        try:
            edit_id = _edit_id(self.content_info, self.client)
            if edit_id:
                logger.debug(f"Edit ID: {edit_id}")
                return edit_id
            else:
                logger.error("Edit ID not found in relationships")
                return None

        except Exception as e:
            logger.error(f"Error getting edit ID: {e}")
            return None


class GetLiveInfo:
    def __init__(self, video_id: str):
        """
        Initialize live event scraper for Max

        Args:
            video_id (str): The video ID of the live event
        """
        self.client = get_client()
        self.video_id = video_id
        self.content_info = None
        self._get_content_info()

    def _get_content_info(self):
        """Fetch live event information using the sport/video detail endpoint."""
        try:
            url = f"{self.client.base_url}/cms/routes/sport/{self.video_id}"
            params = {"include": "default", "decorators": "isFavorite,playbackAllowed,contentAction,badges"}
            with create_client(headers=self.client.headers, cookies=self.client.cookies) as client:
                response = client.get(url, params=params)

            if response.status_code != 200:
                # Fallback: query the video directly via content endpoint
                url = f"{self.client.base_url}/content/videos/{self.video_id}"
                with create_client(headers=self.client.headers, cookies=self.client.cookies) as client:
                    response = client.get(url)
                response.raise_for_status()
                data = response.json()
                if data.get("data", {}).get("type") == "video":
                    self.content_info = data["data"]
                if not self.content_info:
                    logger.error(f"Live content not found for video_id: {self.video_id}")
                return

            response.raise_for_status()
            data = response.json()

            # Use alternateId to find the content node (matches reference implementation _sport)
            title_id = self.video_id.split("/")[-1] if "/" in self.video_id else self.video_id
            self.content_info = next(
                (x for x in data.get("included", []) if x.get("attributes", {}).get("alternateId", "") == title_id),
                None,
            )

            if not self.content_info:
                logger.error(f"Live content not found for video_id: {self.video_id}")
                return

            logger.debug(f"Loaded live info: {self.content_info.get('attributes', {}).get('name')}")

        except Exception as e:
            logger.error(f"Error in GetLiveInfo._get_content_info: {e}")

    def get_edit_id(self):
        """
        Get the edit ID for playback.

        Returns:
            str: The edit ID for the live event
        """
        if not self.content_info:
            logger.error("Live content info not loaded, cannot get edit_id")
            return None

        try:
            edit_id = _edit_id(self.content_info, self.client)
            if edit_id:
                logger.debug(f"Live edit ID: {edit_id}")
                return edit_id
            else:
                logger.error("Edit ID not found in live content relationships")
                return None

        except Exception as e:
            logger.error(f"Error getting live edit ID: {e}")
            return None


class GetSerieInfo:
    def __init__(self, show_id: str):
        """
        Initialize series scraper for Max

        Args:
            show_id (str): The alternate ID of the show
        """
        self.client = get_client()
        self.show_id = show_id
        self.universal_id = None
        self.series_name = ""
        self.seasons_manager = SeasonManager()
        self.n_seasons = 0
        self.seasons_list = []
        self._collect_lock = threading.Lock()
        self._all_episodes = None
        self._get_show_info()

    def _fetch_all_episodes(self):
        """Fetch all episodes for the show"""
        try:
            # Fetch show data (includes season filter information)
            url = f"{self.client.base_url}/cms/routes/show/{self.show_id}"
            params = {"include": "default", "decorators": "viewingHistory,badges,isFavorite,contentAction"}
            with create_client(headers=self.client.headers, cookies=self.client.cookies) as client:
                response = client.get(url, params=params)
            response.raise_for_status()
            data = response.json()
            included = data.get("included", [])

            # Extract show info
            show_info = next(
                (x for x in included if x.get("attributes", {}).get("alternateId", "") == self.show_id), None
            )
            if not show_info:
                logger.error(f"Show info not found for: {self.show_id}")
                return []
            self.universal_id = show_info.get("attributes", {}).get("universalId")
            self.series_name = show_info.get("attributes", {}).get("name", "Unknown")

            # Locate the episodes content block
            content = next(
                (
                    x
                    for x in included
                    if "show-page-rail-episodes-tabbed-content" in x.get("attributes", {}).get("alias", "")
                ),
                None,
            )
            if not content:
                logger.error(f"Episodes content block not found for show {self.show_id}")
                return []

            # Season filter options (parameters for each season)
            season_filter = next(
                (f for f in content["attributes"]["component"].get("filters", []) if f.get("id") == "seasonNumber"),
                None,
            )
            if not season_filter:
                logger.error(f"Season filter not found for show {self.show_id}")
                return []
            season_params = [opt.get("parameter") for opt in season_filter.get("options", [])]

            all_episodes = []

            if season_params:
                for season_param in season_params:
                    # Max expects the stable generic collection route plus the
                    # show alternate id; the page component id returns an empty
                    # collection for authenticated profiles.
                    coll_url = f"{self.client.base_url}/cms/collections/generic-show-page-rail-episodes-tabbed-content"
                    coll_params = {
                        "include": "default",
                        "pf[show.id]": self.show_id,
                        "decorators": "viewingHistory,badges,isFavorite,contentAction",
                    }
                    key, value = season_param.split("=", 1)
                    coll_params[key] = value
                    with create_client(headers=self.client.headers, cookies=self.client.cookies) as client:
                        response = client.get(coll_url, params=coll_params)
                    response.raise_for_status()
                    season_data = response.json()

                    for item in season_data.get("included", []):
                        if item.get("type") == "video" and item.get("attributes", {}).get("videoType") == "EPISODE":
                            attrs = item["attributes"]
                            edit_id = _edit_id(item, self.client)
                            all_episodes.append(
                                {
                                    "id": edit_id,
                                    "show": self.series_name,
                                    "season": attrs.get("seasonNumber"),
                                    "episode": attrs.get("episodeNumber"),
                                    "title": attrs.get("name"),
                                }
                            )
            else:
                item_refs = content.get("relationships", {}).get("items", {}).get("data", [])
                collection_items = {x["id"]: x for x in included if x.get("type") == "collectionItem"}
                videos_by_id = {x["id"]: x for x in included if x.get("type") == "video"}

                for ref in item_refs:
                    citem = collection_items.get(ref.get("id"))
                    if not citem:
                        continue

                    video_ref = citem.get("relationships", {}).get("video", {}).get("data")
                    if not video_ref:
                        continue

                    video = videos_by_id.get(video_ref.get("id"))
                    if not video:
                        continue

                    vattrs = video.get("attributes", {})
                    if vattrs.get("videoType") not in ("EPISODE", "STANDALONE"):
                        continue

                    edit_id = _edit_id(video, self.client)
                    all_episodes.append(
                        {
                            "id": edit_id,
                            "show": self.series_name,
                            "season": vattrs.get("seasonNumber") or 1,
                            "episode": vattrs.get("episodeNumber") or (len(all_episodes) + 1),
                            "title": vattrs.get("name"),
                        }
                    )

            # Sort by season and episode
            all_episodes.sort(key=lambda x: (x["season"], x["episode"]))
            return all_episodes

        except Exception as e:
            logger.error(f"Error in _fetch_all_episodes: {e}")
            return []

    def _get_show_info(self):
        """Cache all episodes and set season counts."""
        try:
            if self._all_episodes is None:
                self._all_episodes = self._fetch_all_episodes()

            if not self._all_episodes:
                return False

            # Distinct seasons
            seasons_set = set(ep["season"] for ep in self._all_episodes if ep["season"] is not None)
            self.n_seasons = len(seasons_set)
            self.seasons_list = sorted(list(seasons_set))

            return True

        except Exception as e:
            logger.error(f"Failed to get show info: {e}")
            return False

    def _get_season_episodes(self, season_number: int):
        """Return episodes for a given season from the cached list."""
        if self._all_episodes is None:
            self._all_episodes = self._fetch_all_episodes()

        season_episodes = []
        for episode in self._all_episodes:
            if episode["season"] == season_number:
                season_episodes.append(
                    {
                        "id": episode["id"],
                        "video_id": episode["id"],
                        "name": episode["title"],
                        "episode_number": episode["episode"],
                        "duration": 0,
                    }
                )
        season_episodes.sort(key=lambda x: x["episode_number"])
        return season_episodes

    def collect_season(self):
        """Populate the seasons_manager with all seasons and episodes."""
        for season_num in self.seasons_list:
            episodes = self._get_season_episodes(season_num)
            if episodes:
                season_obj = self.seasons_manager.add(
                    Season(number=season_num, name=f"Season {season_num}", id=f"season_{season_num}")
                )
                if season_obj:
                    for ep in episodes:
                        season_obj.episodes.add(
                            Episode(
                                id=ep.get("id"),
                                video_id=ep.get("video_id"),
                                name=ep.get("name"),
                                number=ep.get("episode_number"),
                                duration=ep.get("duration"),
                            )
                        )


    # ------------- FOR GUI -------------
    def getNumberSeason(self) -> int:
        """Get total number of seasons"""
        with self._collect_lock:
            if not self.seasons_manager.seasons:
                self.collect_season()
        return len(self.seasons_manager.seasons)

    def getEpisodeSeasons(self, season_number: int) -> list:
        """Get all episodes for a specific season"""
        with self._collect_lock:
            if not self.seasons_manager.seasons:
                self.collect_season()
        season = self.seasons_manager.get_season_by_number(season_number)
        return season.episodes.episodes if season else []
