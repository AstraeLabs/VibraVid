# 14.10.26

import logging
import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

from rich.console import Console

from VibraVid.services._base import Entries, EntriesManager, site_constants
from VibraVid.services._base.object import Episode, Season, SeasonManager
from VibraVid.services._base.site_search_manager import make_search_entrypoints
from VibraVid.utils import TVShowManager

from .downloader import download_album_tracks, download_song

indice = 20
_useFor = "Song"
console = Console()
logger = logging.getLogger(__name__)
entries_manager = EntriesManager()
table_show_manager = TVShowManager()


@dataclass
class YTMusicTrack:
    video_id: str
    title: str
    artist: str
    album: str
    duration: int
    thumbnail: str
    url: str


_ytmusic_client = None


def _get_ytmusic():
    """Lazy init YTMusic client."""
    global _ytmusic_client
    if _ytmusic_client is None:
        try:
            from ytmusicapi import YTMusic
            _ytmusic_client = YTMusic()
        except Exception as e:
            logger.error(f"[youtube_music] Failed to init ytmusicapi: {e}")
            return None
    return _ytmusic_client


def _search_youtube_music(query: str, limit: int = 25) -> list[YTMusicTrack]:
    """Search YouTube Music via ytmusicapi for tracks."""
    yt = _get_ytmusic()
    if not yt:
        # Fallback to yt-dlp
        return _search_youtube_music_ytdlp(query, limit)

    try:
        results = yt.search(query, filter="songs", limit=limit)
    except Exception as e:
        logger.error(f"[youtube_music] ytmusicapi search failed: {e}")
        return _search_youtube_music_ytdlp(query, limit)

    tracks = []
    for r in results:
        if not r.get("videoId"):
            continue
        video_id = r["videoId"]
        artists = r.get("artists", [])
        artist_name = artists[0]["name"] if artists else r.get("album", {}).get("name", "Unknown")
        tracks.append(YTMusicTrack(
            video_id=video_id,
            title=r.get("title", "Unknown"),
            artist=artist_name,
            album=r.get("album", {}).get("name", ""),
            duration=_parse_duration(r.get("duration", "0:00")),
            thumbnail=r.get("thumbnails", [{}])[-1].get("url", "") if r.get("thumbnails") else "",
            url=f"https://music.youtube.com/watch?v={video_id}",
        ))
    return tracks


def _search_youtube_music_ytdlp(query: str, limit: int = 25) -> list[YTMusicTrack]:
    """Fallback search via yt-dlp."""
    import yt_dlp

    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "extract_flat": "in_playlist",
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            result = ydl.extract_info(f"ytsearch{limit}:{query} music", download=False)
    except Exception as e:
        logger.error(f"[youtube_music] yt-dlp search failed: {e}")
        return []

    tracks = []
    for entry in result.get("entries", [])[:limit]:
        if not entry or not entry.get("id"):
            continue
        tracks.append(YTMusicTrack(
            video_id=entry["id"],
            title=entry.get("title", "Unknown"),
            artist=entry.get("artist", entry.get("uploader", "Unknown")),
            album=entry.get("album", ""),
            duration=entry.get("duration", 0) or 0,
            thumbnail=entry.get("thumbnail", "") or (f"https://i.ytimg.com/vi/{entry['id']}/maxresdefault.jpg" if entry.get("id") else ""),
            url=f"https://music.youtube.com/watch?v={entry['id']}",
        ))
    return tracks


def _search_youtube_music_albums(query: str, limit: int = 10) -> list[dict]:
    """Search YouTube Music albums via ytmusicapi."""
    yt = _get_ytmusic()
    if not yt:
        return _search_youtube_music_albums_ytdlp(query, limit)

    try:
        results = yt.search(query, filter="albums", limit=limit)
    except Exception as e:
        logger.error(f"[youtube_music] ytmusicapi album search failed: {e}")
        return _search_youtube_music_albums_ytdlp(query, limit)

    albums = []
    for r in results:
        browse_id = r.get("browseId")
        if not browse_id:
            continue
        albums.append({
            "id": browse_id,
            "name": r.get("title", "Unknown Album"),
            "artists": r.get("artists", [{}])[0].get("name", "Unknown") if r.get("artists") else "Unknown",
            "release_date": str(r.get("year", "")),
            "cover_url": r.get("thumbnails", [{}])[-1].get("url", "") if r.get("thumbnails") else "",
            "external_url": f"https://music.youtube.com/browse/{browse_id}",
        })
    return albums[:limit]


def _search_youtube_music_albums_ytdlp(query: str, limit: int = 10) -> list[dict]:
    """Fallback album search via yt-dlp."""
    import yt_dlp

    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "extract_flat": "in_playlist",
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            result = ydl.extract_info(f"ytsearch{limit}:{query} album playlist", download=False)
    except Exception as e:
        logger.error(f"[youtube_music] yt-dlp album search failed: {e}")
        return []

    albums = []
    seen = set()
    for entry in result.get("entries", []):
        if not entry or not entry.get("id"):
            continue
        title_lower = entry.get("title", "").lower()
        uploader_lower = entry.get("uploader", "").lower()
        is_album_like = (
            any(kw in title_lower for kw in ["album", "playlist", "full album", "ep", "lp", "mix", "discography", "raccolta", "collection"]) or
            any(kw in uploader_lower for kw in ["topic", "official", "vevo", "records", "music"]) or
            (entry.get("duration", 0) or 0) > 1200
        )
        if is_album_like:
            aid = entry["id"]
            if aid not in seen:
                seen.add(aid)
                albums.append({
                    "id": aid,
                    "name": entry.get("title", "Unknown Album"),
                    "artists": entry.get("artist", entry.get("uploader", "Unknown")),
                    "release_date": "",
                    "cover_url": entry.get("thumbnail", "") or (f"https://i.ytimg.com/vi/{aid}/maxresdefault.jpg" if aid else ""),
                    "external_url": f"https://music.youtube.com/playlist?list={aid}",
                })
    return albums[:limit]


def _parse_duration(duration_str: str) -> int:
    """Parse duration string (e.g., '3:28') to seconds."""
    if not duration_str:
        return 0
    try:
        parts = duration_str.split(":")
        if len(parts) == 2:
            return int(parts[0]) * 60 + int(parts[1])
        elif len(parts) == 3:
            return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
    except (ValueError, IndexError):
        pass
    return 0


def title_search(query: str) -> int:
    """Search YouTube Music for tracks and albums."""
    entries_manager.clear()
    table_show_manager.clear()

    tracks = _search_youtube_music(query, limit=20)
    albums = _search_youtube_music_albums(query, limit=8)

    logger.info(f"[youtube_music] query={query!r} -> {len(tracks)} track(s), {len(albums)} album(s)")

    for t in tracks:
        entry = Entries(
            id=t.video_id,
            name=f"{t.artist} - {t.title}" if t.artist else t.title,
            type="song",
            year="",
            image=t.thumbnail,
            url=t.url,
        )
        entry.title = t.title
        entry.artist = t.artist
        entry.album = t.album
        entry.duration_seconds = t.duration
        entries_manager.add(entry)

    for a in albums:
        entry = Entries(
            id=a["id"],
            name=f"{a['artists']} - {a['name']}" if a['artists'] else a["name"],
            type="album",
            year=a["release_date"],
            image=a["cover_url"],
            url=a["external_url"],
        )
        entry.artist = a["artists"]
        entry.album = a["name"]
        entries_manager.add(entry)

    return len(entries_manager)


def _build_album_scraper(select_title: Entries):
    """Build album tracklist from YouTube Music album (ytmusicapi)."""
    yt = _get_ytmusic()
    if not yt:
        return _build_album_scraper_ytdlp(select_title)

    album_id = str(getattr(select_title, "id", "") or "").strip()
    if not album_id:
        console.print(f"[red]Cannot resolve YouTube Music album id for '{select_title.name}'")
        return None

    try:
        album = yt.get_album(album_id)
    except Exception as e:
        console.print(f"[red]Failed to fetch album '{select_title.name}': {e}")
        logger.error(f"ytmusicapi get_album error: {e}")
        return None

    tracks = album.get("tracks", [])
    if not tracks:
        return None

    scraper_title = album.get("title", "Unknown Album")
    scraper_artist = album.get("artists", [{}])[0].get("name", "") if album.get("artists") else (tracks[0].get("artists", [{}])[0].get("name", "") if tracks and tracks[0].get("artists") else "")
    scraper_cover = album.get("thumbnails", [{}])[-1].get("url", "") if album.get("thumbnails") else ""
    scraper_year = str(album.get("year", ""))

    seasons_manager = SeasonManager()
    season = Season(id=1, number=1, name=scraper_title)
    
    for i, track in enumerate(tracks, start=1):
        episode = {
            "id": track.get("videoId"),
            "name": track.get("title", "Unknown Track"),
            "number": i,
            "url": f"https://music.youtube.com/watch?v={track.get('videoId')}",
            "duration_seconds": _parse_duration(track.get("duration", "0:00")),
            "artist": track.get("artists", [{}])[0].get("name", "") if track.get("artists") else scraper_artist,
            "album": scraper_title,
            "year": scraper_year,
            "cover": scraper_cover,
        }
        season.episodes.add(Episode(**episode))
    
    seasons_manager.add(season)

    class AlbumScraper:
        def __init__(self, title, artist, cover_url, year, seasons_manager):
            self.title = title
            self.artist = artist
            self.cover_url = cover_url
            self.year = year
            self.seasons_manager = seasons_manager
        
        def getEpisodeSeasons(self, season_number: int):
            """Return the tracklist as episode dicts for the shared managers."""
            season = self.seasons_manager.get_season_by_number(season_number)
            if not season:
                return []
            episodes = []
            for ep in season.episodes.episodes:
                episodes.append({
                    "id": ep.id,
                    "name": ep.name,
                    "number": ep.number,
                    "url": ep.url,
                    "duration_seconds": ep.duration_seconds,
                    "artist": ep.artist,
                    "album": self.title,
                    "year": self.year,
                    "cover": ep.cover,
                })
            return episodes

    return AlbumScraper(scraper_title, scraper_artist, scraper_cover, scraper_year, seasons_manager)


def _build_album_scraper_ytdlp(select_title: Entries):
    """Fallback: build album from YouTube playlist via yt-dlp."""
    import yt_dlp

    playlist_id = str(getattr(select_title, "id", "") or "").strip()
    if not playlist_id:
        console.print(f"[red]Cannot resolve YouTube playlist id for '{select_title.name}'")
        return None

    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "extract_flat": "in_playlist",
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            result = ydl.extract_info(f"https://music.youtube.com/playlist?list={playlist_id}", download=False)
    except Exception as e:
        console.print(f"[red]Failed to fetch album '{select_title.name}': {e}")
        logger.error(f"YTMusic album fetch error: {e}")
        return None

    entries = result.get("entries", [])
    if not entries:
        return None

    scraper_title = result.get("title", "Unknown Album")
    scraper_artist = result.get("uploader", "") or (entries[0].get("artist", "") if entries else "")
    scraper_cover = result.get("thumbnail", "") or (entries[0].get("thumbnail", "") if entries else "")

    seasons_manager = SeasonManager()
    season = Season(id=1, number=1, name=scraper_title)
    
    for i, entry in enumerate(entries, start=1):
        episode = {
            "id": entry.get("id"),
            "name": entry.get("title", "Unknown Track"),
            "number": i,
            "url": f"https://music.youtube.com/watch?v={entry.get('id')}",
            "duration_seconds": entry.get("duration", 0) or 0,
            "artist": entry.get("artist", entry.get("uploader", scraper_artist)),
            "album": scraper_title,
            "year": "",
            "cover": scraper_cover,
        }
        season.episodes.add(Episode(**episode))
    
    seasons_manager.add(season)

    class AlbumScraper:
        def __init__(self, title, artist, cover_url, year, seasons_manager):
            self.title = title
            self.artist = artist
            self.cover_url = cover_url
            self.year = year
            self.seasons_manager = seasons_manager
        
        def getEpisodeSeasons(self, season_number: int):
            """Return the tracklist as episode dicts for the shared managers."""
            season = self.seasons_manager.get_season_by_number(season_number)
            if not season:
                return []
            episodes = []
            for ep in season.episodes.episodes:
                episodes.append({
                    "id": ep.id,
                    "name": ep.name,
                    "number": ep.number,
                    "url": ep.url,
                    "duration_seconds": ep.duration_seconds,
                    "artist": ep.artist,
                    "album": self.title,
                    "year": self.year,
                    "cover": ep.cover,
                })
            return episodes

    return AlbumScraper(scraper_title, scraper_artist, scraper_cover, "", seasons_manager)


def download_album(select_title: Entries, season_selection=None, episode_selection=None, scrape_serie=None):
    """Drive the shared season/episode pipeline for a YouTube Music album."""
    start_name = select_title.name if select_title else "Unknown"
    console.print(f"\n[yellow]Download album: [red]{site_constants.SITE_NAME} -> [cyan]{start_name}\n")

    if scrape_serie is None:
        scrape_serie = _build_album_scraper(select_title)
        if scrape_serie is None:
            return (None, False, "Could not load album metadata")

    seasons_count = len(scrape_serie.seasons_manager)

    def _download_episode_callback(season_number: int, download_all: bool, episode_selection=None):
        from VibraVid.services._base.tv_download_manager import process_episode_download
        process_episode_download(
            index_season_selected=season_number,
            scrape_serie=scrape_serie,
            download_video_callback=lambda ep, sn, ei: download_album_tracks(ep, sn, ei, scrape_serie),
            download_all=download_all,
            episode_selection=episode_selection,
        )

    from VibraVid.services._base.tv_download_manager import process_season_selection
    process_season_selection(
        scrape_serie=scrape_serie,
        seasons_count=seasons_count,
        season_selection=season_selection,
        episode_selection=episode_selection,
        download_episode_callback=_download_episode_callback,
    )

    entries_manager.clear()
    table_show_manager.clear()


def process_search_result(select_title, selections=None, scrape_serie=None):
    """Route songs to download_song, albums to the series pipeline."""
    if select_title is None:
        console.print("[yellow]No title selected.")
        return False

    media_type = str(getattr(select_title, "type", "")).lower()

    if media_type == "album":
        if scrape_serie is None:
            scrape_serie = _build_album_scraper(select_title)
            if scrape_serie is None:
                return False

        from VibraVid.services._base.site_search_manager import base_process_search_result
        return base_process_search_result(
            select_title=select_title,
            download_series_func=download_album,
            media_search_manager=entries_manager,
            table_show_manager=table_show_manager,
            selections=selections,
            scrape_serie=scrape_serie,
        )

    from VibraVid.services._base.site_search_manager import base_process_search_result
    return base_process_search_result(
        select_title=select_title,
        download_film_func=download_song,
        media_search_manager=entries_manager,
        table_show_manager=table_show_manager,
        selections=selections,
        scrape_serie=scrape_serie,
    )


search, _ = make_search_entrypoints(
    title_search=title_search,
    entries_manager=entries_manager,
    table_show_manager=table_show_manager,
    process_result=process_search_result,
)