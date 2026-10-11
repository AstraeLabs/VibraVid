# 22.08.26
# By @sync-luca98

import re
import threading
from urllib.parse import urljoin

from VibraVid.services._base.object import Episode, Season, SeasonManager
from VibraVid.utils.http_client import create_client, get_userAgent
from rich.console import Console

console = Console()


class GetSerieInfo:
    def __init__(self, path_id: str):
        self.base_url = "https://www.la7.it"
        self.path_id = path_id
        self.series_name = None
        self.year = None
        self.seasons_manager = SeasonManager()
        self._collect_lock = threading.Lock()

    def collect_info_title(self) -> None:
        """Get series info including seasons/episodes from rivedila7 page."""
        try:
            path = self.path_id.lstrip("/")
            program_url = urljoin(self.base_url, path.rstrip("/") + "/rivedila7")
            
            with create_client(headers={"user-agent": get_userAgent()}) as client:
                response = client.get(program_url)
            
            if response.status_code == 404:
                return
            
            response.raise_for_status()
            html = response.text
            
            # Extract program name from page
            # Try og:title first
            og_title = re.search(r'property="og:title"[^>]*content="([^"]+)"', html, re.I)
            if og_title:
                self.series_name = og_title.group(1).split(",")[0].strip()
            else:
                # Try <title>
                title_match = re.search(r'<title[^>]*>([^<]+)</title>', html, re.I)
                if title_match:
                    self.series_name = title_match.group(1).split(",")[0].strip()
                else:
                    # Try h1
                    name_match = re.search(r'<h1[^>]*>([^<]+)</h1>', html, re.I)
                    if name_match:
                        self.series_name = re.sub(r"<[^>]+>", "", name_match.group(1)).strip()
            
            # Find all episode links - look for relative URLs in the HTML
            episode_paths = re.findall(r'/[^"\']*/rivedila7/[^"\']+', html)
            unique_paths = list(dict.fromkeys(episode_paths))
            
            season = Season(id="1", number=1, name="Episodi", type="Episodi")
            
            for idx, ep_path in enumerate(unique_paths, 1):
                ep_url = urljoin(self.base_url, ep_path)
                # Extract episode name from URL
                ep_name = ep_path.rsplit("/", 1)[-1].replace("-", " ").title()
                season.episodes.add(
                    Episode(
                        id=str(idx),
                        number=idx,
                        name=ep_name,
                        url=ep_url,
                    )
                )
            
            self.seasons_manager.add(season)
            
        except Exception as e:
            console.print(f"[red]Error collecting series info: {e}")

    def getEpisodeSeasons(self, season_number: int) -> list:
        """Get all episodes for a specific season."""
        season = self.seasons_manager.get_season_by_number(season_number)
        
        if not season:
            console.print(f"[red]Season {season_number} not found")
            return []
        
        # Episodes already collected in collect_info_title
        return season.episodes.episodes


def get_series_episodes(path_id: str) -> list[tuple[str, str]]:
    """Get all episodes for a program path_id."""
    scrape = GetSerieInfo(path_id)
    scrape.collect_info_title()
    
    episodes = []
    for season in scrape.seasons_manager.seasons:
        for ep in season.episodes:
            episodes.append((ep.name, ep.url))
    return episodes