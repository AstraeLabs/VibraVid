# 06-06-2025 By @FrancescoGrazioso -> "https://github.com/FrancescoGrazioso"


import importlib
from typing import List, Optional


# Internal utilities
from .base import BaseStreamingAPI, MediaItem, Season, Episode


# External utilities
from StreamingCommunity.Api.Template.loader import get_folder_name
from StreamingCommunity.Api.Service.tubitv.util.get_license import get_bearer_token
from StreamingCommunity.Api.Service.tubitv.util.ScrapeSerie import GetSerieInfo


class TubiTVAPI(BaseStreamingAPI):
    def __init__(self):
        super().__init__()
        self.site_name = "tubitv"
        self._load_config()
        self._search_fn = None
    
    def _load_config(self):
        """Load site configuration."""
        self.base_url = "https://tubitv.com"
    
    def _get_search_fn(self):
        """Lazy load the search function."""
        if self._search_fn is None:
            module = importlib.import_module(f"StreamingCommunity.Api.{get_folder_name()}.tubitv")
            self._search_fn = getattr(module, "search")
        return self._search_fn
    
    def search(self, query: str) -> List[MediaItem]:
        """
        Search for content on Tubi TV.
        
        Args:
            query: Search term
            
        Returns:
            List of MediaItem objects
        """
        try:
            search_fn = self._get_search_fn()
            database = search_fn(query, get_onlyDatabase=True)
            
            results = []
            if database and hasattr(database, 'media_list'):
                for element in database.media_list:
                    item_dict = element.__dict__.copy() if hasattr(element, '__dict__') else {}
                    
                    media_item = MediaItem(
                        title=item_dict.get('name'),
                        type=item_dict.get('type'),
                        url=item_dict.get('url'),
                        poster=item_dict.get('image'),
                        year=item_dict.get('date'),
                        raw_data=item_dict
                    )
                    results.append(media_item)
            
            return results
        
        except Exception as e:
            raise Exception(f"Tubi TV search error: {e}")
    
    def get_series_metadata(self, media_item: MediaItem) -> Optional[List[Season]]:
        """
        Get seasons and episodes for a Tubi TV series.
        
        Args:
            media_item: MediaItem to get metadata for
            
        Returns:
            List of Season objects, or None if not a series
        """
        if media_item.is_movie:
            return None
        
        try:
            bearer_token = get_bearer_token()
            
            # Initialize scraper with URL, bearer token, and series name
            scraper = GetSerieInfo(media_item.url, bearer_token, media_item.title)
            scraper.getNumberSeason()
            seasons_count = len(scraper.seasons_manager.seasons)
            
            if not seasons_count:
                print(f"[TubiTV] No seasons found for url: {media_item.url}")
                return None
        
            seasons = []
            for idx, season_obj in enumerate(scraper.seasons_manager.seasons, 1):
                try:
                    # Get episodes for this season using season number
                    episodes_raw = scraper.getEpisodeSeasons(season_obj.number)
                    episodes = []
                    
                    for ep_idx, ep in enumerate(episodes_raw or [], 1):
                        episode = Episode(
                            number=ep_idx,
                            name=getattr(ep, 'name', f"Episode {ep_idx}"),
                            id=getattr(ep, 'id', ep_idx)
                        )
                        episodes.append(episode)
                    
                    season = Season(number=season_obj.number, episodes=episodes)
                    seasons.append(season)
                    print(f"[TubiTV] Season {season_obj.number}: {len(episodes)} episodes")
                
                except Exception as e:
                    print(f"[TubiTV] Error getting season {season_obj.number}: {e}")
                    continue
            
            return seasons if seasons else None
            
        except Exception as e:
            raise Exception(f"Error getting series metadata: {e}")
    
    def start_download(self, media_item: MediaItem, season: Optional[str] = None, episodes: Optional[str] = None) -> bool:
        """
        Start downloading from Tubi TV.
        
        Args:
            media_item: MediaItem to download
            season: Season number (for series)
            episodes: Episode selection
            
        Returns:
            True if download started successfully
        """
        try:
            search_fn = self._get_search_fn()
            
            # Prepare direct_item from MediaItem
            direct_item = media_item.raw_data or media_item.to_dict()
            
            # Prepare selections
            selections = None
            if season or episodes:
                selections = {
                    'season': season,
                    'episode': episodes
                }
            
            # Execute download
            search_fn(direct_item=direct_item, selections=selections)
            return True
            
        except Exception as e:
            raise Exception(f"Download error: {e}")