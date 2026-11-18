# 16.04.24

import base64
import logging
from pathlib import Path

from mutagen.flac import FLAC, Picture
from mutagen.id3 import (
    APIC,
    ID3,
    TALB,
    TCON,
    TDRC,
    TIT2,
    TPE1,
    TPE2,
    TRCK,
    USLT,
    ID3NoHeaderError,
)
from mutagen.mp4 import MP4, MP4Cover
from mutagen.oggopus import OggOpus

from VibraVid.utils.http_client import fetch_image_bytes

logger = logging.getLogger(__name__)

_ARTIST_SEPARATORS = (", ", " feat. ", " ft. ", " featuring ", " & ", " x ", " / ")


def _primary_artist(artist: str) -> str:
    """Reduce a possibly multi-artist string ("Drake, 21 Savage") to its first/main artist."""
    if not artist:
        return artist
    best = artist
    for sep in _ARTIST_SEPARATORS:
        head = artist.split(sep, 1)[0].strip()
        if len(head) < len(best):
            best = head
    return best or artist


def _fetch_cover(url: str) -> bytes | None:
    """Download cover image bytes from a URL."""
    return fetch_image_bytes(url)


def _tag_flac(
    path: Path,
    title: str,
    artist: str,
    album: str,
    year: str,
    track_number: int | None,
    genre: str,
    cover_url: str | None,
    album_artist: str = "",
    lyrics: str | None = None,
) -> None:
    """Write Vorbis comment tags + cover art to a FLAC file."""
    audio = FLAC(str(path))
    audio["title"] = [title]
    audio["artist"] = [artist]
    audio["albumartist"] = [album_artist or _primary_artist(artist)]

    if album:
        audio["album"] = [album]
    if year:
        audio["date"] = [str(year)]
    if track_number is not None:
        audio["tracknumber"] = [str(track_number)]
    if genre:
        audio["genre"] = [genre]
    if lyrics:
        audio["lyrics"] = [lyrics]

    if cover_url:
        cover_data = _fetch_cover(cover_url)
        if cover_data:
            pic = Picture()
            pic.type = 3  # 3 = Cover (front)
            pic.mime = "image/jpeg"
            pic.desc = "Cover"
            pic.data = cover_data
            audio.clear_pictures()
            audio.add_picture(pic)
            logger.info("Cover art embedded (FLAC): %s", cover_url)
        else:
            logger.warning("Cover download returned empty bytes (FLAC)")

    audio.save()


def _tag_mp4(
    path: Path,
    title: str,
    artist: str,
    album: str,
    year: str,
    track_number: int | None,
    genre: str,
    cover_url: str | None,
    album_artist: str = "",
    lyrics: str | None = None,
) -> None:
    """Write atom tags + cover art to an M4A/MP4/AAC file."""
    audio = MP4(str(path))
    if audio.tags is None:
        audio.add_tags()
    tags = audio.tags

    tags["\xa9nam"] = [title]
    tags["\xa9ART"] = [artist]
    tags["aART"] = [album_artist or _primary_artist(artist)]

    if album:
        tags["\xa9alb"] = [album]
    if year:
        tags["\xa9day"] = [str(year)]
    if track_number is not None:
        tags["trkn"] = [(int(track_number), 0)]
    if genre:
        tags["\xa9gen"] = [genre]
    if lyrics:
        tags["\xa9lyr"] = [lyrics]

    if cover_url:
        cover_data = _fetch_cover(cover_url)
        if cover_data:
            tags["covr"] = [MP4Cover(cover_data, imageformat=MP4Cover.FORMAT_JPEG)]
            logger.info("Cover art embedded (MP4): %s", cover_url)
        else:
            logger.warning("Cover download returned empty bytes (MP4)")

    audio.save()


def _tag_opus(
    path: Path,
    title: str,
    artist: str,
    album: str,
    year: str,
    track_number: int | None,
    genre: str,
    cover_url: str | None,
    album_artist: str = "",
    lyrics: str | None = None,
) -> None:
    """Write Vorbis comment tags + cover art to an Opus file."""
    audio = OggOpus(str(path))
    if audio.tags is None:
        audio.add_tags()

    audio["title"] = [title]
    audio["artist"] = [artist]
    audio["albumartist"] = [album_artist or _primary_artist(artist)]
    if album:
        audio["album"] = [album]
    if year:
        audio["date"] = [str(year)]
    if track_number is not None:
        audio["tracknumber"] = [str(track_number)]
    if genre:
        audio["genre"] = [genre]
    if lyrics:
        audio["lyrics"] = [lyrics]

    if cover_url:
        cover_data = _fetch_cover(cover_url)
        if cover_data:
            pic = Picture()
            pic.type = 3
            pic.mime = "image/jpeg"
            pic.desc = "Cover"
            pic.data = cover_data
            audio["metadata_block_picture"] = [base64.b64encode(pic.write()).decode("ascii")]
            logger.info("Cover art embedded (Opus): %s", cover_url)
        else:
            logger.warning("Cover download returned empty bytes (Opus)")

    audio.save()


def _tag_mp3(
    path: Path,
    title: str,
    artist: str,
    album: str,
    year: str,
    track_number: int | None,
    genre: str,
    cover_url: str | None,
    album_artist: str = "",
    lyrics: str | None = None,
) -> None:
    """Write ID3 tags + cover art to an MP3 file."""
    try:
        tags = ID3(str(path))
    except ID3NoHeaderError:
        tags = ID3()

    tags.delall("TIT2")
    tags["TIT2"] = TIT2(encoding=3, text=title)
    tags.delall("TPE1")
    tags["TPE1"] = TPE1(encoding=3, text=artist)
    tags.delall("TPE2")
    tags["TPE2"] = TPE2(encoding=3, text=album_artist or _primary_artist(artist))

    if album:
        tags.delall("TALB")
        tags["TALB"] = TALB(encoding=3, text=album)
    if year:
        tags.delall("TDRC")
        tags["TDRC"] = TDRC(encoding=3, text=str(year))
    if track_number is not None:
        tags.delall("TRCK")
        tags["TRCK"] = TRCK(encoding=3, text=str(track_number))
    if genre:
        tags.delall("TCON")
        tags["TCON"] = TCON(encoding=3, text=genre)
    if lyrics:
        tags.delall("USLT")
        tags["USLT::eng"] = USLT(encoding=3, lang="eng", desc="", text=lyrics)

    if cover_url:
        cover_data = _fetch_cover(cover_url)
        if cover_data:
            tags.delall("APIC")
            tags["APIC"] = APIC(
                encoding=3,
                mime="image/jpeg",
                type=3,
                desc="Cover",
                data=cover_data,
            )
            logger.info("Cover art embedded (MP3): %s", cover_url)
        else:
            logger.warning("Cover download returned empty bytes (MP3)")

    tags.save(str(path), v2_version=3)


def tag_track(
    file_path: str,
    title: str,
    artist: str,
    album: str = "",
    year: str = "",
    track_number: int | None = None,
    genre: str = "",
    cover_url: str | None = None,
    album_artist: str = "",
    lyrics: str | None = None,
) -> bool:
    """
    Write tags (+ optional cover art + optional lyrics) to an MP3, FLAC, or M4A/AAC file.

    Routing:
        .flac          → mutagen.flac.FLAC  (Vorbis comments + Picture block)
        .mp3           → mutagen.id3.ID3
        .m4a / .mp4    → mutagen.mp4.MP4

    `artist` may legitimately list every contributor on the track (e.g. "Drake, 21 Savage").
    `album_artist`, if not given explicitly, is reduced to the single primary artist so
    players don't group the album under a different combo per track.

    `lyrics` is written as-is: pass LRC-format text (`[mm:ss.xx]line`) for
    synced lyrics, or plain newline-joined text for unsynced -- both are valid
    in the same native tag (©lyr / LYRICS / USLT) and most players render
    either correctly.

    Returns True on success.
    """
    path = Path(file_path)
    if not path.exists():
        logger.error("File not found for tagging: %s", file_path)
        return False

    suffix = path.suffix.lower()

    try:
        if suffix == ".flac":
            _tag_flac(path, title, artist, album, year, track_number, genre, cover_url, album_artist, lyrics)

        elif suffix == ".mp3":
            _tag_mp3(path, title, artist, album, year, track_number, genre, cover_url, album_artist, lyrics)

        elif suffix == ".opus":
            _tag_opus(path, title, artist, album, year, track_number, genre, cover_url, album_artist, lyrics)

        else:
            # .m4a, .mp4, .aac, …
            _tag_mp4(path, title, artist, album, year, track_number, genre, cover_url, album_artist, lyrics)

        logger.info("Tags written to: %s", path.name)
        return True

    except Exception as exc:
        logger.error("Tagging failed for %s: %s", path.name, exc, exc_info=True)
        return False
