# 06-06-2025 By @FrancescoGrazioso -> "https://github.com/FrancescoGrazioso"


import importlib
from typing import List, Optional


# Internal utilities
from .base import BaseStreamingAPI, MediaItem, Season, Episode


# External utilities
from StreamingCommunity.Util import config_manager
from StreamingCommunity.Api.Template.loader import get_folder_name
from StreamingCommunity.Api.Service.animeunity.util.ScrapeSerie import ScrapeSerieAnime



class AnimeUnityAPI(BaseStreamingAPI):
    def __init__(self):
        super().__init__()
        self.site_name = "animeunity"
        self._load_config()
        self._search_fn = None
    
    def _load_config(self):
        """Load site configuration."""
        self.base_url = (config_manager.get_site("animeunity", "full_url") or "").rstrip("/")
    
    def _get_search_fn(self):
        """Lazy load the search function."""
        if self._search_fn is None:
            module = importlib.import_module(f"StreamingCommunity.Api.{get_folder_name()}.animeunity")
            self._search_fn = getattr(module, "search")
        return self._search_fn
    
    def search(self, query: str) -> List[MediaItem]:
        """
        Search for content on AnimeUnity.
        
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
                        id=item_dict.get('id'),
                        title=item_dict.get('name'),
                        slug=item_dict.get('slug', ''),
                        type=item_dict.get('type'),
                        url=item_dict.get('url'),
                        poster=item_dict.get('image'),
                        raw_data=item_dict
                    )
                    results.append(media_item)
            
            return results
        
        except Exception as e:
            raise Exception(f"AnimeUnity search error: {e}")
    
    def get_series_metadata(self, media_item: MediaItem) -> Optional[List[Season]]:
        """
        Get seasons and episodes for an AnimeUnity series.
        Note: AnimeUnity typically has single season anime.
        
        Args:
            media_item: MediaItem to get metadata for
            
        Returns:
            List of Season objects (usually one season), or None if not a series
        """
        # Check if it's a movie or OVA
        if media_item.is_movie:
            return None
        
        try:
            scraper = ScrapeSerieAnime(self.base_url)
            scraper.setup(series_name=media_item.slug, media_id=media_item.id)
            
            episodes_count = scraper.get_count_episodes()
            if not episodes_count:
                return None
            
            # AnimeUnity typically has single season
            episodes = []
            for ep_num in range(1, episodes_count + 1):
                episode = Episode(
                    number=ep_num,
                    name=f"Episodio {ep_num}",
                    id=ep_num
                )
                episodes.append(episode)
            
            season = Season(number=1, episodes=episodes)
            return [season]
            
        except Exception as e:
            raise Exception(f"Error getting series metadata: {e}")
    
    def start_download(self, media_item: MediaItem, season: Optional[str] = None, episodes: Optional[str] = None) -> bool:
        """
        Start downloading from AnimeUnity.
        
        Args:
            media_item: MediaItem to download
            season: Season number (typically 1 for anime)
            episodes: Episode selection
            
        Returns:
            True if download started successfully
        """
        try:
            search_fn = self._get_search_fn()
            
            # Prepare direct_item from MediaItem
            direct_item = media_item.raw_data or media_item.to_dict()
            
            # For AnimeUnity, we only use episode selection
            selections = None
            if episodes:
                selections = {'episode': episodes}
                
            elif not media_item.is_movie:
                # Default: download all episodes
                selections = {'episode': '*'}
            
            # Execute download
            search_fn(direct_item=direct_item, selections=selections)
            return True
            
        except Exception as e:
            raise Exception(f"Download error: {e}")