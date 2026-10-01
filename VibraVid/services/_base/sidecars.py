# 01.10.26

import logging
import os
import re
import xml.etree.ElementTree as ET
from datetime import datetime
from typing import NamedTuple

from VibraVid.core.ui.tracker import context_tracker
from VibraVid.services._base.metadata import EpisodeInfo, MovieInfo, Person, SeriesInfo, get_provider
from VibraVid.utils.http_client import create_client

logger = logging.getLogger(__name__)
_XML_HEADER = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
_IMAGE_TIMEOUT = 15
_JPEG_MAGIC = bytes([0xFF, 0xD8, 0xFF])
_PNG_MAGIC = bytes([0x89]) + b"PNG"
_IMAGE_EXTENSIONS = (".jpg", ".png")
_RATING_NAMES = {"tmdb": "themoviedb", "imdb": "imdb", "tvdb": "thetvdb"}
_SEASON_DIR = re.compile(r"^(?:season|stagione|saison|staffel|temporada|s)[\s._-]*\d+$|^specials?$", re.IGNORECASE)


class Sidecars:
    class Target(NamedTuple):
        """What the download being set up should get sidecars from (see :meth:`snapshot`)."""
        provider: str
        media_type: str
        item_id: str
        season: int = 0
        episode: int = 0
        episode_name: str | None = None
        verify: bool = False  # title-only candidate: write only if the provider's episode title matches episode_name

    def __init__(
        self,
        video_path: str,
        provider: str,
        media_type: str,
        item_id: str,
        season: int = 0,
        episode: int = 0,
        episode_name: str | None = None,
        verify: bool = False,
    ):
        self.video_path = video_path
        self.provider = provider
        self.media_type = media_type
        self.item_id = str(item_id)
        self.season = int(season or 0)
        self.episode = int(episode or 0)
        self.episode_name = episode_name
        self.verify = verify
        self.base = os.path.splitext(video_path)[0]
        self.written: list[str] = []

    @classmethod
    def snapshot(cls) -> "Sidecars.Target | None":
        """The :class:`Target` of the download being set up, or None when no sidecar is due (flag off / no usable match). Read at downloader creation, like the poster, because the context moves on to the next item."""
        if not (context_tracker.sidecar_provider and context_tracker.sidecar_id and context_tracker.sidecar_media_type):
            return None
        
        return cls.Target(
            context_tracker.sidecar_provider,
            context_tracker.sidecar_media_type,
            str(context_tracker.sidecar_id),
            context_tracker.season or 0,
            context_tracker.episode or 0,
            context_tracker.episode_name or None,
            context_tracker.sidecar_verify,
        )

    @classmethod
    def write_for(cls, video_path: str, target: "Sidecars.Target | None") -> list[str]:
        """Write the sidecars of *video_path* from a :meth:`snapshot` (no-op for None)."""
        if not target:
            return []
        return cls(video_path, *target).write()

    def write(self) -> list[str]:
        """Write the sidecars and return their paths (empty when nothing could be written)."""
        source = get_provider(self.provider)
        if not (self.video_path and os.path.isfile(self.video_path) and source and source.available()):
            return []

        try:
            if self.media_type == "movie":
                info = source.movie(self.item_id)
                if info:
                    self._write_movie(info)

            elif self.media_type == "tv" and self.season > 0 and self.episode > 0:
                info = source.episode(self.item_id, self.season, self.episode)
                if info and self._episode_confirmed(source, info):
                    self._write_episode(info)
                    self._write_series(source, info)

        except Exception as exc:
            logger.warning(f"sidecars failed for {os.path.basename(self.video_path)}: {exc}")

        if self.written:
            logger.info(f"sidecars written ({self.provider}): {', '.join(os.path.basename(p) for p in self.written)}")
        return self.written

    def _episode_confirmed(self, source, info: EpisodeInfo) -> bool:
        """For a title-only series candidate the episode title is the proof that both the series and the season/episode numbering are right."""
        if not self.verify:
            return True
        
        if self.episode_name and source.titles_match([info.title], self.episode_name):
            return True
        
        logger.info(f"sidecars skipped for {os.path.basename(self.video_path)}: {self.provider} S{self.season:02d}E{self.episode:02d} is '{info.title}', the site says '{self.episode_name}'")
        return False

    # ── film ──────────────────────────────────────────────────────────────
    def _write_movie(self, info: MovieInfo) -> None:
        root = ET.Element("movie")
        self._text(root, "title", info.title)
        self._text(root, "originaltitle", info.original_title)
        self._text(root, "sorttitle", info.title)
        self._text(root, "year", info.year)
        self._text(root, "premiered", info.premiered)
        self._ratings(root, info.rating, info.votes)
        self._text(root, "tagline", info.tagline)
        self._text(root, "plot", info.plot)
        self._text(root, "runtime", info.runtime)
        self._text(root, "mpaa", info.mpaa)
        self._ids(root, info.ids)
        self._text(root, "id", info.ids.get("imdb"))  # legacy Kodi tag
        self._texts(root, "genre", info.genres)
        self._texts(root, "tag", info.tags)
        self._texts(root, "country", info.countries)
        self._texts(root, "studio", info.studios)
        if info.collection:
            collection = ET.SubElement(root, "set")
            self._text(collection, "name", info.collection)
            self._text(collection, "overview", info.collection_overview)
        
        self._texts(root, "director", info.directors)
        self._texts(root, "credits", info.writers)
        self._art(root, info.image_url, info.fanart_url)
        self._text(root, "trailer", info.trailer_url)
        self._actors(root, info.actors)
        self._text(root, "dateadded", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))

        self._write_xml(root, self.base + ".nfo")
        self._write_image(info.image_url, self.base + "-poster")
        self._write_image(info.fanart_url, self.base + "-fanart")

    # ── episode ───────────────────────────────────────────────────────────
    def _write_episode(self, info: EpisodeInfo) -> None:
        root = ET.Element("episodedetails")
        self._text(root, "title", info.title)
        self._text(root, "showtitle", info.show_title)
        self._text(root, "season", info.season)
        self._text(root, "episode", info.episode)
        self._text(root, "aired", info.aired)
        self._text(root, "plot", info.plot)
        self._text(root, "runtime", info.runtime)
        self._ratings(root, info.rating, info.votes)
        self._ids(root, info.ids)
        self._texts(root, "director", info.directors)
        self._texts(root, "credits", info.writers)
        self._text(root, "thumb", info.image_url)
        self._actors(root, info.actors)
        self._text(root, "dateadded", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))

        self._write_xml(root, self.base + ".nfo")
        self._write_image(info.image_url, self.base + "-thumb")

    # ── series (shared by every episode of the show; written once) ────────

    def _series_dir(self, show_title: str | None, source) -> str | None:
        """The show's folder: the parent of the season folder, or the episode's own folder when it carries the show's name. None = not sure, write nothing."""
        parent = os.path.dirname(os.path.abspath(self.video_path))
        if _SEASON_DIR.match(os.path.basename(parent)):
            return os.path.dirname(parent)
        if show_title and source.titles_match([os.path.basename(parent)], show_title):
            return parent
        return None

    @staticmethod
    def _image_exists(path_without_extension: str) -> bool:
        return any(os.path.exists(path_without_extension + ext) for ext in _IMAGE_EXTENSIONS)

    def _write_series(self, source, episode: EpisodeInfo) -> None:
        series_dir = self._series_dir(episode.show_title, source)
        if not series_dir:
            logger.debug(f"sidecars: no show folder recognised for {self.video_path}, skipping tvshow files")
            return

        season_stem = "season-specials-poster" if episode.season == 0 else f"season{episode.season:02d}-poster"
        nfo = os.path.join(series_dir, "tvshow.nfo")
        stems = {name: os.path.join(series_dir, name) for name in ("poster", "fanart", season_stem)}
        missing = {name: stem for name, stem in stems.items() if not self._image_exists(stem)}
        if os.path.exists(nfo) and not missing:
            return

        info = source.series(self.item_id)
        if not info:
            return
        if not os.path.exists(nfo):
            self._write_tvshow_nfo(info, nfo)
        if "poster" in missing:
            self._write_image(info.image_url, missing["poster"])
        if "fanart" in missing:
            self._write_image(info.fanart_url, missing["fanart"])
        if season_stem in missing:
            self._write_image(info.season_posters.get(episode.season), missing[season_stem])

    def _write_tvshow_nfo(self, info: SeriesInfo, path: str) -> None:
        root = ET.Element("tvshow")
        self._text(root, "title", info.title)
        self._text(root, "originaltitle", info.original_title)
        self._text(root, "showtitle", info.title)
        self._text(root, "sorttitle", info.title)
        self._text(root, "year", info.year)
        self._text(root, "premiered", info.premiered)
        self._text(root, "status", info.status)
        self._ratings(root, info.rating, info.votes)
        self._text(root, "plot", info.plot)
        self._text(root, "mpaa", info.mpaa)
        self._text(root, "runtime", info.runtime)
        self._ids(root, info.ids)
        self._texts(root, "genre", info.genres)
        self._texts(root, "studio", info.studios)

        if info.image_url:
            ET.SubElement(root, "thumb", {"aspect": "poster", "season": "-1"}).text = info.image_url
        for number, url in sorted(info.season_posters.items()):
            ET.SubElement(root, "thumb", {"aspect": "poster", "season": str(number)}).text = url
        if info.fanart_url:
            ET.SubElement(ET.SubElement(root, "fanart"), "thumb").text = info.fanart_url
        
        self._actors(root, info.actors)
        self._write_xml(root, path)

    # ── XML pieces ────────────────────────────────────────────────────────

    @staticmethod
    def _text(parent: ET.Element, tag: str, value) -> None:
        """Add <tag>value</tag>, skipping empty values."""
        if value is None or str(value).strip() == "":
            return
        ET.SubElement(parent, tag).text = str(value).strip()

    @classmethod
    def _texts(cls, parent: ET.Element, tag: str, values) -> None:
        for value in values or []:
            cls._text(parent, tag, value)

    def _ratings(self, root: ET.Element, rating, votes) -> None:
        """Kodi's ``<ratings>`` block plus the plain ``<rating>`` Jellyfin and older readers use."""
        if not rating:
            return
        block = ET.SubElement(ET.SubElement(root, "ratings"), "rating", {"name": _RATING_NAMES.get(self.provider, self.provider), "max": "10", "default": "true"})
        self._text(block, "value", f"{float(rating):.1f}")
        self._text(block, "votes", votes)
        self._text(root, "rating", f"{float(rating):.1f}")

    def _ids(self, root: ET.Element, ids: dict[str, str]) -> None:
        """Kodi ``uniqueid`` (the matching provider's id is the default) plus the legacy ``tmdbid``/``imdbid``/``tvdbid`` tags Jellyfin also reads."""
        ordered = sorted(ids.items(), key=lambda kv: kv[0] != self.provider)
        for index, (kind, value) in enumerate(ordered):
            attributes = {"type": kind}
            if index == 0:
                attributes["default"] = "true"
            ET.SubElement(root, "uniqueid", attributes).text = str(value)
            ET.SubElement(root, f"{kind}id").text = str(value)

    @classmethod
    def _actors(cls, root: ET.Element, people: list[Person]) -> None:
        for order, person in enumerate(people or []):
            actor = ET.SubElement(root, "actor")
            cls._text(actor, "name", person.name)
            cls._text(actor, "role", person.role)
            cls._text(actor, "order", order)
            cls._text(actor, "thumb", person.image_url)

    @staticmethod
    def _art(root: ET.Element, poster: str | None, fanart: str | None) -> None:
        if poster:
            ET.SubElement(root, "thumb", {"aspect": "poster"}).text = poster
        if fanart:
            ET.SubElement(ET.SubElement(root, "fanart"), "thumb").text = fanart

    def _write_xml(self, root: ET.Element, path: str) -> None:
        ET.indent(root)
        self._atomic_write(path, (_XML_HEADER + ET.tostring(root, encoding="unicode") + "\n").encode("utf-8"))

    # ── images ────────────────────────────────────────────────────────────

    @staticmethod
    def _image_extension(data: bytes) -> str | None:
        """File extension matching the image's real format (Kodi/Jellyfin expect jpg or png), None for anything else (e.g. WebP)."""
        if data.startswith(_JPEG_MAGIC):
            return ".jpg"
        if data.startswith(_PNG_MAGIC):
            return ".png"
        return None

    def _write_image(self, url: str | None, path_without_extension: str) -> None:
        if not url:
            return
        try:
            # The browser-impersonating client advertises WebP and the CDNs then serve WebP, so ask for JPEG.
            with create_client(timeout=_IMAGE_TIMEOUT, follow_redirects=True) as client:
                response = client.get(url, headers={"Accept": "image/jpeg,image/png;q=0.9,*/*;q=0.1"})
            extension = self._image_extension(response.content or b"")
            if response.status_code != 200 or not extension:
                logger.warning(f"sidecar image {url} skipped (status {response.status_code}, format not jpg/png)")
                return
            self._atomic_write(path_without_extension + extension, response.content)
        except Exception as exc:
            logger.warning(f"sidecar image {url} failed: {exc}")

    def _atomic_write(self, path: str, data: bytes) -> None:
        """Write via a temp file so an interrupted run never leaves a truncated sidecar."""
        tmp = path + ".tmp"
        with open(tmp, "wb") as handle:
            handle.write(data)
        os.replace(tmp, path)
        self.written.append(path)
