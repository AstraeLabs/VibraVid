# 06-06-2025 By @FrancescoGrazioso -> "https://github.com/FrancescoGrazioso"


from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional
from dataclasses import dataclass


@dataclass
class MediaItem:
    """Standardized media item representation."""
    id: Any
    title: str
    slug: str
    type: str  # 'film', 'series', 'ova', etc.
    url: Optional[str] = None
    poster: Optional[str] = None
    release_date: Optional[str] = None
    year: Optional[int] = None
    raw_data: Optional[Dict[str, Any]] = None
    
    @property
    def is_movie(self) -> bool:
        return self.type.lower() in ['film', 'movie', 'ova']
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            'id': self.id,
            'title': self.title,
            'slug': self.slug,
            'type': self.type,
            'url': self.url,
            'poster': self.poster,
            'release_date': self.release_date,
            'year': self.year,
            'raw_data': self.raw_data,
            'is_movie': self.is_movie
        }


@dataclass
class Episode:
    """Episode information."""
    number: int
    name: str
    id: Optional[Any] = None
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            'number': self.number,
            'name': self.name,
            'id': self.id
        }


@dataclass
class Season:
    """Season information."""
    number: int
    episodes: List[Episode]
    
    @property
    def episode_count(self) -> int:
        return len(self.episodes)
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            'number': self.number,
            'episodes': [ep.to_dict() for ep in self.episodes],
            'episode_count': self.episode_count
        }


class BaseStreamingAPI(ABC):
    """Base class for all streaming site APIs."""
    
    def __init__(self):
        self.site_name: str = ""
        self.base_url: str = ""
    
    @abstractmethod
    def search(self, query: str) -> List[MediaItem]:
        """
        Search for content on the streaming site.
        
        Args:
            query: Search term
            
        Returns:
            List of MediaItem objects
        """
        pass
    
    @abstractmethod
    def get_series_metadata(self, media_item: MediaItem) -> Optional[List[Season]]:
        """
        Get seasons and episodes for a series.
        
        Args:
            media_item: MediaItem to get metadata for
            
        Returns:
            List of Season objects, or None if not a series
        """
        pass
    
    @abstractmethod
    def start_download(self, media_item: MediaItem, season: Optional[str] = None, episodes: Optional[str] = None) -> bool:
        """
        Start downloading content.
        
        Args:
            media_item: MediaItem to download
            season: Season number (for series)
            episodes: Episode selection (e.g., "1-5" or "1,3,5" or "*" for all)
            
        Returns:
            True if download started successfully
        """
        pass
    
    def ensure_complete_item(self, partial_item: Dict[str, Any]) -> MediaItem:
        """
        Ensure a media item has all required fields by searching the database.
        
        Args:
            partial_item: Dictionary with partial item data
            
        Returns:
            Complete MediaItem object
        """
        # If already complete, convert to MediaItem
        if partial_item.get('id') and (partial_item.get('slug') or partial_item.get('url')):
            return self._dict_to_media_item(partial_item)
        
        # Try to find in database
        query = (partial_item.get('title') or partial_item.get('name') or partial_item.get('slug') or partial_item.get('display_title'))
        
        if query:
            results = self.search(query)
            if results:
                wanted_slug = partial_item.get('slug')
                if wanted_slug:
                    for item in results:
                        if item.slug == wanted_slug:
                            return item
                        
                return results[0]
        
        # Fallback: return partial item
        return self._dict_to_media_item(partial_item)
    
    def _dict_to_media_item(self, data: Dict[str, Any]) -> MediaItem:
        """Convert dictionary to MediaItem."""
        return MediaItem(
            id=data.get('id'),
            title=data.get('title') or data.get('name') or 'Unknown',
            slug=data.get('slug') or '',
            type=data.get('type') or data.get('media_type') or 'unknown',
            url=data.get('url'),
            poster=data.get('poster') or data.get('poster_url') or data.get('image'),
            release_date=data.get('release_date') or data.get('first_air_date'),
            year=data.get('year'),
            raw_data=data
        )