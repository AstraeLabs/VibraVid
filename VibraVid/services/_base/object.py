# 23.11.24

import difflib
import logging
from datetime import datetime
from typing import Any

from VibraVid.provider.tmdb import tmdb_client

logger = logging.getLogger(__name__)


class Episode:
    def __init__(
        self,
        id: Any | None = None,
        video_id: str | None = None,
        number: Any | None = None,
        name: str | None = None,
        duration: Any | None = None,
        url: str | None = None,
        mpd_id: str | None = None,
        channel: str | None = None,
        category: str | None = None,
        description: str | None = None,
        image: str | None = None,
        poster: str | None = None,
        year: Any | None = None,
        is_special: bool | None = None,
        tmdb_id: str | None = None,
        **kwargs,
    ):
        self.id = id
        self.video_id = video_id
        self.number = number
        self.name = name
        self.duration = duration
        self.url = url
        self.mpd_id = mpd_id
        self.channel = channel
        self.category = category
        self.description = description
        self.image = image
        self.poster = poster
        self.year = year
        self.is_special = is_special
        self.tmdb_id = tmdb_id

        # [SERVICE-SPECIFIC] Allow additional attributes from different services (e.g., main_guid for Crunchyroll)
        for key, value in kwargs.items():
            setattr(self, key, value)

    def to_dict(self) -> dict:
        """Convert the episode to a dictionary."""
        return self.__dict__.copy()

    def __str__(self):
        return f"Episode(id={self.id}, number={self.number}, name='{self.name}', duration={self.duration} min)"


class EpisodeManager:
    def __init__(self):
        self.episodes: list[Episode] = []

    def add(self, episode: Episode):
        self.episodes.append(episode)

    def get(self, index: int) -> Episode:
        return self.episodes[index]

    def clear(self) -> None:
        self.episodes.clear()

    def __len__(self) -> int:
        return len(self.episodes)

    def __str__(self):
        return f"EpisodeManager(num_episodes={len(self.episodes)})"


class Season:
    def __init__(
        self,
        id: int | None = None,
        number: int | None = None,
        name: str | None = None,
        slug: str | None = None,
        type: str | None = None,
        tmdb_id: str | None = None,
        **kwargs,
    ):
        self.id = id
        self.number = number
        self.name = name
        self.slug = slug
        self.type = type
        self.tmdb_id = tmdb_id
        self.episodes: EpisodeManager = EpisodeManager()

        for key, value in kwargs.items():
            setattr(self, key, value)

    def __str__(self):
        return f"Season(id={self.id}, number={self.number}, name='{self.name}', episodes={self.episodes.__len__()})"


class SeasonManager:
    def __init__(self):
        self.seasons: list[Season] = []

    def add(self, season: Season) -> Season:
        self.seasons.append(season)
        self.seasons.sort(key=lambda x: x.number)
        return season

    def get_season_by_number(self, number: int) -> Season | None:
        if len(self.seasons) == 1:
            return self.seasons[0]

        for season in self.seasons:
            if season.number == number:
                return season

        return None

    def __len__(self) -> int:
        return len(self.seasons)


class EntriesMeta(type):
    def __new__(cls, name, bases, dct):
        def init(self, **kwargs):
            for key, value in kwargs.items():
                setattr(self, key, value)

        dct["__init__"] = init

        def get_attr(self, item):
            return self.__dict__.get(item, None)

        dct["__getattr__"] = get_attr

        def set_attr(self, key, value):
            self.__dict__[key] = value

        dct["__setattr__"] = set_attr

        return super().__new__(cls, name, bases, dct)


class Entries(metaclass=EntriesMeta):
    id: int
    name: str
    type: str
    url: str
    size: str
    score: str
    desc: str
    availability: str
    slug: str
    year: str
    provider_language: str
    tmdb_id: str

    def to_dict(self):
        return self.__dict__.copy()

    @property
    def is_movie(self) -> bool:
        return str(getattr(self, "type", "")).lower() in ["film", "movie", "ova"]

    @property
    def poster(self) -> str:
        return getattr(self, "image", "") or getattr(self, "poster_url", "")

    def __str__(self):
        return f"Entries(id={self.id}, name='{self.name}', type='{self.type}', year='{self.year}', url='{self.url}', slug='{self.slug}', year='{self.year}')"


class EntriesManager:
    def __init__(self):
        self.media_list: list[Entries] = []

    def add(self, media: Entries) -> None:

        # MUSIC: Remove duplicates based on name, artist, album, and type
        media_name = str(getattr(media, "name", "") or "").strip().lower()
        media_artist = str(getattr(media, "artist", "") or "").strip().lower()
        media_album = str(getattr(media, "album", "") or "").strip().lower()
        media_type = str(getattr(media, "type", "") or "").strip().lower()
        if media_name:
            for existing in self.media_list:
                if (
                    str(getattr(existing, "name", "") or "").strip().lower() == media_name
                    and str(getattr(existing, "artist", "") or "").strip().lower() == media_artist
                    and str(getattr(existing, "album", "") or "").strip().lower() == media_album
                    and str(getattr(existing, "type", "") or "").strip().lower() == media_type
                ):
                    return

        # FILM / TV: Fetch year if it's "9999" and TMDB API key is available
        if media.year == "9999" and not tmdb_client.api_key:
            media.year = str(datetime.now().year)
        elif media.year == "9999":
            if media.slug and media.slug != "":
                logger.info(f"Fetching year for slug: {media.slug}, type: {media.type}")
                media.year = str(tmdb_client.get_year_by_slug_and_type(media.slug, media.type) or "9999")
                if media.year == "9999":
                    logger.warning("Cant fetch year setting current year.")
                    media.year = str(datetime.now().year)

            elif media.name and media.name != "":
                logger.info(f"Fetching year for name: {media.name}, type: {media.type}")
                media.year = str(
                    tmdb_client.get_year_by_slug_and_type(media.name.replace(" ", "-").lower(), media.type) or "9999"
                )
                if media.year == "9999":
                    logger.warning("Cant fetch year setting current year.")
                    media.year = str(datetime.now().year)

        self.media_list.append(media)

    def get(self, index: int) -> Entries:
        return self.media_list[index]

    def clear(self) -> None:
        self.media_list.clear()

    def __len__(self) -> int:
        return len(self.media_list)

    def __str__(self):
        return f"EntriesManager(num_media={len(self.media_list)})"

    def sort_by_fuzzy_score(self, query: str) -> None:
        """
        Calculate fuzzy match scores for each media item based on the query and sort by score descending.
        """
        query_lower = query.lower()
        for media in self.media_list:
            title = getattr(media, "name", "")
            score = 0 if title is None else difflib.SequenceMatcher(None, query_lower, title.lower()).ratio()
            media.score = score

        self.media_list.sort(key=lambda x: getattr(x, "score", 0), reverse=True)
