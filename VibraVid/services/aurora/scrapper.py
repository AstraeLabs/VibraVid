# 26.11.25

import logging
import threading

from VibraVid.services._base.object import Episode, Season, SeasonManager
from VibraVid.utils.http_client import create_client, get_headers

logger = logging.getLogger(__name__)


class GetSerieInfo:
    def __init__(self, url):
        """
        Initialize the GetSerieInfo class for scraping TV series information.

        Args:
            url (str): The URL of the streaming site.
        """
        self.url = url
        self.headers = get_headers()
        self.series_name = None
        self.seasons_manager = SeasonManager()
        self.all_episodes = []
        self.title_info = None
        self._collect_lock = threading.Lock()

    def collect_info_title(self) -> None:
        """
        Retrieve general information about the TV series from the streaming site.
        """
        try:
            with create_client(headers=self.headers) as client:
                response = client.get(self.url)
            response.raise_for_status()

            # Parse JSON response
            json_response = response.json()

            # Extract episodes from blocks[1]['items']
            blocks = json_response.get("blocks", [])
            if len(blocks) < 2:
                logger.error(f"Unexpected response structure: {len(blocks)} blocks found")
                return

            items = blocks[1].get("items", [])

            if not items:
                logger.error("No episodes found in response")
                return

            # Store all episodes
            self.all_episodes = items

            # Get show title from first episode
            if items:
                first_episode = items[0]
                show_info = first_episode.get("show", {})

                # Set series_name if not provided
                if self.series_name is None:
                    self.series_name = show_info.get("title", "Unknown Series")

                self.title_info = {"id": show_info.get("id", ""), "title": show_info.get("title", "Unknown Series")}

                logger.info(f"Found series: {self.series_name} with {len(items)} total episodes")

            # Group episodes by season and build season structure
            seasons_dict = {}
            for episode in items:
                season_num = episode.get("seasonNumber", 0)

                if season_num not in seasons_dict:
                    seasons_dict[season_num] = {
                        "id": f"season-{season_num}",
                        "number": season_num,
                        "name": f"Season {season_num}",
                        "slug": f"season-{season_num}",
                    }

            # Add seasons to SeasonManager (sorted by season number)
            for season_num in sorted(seasons_dict.keys()):
                if season_num is not None:
                    s_data = seasons_dict[season_num]
                    self.seasons_manager.add(
                        Season(
                            id=s_data.get("id"),
                            number=s_data.get("number"),
                            name=s_data.get("name"),
                            slug=s_data.get("slug"),
                        )
                    )
                else:
                    logger.error(f"Episode with missing season number: {episode.get('id')}")

            logger.info(f"Found {len(seasons_dict)} seasons")

        except Exception as e:
            logger.error(f"Error collecting series info: {e}")
            raise

    def collect_info_season(self, number_season: int) -> None:
        """
        Retrieve episode information for a specific season.

        Args:
            number_season (int): Season number to fetch episodes for

        Raises:
            Exception: If there's an error fetching episode information
        """
        try:
            # Make sure we have collected title info
            if not self.all_episodes:
                logger.error("No episodes loaded, calling collect_info_title()")
                self.collect_info_title()

            season = self.seasons_manager.get_season_by_number(number_season)
            if not season:
                logger.error(f"Season {number_season} not found")
                return

            # Filter episodes for this specific season
            season_episodes = [ep for ep in self.all_episodes if ep.get("seasonNumber") == number_season]

            if not season_episodes:
                logger.error(f"No episodes found for season {number_season}")
                return

            # Sort episodes by episode number in ascending order
            season_episodes.sort(key=lambda x: x.get("episodeNumber", 0), reverse=False)

            logger.info(f"Processing {len(season_episodes)} episodes for season {number_season}")

            # Transform episodes to match the expected format
            for episode in season_episodes:
                # Convert duration from milliseconds to minutes
                duration_ms = episode.get("videoDuration", 0)
                duration_minutes = round(duration_ms / 1000 / 60) if duration_ms else 0

                # Add episode to the season's episode manager
                season.episodes.add(
                    Episode(
                        id=episode.get("id"),
                        number=episode.get("episodeNumber"),
                        name=episode.get("title", f"Episode {episode.get('episodeNumber')}"),
                        description=episode.get("description"),
                        duration=duration_minutes,
                        poster=episode.get("poster", {}).get("src"),
                        channel="X-REALM-IT" if episode.get("channel") is None else "X-REALM-DPLAY",
                    )
                )

            logger.info(f"Added {len(season_episodes)} episodes to season {number_season}")

        except Exception as e:
            logger.error(f"Error collecting episodes for season {number_season}: {e}")
            raise

    # ------------- FOR GUI -------------
    def getNumberSeason(self) -> int:
        """Get the total number of seasons available for the series."""
        with self._collect_lock:
            if not self.seasons_manager.seasons:
                logger.info("No seasons loaded, calling collect_info_title()")
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
