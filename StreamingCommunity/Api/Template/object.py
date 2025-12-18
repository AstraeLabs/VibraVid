# 23.11.24

from typing import Dict, Any, List, Optional, TypedDict


class Episode:
    def __init__(self, data: Dict[str, Any]):
        self.data = data

        self.id: int = data.get('id', 0)
        self.number: int = data.get('number', 1)
        self.name: str = data.get('name', '')
        self.duration: int = data.get('duration', 0)
        self.url: str = data.get('url', '')
        self.mpd_id: str = data.get('mpd_id', '')
        self.channel: str = data.get('channel', '')
        self.category: str = data.get('category', '')

    def __str__(self):
        return f"Episode(id={self.id}, number={self.number}, name='{self.name}', duration={self.duration} sec)"

class EpisodeManager:
    def __init__(self):
        self.episodes: List[Episode] = []

    def add(self, episode_data: Dict[str, Any]):
        """
        Add a new episode to the manager.

        Parameters:
            - episode_data (Dict[str, Any]): A dictionary containing data for the new episode.
        """
        episode = Episode(episode_data)
        self.episodes.append(episode)

    def get(self, index: int) -> Episode:
        """
        Retrieve an episode by its index in the episodes list.

        Parameters:
            - index (int): The zero-based index of the episode to retrieve.
        """
        return self.episodes[index]
    
    def clear(self) -> None:
        """
        This method clears the episodes list.
        """
        self.episodes.clear()

    def __len__(self) -> int:
        """
        Get the number of episodes in the manager.
        """
        return len(self.episodes)

    def __str__(self):
        return f"EpisodeManager(num_episodes={len(self.episodes)})"


class Season:
    def __init__(self, data: Dict[str, Any]):
        self.id: int = data.get('id', 0)
        self.number: int = data.get('number', 0)
        self.name: str = data.get('name', '')
        self.slug: str = data.get('slug', '')
        self.type: str = data.get('type', '')
        self.episodes: EpisodeManager = EpisodeManager()

    def __str__(self):
        return f"Season(id={self.id}, number={self.number}, name='{self.name}', episodes={self.episodes.length()})"

class SeasonManager:
    def __init__(self):
        self.seasons: List[Season] = []
    
    def add_season(self, season_data: Dict[str, Any]) -> Season:
        """
        Add a new season to the manager and return it.
        
        Parameters:
            - season_data (Dict[str, Any]): A dictionary containing data for the new season.
        """
        season = Season(season_data)
        self.seasons.append(season)
        return season
        
    def get_season_by_number(self, number: int) -> Optional[Season]:
        """
        Get a season by its number.
        
        Parameters:
            - number (int): The season number (1-based index)
        """
        if len(self.seasons) == 1:
            return self.seasons[0]
        
        for season in self.seasons:
            if season.number == number:
                return season
            
        return None
    
    def __len__(self) -> int:
        """
        Return the number of seasons managed.
        """
        return len(self.seasons)
    

class MediaItemData(TypedDict, total=False):
    id: int                 # GENERAL
    name: str               # GENERAL     
    type: str               # GENERAL
    url: str                # GENERAL
    size: str               # GENERAL
    score: str              # GENERAL
    date: str               # GENERAL
    desc: str               # GENERAL

    seeder: int             # TOR
    leecher: int            # TOR

    slug: str               # SC
    
class MediaItemMeta(type):
    def __new__(cls, name, bases, dct):
        def init(self, **kwargs):
            for key, value in kwargs.items():
                setattr(self, key, value)

        dct['__init__'] = init

        def get_attr(self, item):
            return self.__dict__.get(item, None)

        dct['__getattr__'] = get_attr

        def set_attr(self, key, value):
            self.__dict__[key] = value

        dct['__setattr__'] = set_attr

        return super().__new__(cls, name, bases, dct)

class MediaItem(metaclass=MediaItemMeta):
    id: int                 # GENERAL
    name: str               # GENERAL     
    type: str               # GENERAL
    url: str                # GENERAL
    size: str               # GENERAL
    score: str              # GENERAL
    date: str               # GENERAL
    desc: str               # GENERAL

    seeder: int             # TOR
    leecher: int            # TOR

    slug: str               # SC
 
class MediaManager:
    def __init__(self):
        self.media_list: List[MediaItem] = []

    def add_media(self, data: dict) -> None:
        """
        Add media to the list.

        Args:
            data (dict): Media data to add.
        """
        self.media_list.append(MediaItem(**data))

    def get(self, index: int) -> MediaItem:
        """
        Get a media item from the list by index.

        Args:
            index (int): The index of the media item to retrieve.

        Returns:
            MediaItem: The media item at the specified index.
        """
        return self.media_list[index]

    def get_length(self) -> int:
        """
        Get the number of media items in the list.

        Returns:
            int: Number of media items.
        """
        return len(self.media_list)

    def clear(self) -> None:
        """
        This method clears the media list.
        """
        self.media_list.clear()

    def __str__(self):
        return f"MediaManager(num_media={len(self.media_list)})"