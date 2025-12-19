# 06-06-2025 By @FrancescoGrazioso -> "https://github.com/FrancescoGrazioso"


import importlib
from typing import List, Optional


# Internal utilities
from .base import BaseStreamingAPI, MediaItem, Season, Episode


# External utilities
from StreamingCommunity.Util import config_manager
from StreamingCommunity.Api.Template.loader import get_folder_name
from StreamingCommunity.Api.Service.altadefinizione.util.ScrapeSerie import GetSerieInfo


class AltaDefinizioneAPI(BaseStreamingAPI):
    def __init__(self):
        super().__init__()
        self.site_name = "altadefinizione"
        self._load_config()
        self._search_fn = None
    
    def _load_config(self):
        """Load site configuration."""
        self.base_url = (config_manager.get_site("altadefinizione", "full_url") or "").rstrip("/")
    
    def _get_search_fn(self):
        """Lazy load the search function."""
        if self._search_fn is None:
            module = importlib.import_module(f"StreamingCommunity.Api.{get_folder_name()}.altadefinizione")
            self._search_fn = getattr(module, "search")
        return self._search_fn
    
    def search(self, query: str) -> List[MediaItem]:
        """
        Search for content on AltaDefinizione.
        
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
                        raw_data=item_dict
                    )
                    results.append(media_item)
            
            return results
        
        except Exception as e:
            raise Exception(f"AltaDefinizione search error: {e}")
    
    def get_series_metadata(self, media_item: MediaItem) -> Optional[List[Season]]:
        """
        Get seasons and episodes for an AltaDefinizione series.
        
        Args:
            media_item: MediaItem to get metadata for
            
        Returns:
            List of Season objects, or None if not a series
        """
        if media_item.is_movie:
            return None
        
        try:
            scraper = GetSerieInfo(media_item.url)
            seasons_count = scraper.getNumberSeason()
            
            if not seasons_count:
                return None
            
            seasons = []
            for season_num in range(1, seasons_count + 1):
                try:
                    episodes_raw = scraper.getEpisodeSeasons(season_num)
                    episodes = []
                    
                    for idx, ep in enumerate(episodes_raw or [], 1):
                        episode = Episode(
                            number=idx,
                            name=getattr(ep, 'name', f"Episodio {idx}"),
                            id=getattr(ep, 'id', idx)
                        )
                        episodes.append(episode)
                    
                    season = Season(number=season_num, episodes=episodes)
                    seasons.append(season)
                
                except Exception:
                    continue
            
            return seasons if seasons else None
            
        except Exception as e:
            raise Exception(f"Error getting series metadata: {e}")
    
    def start_download(self, media_item: MediaItem, season: Optional[str] = None, episodes: Optional[str] = None) -> bool:
        """
        Start downloading from AltaDefinizione.
        
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