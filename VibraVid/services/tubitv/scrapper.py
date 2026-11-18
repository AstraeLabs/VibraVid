# 16.12.25

import logging
import re
import threading

from VibraVid.services._base.object import Episode, Season, SeasonManager
from VibraVid.utils.http_client import create_client, get_headers

logger = logging.getLogger(__name__)


def extract_content_id(url: str) -> str:
    """Extract content ID from Tubi TV URL"""
    # URL format: https://tubitv.com/series/{content_id}/{slug}
    match = re.search(r"/series/(\d+)/", url)
    if match:
        return match.group(1)
    return None


class GetSerieInfo:
    def __init__(self, url, bearer_token=None, series_name=None):
        """
        Initialize the GetSerieInfo class for scraping Tubi TV series information.

        Args:
            url (str): The URL of the series
            bearer_token (str, optional): Bearer token for authentication
        """
        self.url = url
        self.content_id = extract_content_id(url)
        self.bearer_token = bearer_token
        self.series_name = series_name
        self.seasons_manager = SeasonManager()
        self.all_episodes_by_season = {}
        self._collect_lock = threading.Lock()

        # Setup headers
        self.headers = get_headers()
        if self.bearer_token:
            self.headers["authorization"] = f"Bearer {self.bearer_token}"

    def collect_info_title(self) -> None:
        """
        Retrieve general information about the TV series from Tubi TV.
        """
        try:
            with create_client(headers=self.headers) as client:
                response = client.get(
                    f"https://content-cdn.production-public.tubi.io/cms/series/{self.content_id}/episodes"
                )
            response.raise_for_status()

            json_data = response.json()
            episodes_by_season = json_data.get("episodes_by_season", {})

            if not episodes_by_season:
                logger.error("No seasons found in response")
                return

            # Store episodes by season
            self.all_episodes_by_season = episodes_by_season

            # Create seasons in SeasonManager
            for season_num in sorted(episodes_by_season.keys(), key=int):
                self.seasons_manager.add(
                    Season(
                        id=f"season-{season_num}",
                        number=int(season_num),
                        name=f"Season {season_num}",
                        slug=f"season-{season_num}",
                    )
                )

        except Exception as e:
            logger.error(f"Error collecting series info: {e}")
            raise

    def collect_info_season(self, number_season: int) -> None:
        """
        Retrieve episode information for a specific season.

        Args:
            number_season (int): Season number to fetch episodes for
        """
        try:
            season = self.seasons_manager.get_season_by_number(number_season)
            if not season:
                logger.error(f"Season {number_season} not found")
                return

            params = {
                "app_id": "tubitv",
                "platform": "web",
                "content_id": self.content_id,
                "pagination[season]": str(number_season),
            }

            with create_client(headers=self.headers) as client:
                response = client.get("https://content-cdn.production-public.tubi.io/api/v2/content", params=params)
            response.raise_for_status()
            json_data = response.json()

            episodes = []
            for season_group in json_data.get("children", []):
                try:
                    group_season_num = int(season_group.get("id", -1))
                except (ValueError, TypeError):
                    continue

                if group_season_num != number_season:
                    continue  # skip other seasons

                for episode in season_group.get("children", []):
                    episodes.append(episode)

                break  # found our season group — no need to keep iterating

            if not episodes:
                logger.error(f"No episodes found for season {number_season}")
                return

            # Sort episodes by episode number
            episodes.sort(key=lambda x: int(x.get("episode_number", 0)))

            # Add episodes to the season object
            for episode in episodes:
                # Get thumbnail (first entry if available)
                thumbnails = episode.get("thumbnails", [])
                thumbnail = thumbnails[0] if thumbnails else ""

                # Convert duration from seconds to minutes
                duration_seconds = episode.get("duration", 0)
                duration_minutes = round(duration_seconds / 60) if duration_seconds else 0

                season.episodes.add(
                    Episode(
                        id=episode.get("id"),
                        name=episode.get("title", f"Episode {episode.get('episode_number')}"),
                        number=episode.get("episode_number"),
                        image=thumbnail,
                        year=episode.get("year"),
                        duration=duration_minutes,
                        needs_login=episode.get("needs_login"),
                        country=episode.get("country"),
                    )
                )

        except Exception as e:
            logger.error(f"Error collecting episodes for season {number_season}: {e}")
            raise

    # ------------- FOR GUI -------------
    def getNumberSeason(self) -> int:
        """Get the total number of seasons available for the series."""
        with self._collect_lock:
            if not self.seasons_manager.seasons:
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
