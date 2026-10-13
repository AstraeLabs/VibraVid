# 21.05.24

import logging
import re
import threading

from VibraVid.services._base.object import Episode, Season, SeasonManager
from VibraVid.utils.http_client import create_client, get_headers

logger = logging.getLogger(__name__)


class GetSerieInfo:
    def __init__(self, path_id: str):
        """Initialize the GetSerieInfo class."""
        self.base_url = "https://www.raiplay.it"
        self.path_id = path_id
        self.series_name = None
        self.prog_description = None
        self.prog_year = None
        self.seasons_manager = SeasonManager()
        self.season_block_mapping = {}  # Map season number to block_id
        self.all_seasons_data = []  # Store all seasons before filtering
        self._collect_lock = threading.Lock()

    def collect_info_title(self) -> None:
        """Get series info including seasons from all multimedia blocks."""
        try:
            path = self.path_id.lstrip("/")
            program_url = f"{self.base_url}/{path}"
            with create_client(headers=get_headers()) as client:
                response = client.get(program_url)

            # If 404, content is not yet available
            if response.status_code == 404:
                logger.error(f"Content not yet available: {program_url}")
                return

            response.raise_for_status()
            json_data = response.json()

            # Get basic program info
            program_info = json_data.get("program_info", {})
            self.prog_description = program_info.get("vanity", "") or program_info.get("description", "")
            self.prog_year = program_info.get("year", "")
            self.series_name = program_info.get("title", "") or program_info.get("name", "")

            # Collect all seasons from all multimedia blocks
            self.all_seasons_data = []
            blocks_found = {}

            # Block names to skip (supplementary content, not main seasons)
            skip_blocks = {"clip", "extra", "highlights", "anticipazioni", "speciale", "backstage"}

            for block in json_data.get("blocks", []):
                block_type = block.get("type", "")
                block_name = block.get("name", "N/A")
                block_id = block.get("id", "")

                # Skip supplementary blocks
                if block_name.lower() in skip_blocks:
                    logger.debug(f" - Skipping {block_name} block")
                    continue

                # Only process multimedia blocks with sets
                if block_type == "RaiPlay Multimedia Block" and "sets" in block:
                    sets = block.get("sets", [])

                    for season_set in sets:
                        episode_size = season_set.get("episode_size", {})
                        episode_count = episode_size.get("number", 0)
                        set_name = season_set.get("name", "")

                        # Skip Clip sets
                        if set_name.lower() == "clip":
                            logger.debug(" - Skipping Clip set")
                            continue

                        # Skip sets that don't look like seasons (no number, not "stagione")
                        # unless the block_name has a season number
                        has_season_number = bool(re.search(r"(\d+)", set_name)) or bool(re.search(r"(\d+)", block_name))
                        is_season_like = "stagione" in set_name.lower() or "stagione" in block_name.lower()
                        if not has_season_number and not is_season_like:
                            logger.debug(f" - Skipping non-season set: {set_name} in block {block_name}")
                            continue

                        # Only add sets with episodes
                        if episode_count > 0:
                            self.all_seasons_data.append(
                                {"season_set": season_set, "block_id": block_id, "block_name": block_name}
                            )

                            # Track which blocks we found
                            if block_name not in blocks_found:
                                blocks_found[block_name] = 0
                            blocks_found[block_name] += 1

            # Add all collected seasons, but avoid duplicates by season number
            # Keep only the first occurrence of each season number (from main block)
            seen_seasons = set()
            for season_data in self.all_seasons_data:
                set_name = season_data["season_set"].get("name", "")
                block_name = season_data["block_name"]
                # Extract season number to check for duplicates
                match = re.search(r"(\d+)", block_name)
                if not match:
                    match = re.search(r"(\d+)", set_name)
                if match:
                    season_num = int(match.group(1))
                    if season_num in seen_seasons:
                        logger.debug(f" - Skipping duplicate season {season_num} from block {block_name}")
                        continue
                    seen_seasons.add(season_num)

                self._add_season(season_data["season_set"], season_data["block_id"], season_data["block_name"])

        except Exception as e:
            logger.error(f"Unexpected error collecting series info: {e}")

    def _add_season(self, season_set: dict, block_id: str, block_name: str):
        """Add a season combining set name and block name."""
        set_name = season_set.get("name", "")

        # Try to extract season number from block_name first (e.g., "Stagione 4")
        match = re.search(r"(\d+)", block_name)
        if match:
            season_number = int(match.group(1))
        else:
            # Fallback to set_name if block_name doesn't have a number
            match = re.search(r"(\d+)", set_name)
            if match:
                season_number = int(match.group(1))
            else:
                season_number = len(self.seasons_manager.seasons) + 1

        # Determine season name: prefer the one containing "Stagione" or the block_name
        if "stagione" in block_name.lower():
            season_name = block_name
            season_type = set_name
        elif "stagione" in set_name.lower():
            season_name = set_name
            season_type = block_name
        else:
            season_name = block_name
            season_type = set_name

        # Store block_id mapping
        self.season_block_mapping[season_number] = {"block_id": block_id, "set_id": season_set.get("id")}

        self.seasons_manager.add(
            Season(id=season_set.get("id"), number=int(season_number), name=season_name, type=season_type)
        )

    def collect_info_season(self, number_season: int) -> None:
        """Get episodes for a specific season using episodes.json endpoint."""
        try:
            season = self.seasons_manager.get_season_by_number(number_season)
            block_info = self.season_block_mapping[number_season]
            block_id = block_info["block_id"]
            set_id = block_info["set_id"]

            # Build episodes endpoint URL
            base_path = self.path_id.lstrip("/").replace(".json", "")
            url = f"{self.base_url}/{base_path}/{block_id}/{set_id}/episodes.json"

            with create_client(headers=get_headers()) as client:
                response = client.get(url)
            response.raise_for_status()

            episodes_data = response.json()

            # Navigate nested structure to find cards
            cards = []
            seasons = episodes_data.get("seasons", [])
            if seasons:
                for season_data in seasons:
                    episodes = season_data.get("episodes", [])
                    for episode in episodes:
                        cards.extend(episode.get("cards", []))

            # Fallback to direct cards if nested structure not found
            if not cards:
                cards = episodes_data.get("cards", [])

            # Add episodes to season
            for ep in cards:
                video_url = ep.get("video_url", "")
                mpd_id = ""
                if video_url and "=" in video_url:
                    mpd_id = video_url.split("=")[1].strip()

                weblink = ep.get("weblink", "") or ep.get("url", "")
                episode_url = f"{self.base_url}{weblink}" if weblink else ""

                season.episodes.add(
                    Episode(
                        id=ep.get("id"),
                        number=ep.get("episode"),
                        name=ep.get("episode_title") or ep.get("name") or ep.get("toptitle"),
                        duration=ep.get("duration") or ep.get("duration_in_minutes"),
                        url=episode_url,
                        mpd_id=mpd_id,
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
