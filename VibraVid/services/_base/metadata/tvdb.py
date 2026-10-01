# 01.10.26

import re

from VibraVid.provider.tvdb import tvdb_client
from VibraVid.services._base import tmdb_artwork

from .base import BaseMetadataProvider, EpisodeInfo, MovieInfo, Person, SeriesInfo

_TVDB_IMAGE_HOST = "https://artworks.thetvdb.com"
_ISO_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
_MAX_ACTORS = 15
_POSTER = {"movie": 14, "tv": 2}
_BACKGROUND = {"movie": 15, "tv": 3}


class TvdbProvider(BaseMetadataProvider):
    NAME = "tvdb"

    def available(self) -> bool:
        return bool(tvdb_client.api_key)

    def find(self, media_type, name, slug, year, site_tmdb_id):
        imdb_id = self.imdb_id_of_site_tmdb(media_type, site_tmdb_id)
        if imdb_id:
            found = tvdb_client.find_by_remote_id(imdb_id, media_type)
            if found:
                return str(found)

        wanted_year = tmdb_artwork.year_of(year)
        if not name or wanted_year is None:
            return None
        
        best = self.best_match(
            [hit for hit in tvdb_client.search(name, media_type) if hit.get("tvdb_id")],
            name,
            wanted_year,
            titles_of=lambda hit: [hit.get("name"), *(hit.get("aliases") or [])],
            year_of=lambda hit: hit.get("year"),
        )
        return str(best["tvdb_id"]) if best else None

    def find_by_title(self, media_type, name):
        if not name:
            return None
        
        return self.unique_match(
            tvdb_client.search(name, media_type), name, lambda hit: [hit.get("name"), *(hit.get("aliases") or [])], lambda hit: hit.get("tvdb_id")
        )

    @staticmethod
    def _ids(remote_ids, tvdb_id) -> dict[str, str]:
        ids = {"tvdb": str(tvdb_id)}
        for entry in remote_ids or []:
            source = (entry.get("sourceName") or "").lower()
            if source == "imdb" and entry.get("id"):
                ids["imdb"] = str(entry["id"])
            elif source.startswith("themoviedb") and str(entry.get("id") or "").isdigit():
                ids["tmdb"] = str(entry["id"])
        return ids

    @staticmethod
    def _image(path) -> str | None:
        """TVDB gives either a full URL or a path under its artwork host."""
        if not path:
            return None
        path = str(path)
        return path if path.startswith("http") else _TVDB_IMAGE_HOST + (path if path.startswith("/") else "/" + path)

    @classmethod
    def _artwork(cls, detail: dict, type_id: int) -> str | None:
        """URL of the best-scored artwork of that TVDB type."""
        candidates = [a for a in detail.get("artworks") or [] if a.get("type") == type_id and a.get("image")]
        best = max(candidates, key=lambda a: a.get("score") or 0, default=None)
        return cls._image(best["image"]) if best else None

    @staticmethod
    def _people(detail: dict, kind: str) -> list[str]:
        names = [c.get("personName") for c in detail.get("characters") or [] if c.get("peopleType") == kind and c.get("personName")]
        return list(dict.fromkeys(names))

    @classmethod
    def _actors(cls, detail: dict) -> list[Person]:
        cast = [c for c in detail.get("characters") or [] if c.get("peopleType") == "Actor" and c.get("personName")]
        cast.sort(key=lambda c: c.get("sort") if c.get("sort") is not None else 999)
        return [Person(c["personName"], c.get("name") or None, cls._image(c.get("personImgURL"))) for c in cast[:_MAX_ACTORS]]

    @staticmethod
    def _content_rating(detail: dict) -> str | None:
        """Age rating: Italy's if TVDB has it, else the US one."""
        by_country = {r.get("country"): r.get("name") for r in detail.get("contentRatings") or [] if r.get("name")}
        return by_country.get("ita") or by_country.get("usa")

    @staticmethod
    def _companies(detail: dict, *kinds: str) -> list[str]:
        companies = detail.get("companies")
        if isinstance(companies, dict):
            names = [c.get("name") if isinstance(c, dict) else c for kind in kinds for c in companies.get(kind) or []]
        else:
            names = [c.get("name") for c in companies or [] if isinstance(c, dict)]
        return list(dict.fromkeys(n for n in names if n))

    # ── film ──────────────────────────────────────────────────────────────
    def movie(self, item_id):
        detail = tvdb_client.extended(item_id, "movie", full=True)
        if not detail or not detail.get("name"):
            return None
        
        translated = tvdb_client.translation("movies", item_id)
        release = detail.get("first_release")
        premiered = release.get("date") if isinstance(release, dict) else None
        trailer = next((t.get("url") for t in detail.get("trailers") or [] if t.get("url")), None)
        country = detail.get("originalCountry")
        return MovieInfo(
            title=translated.get("name") or detail["name"],
            original_title=detail["name"],
            year=tmdb_artwork.year_of(detail.get("year")),
            premiered=premiered if premiered and _ISO_DATE.fullmatch(str(premiered)) else None,
            plot=translated.get("overview"),
            runtime=detail.get("runtime") or None,
            genres=[g["name"] for g in detail.get("genres") or [] if g.get("name")],
            mpaa=self._content_rating(detail),
            countries=[str(country).upper()] if country else [],
            studios=self._companies(detail, "studio", "production"),
            directors=self._people(detail, "Director"),
            writers=self._people(detail, "Writer"),
            actors=self._actors(detail),
            trailer_url=trailer,
            ids=self._ids(detail.get("remoteIds"), item_id),
            image_url=self._image(detail.get("image")) or self._artwork(detail, _POSTER["movie"]),
            fanart_url=self._artwork(detail, _BACKGROUND["movie"]),
        )

    # ── episode ───────────────────────────────────────────────────────────
    def episode(self, series_id, season, episode):
        match = next(
            (e for e in tvdb_client.episodes(series_id) if e.get("seasonNumber") == int(season) and e.get("number") == int(episode)),
            None,
        )

        if not match or not match.get("id"):
            return None
        
        translated = tvdb_client.translation("episodes", match["id"])
        title = translated.get("name") or match.get("name")
        if not title:
            return None
        
        show_title = tvdb_client.translation("series", series_id).get("name") or (tvdb_client.extended(series_id, "tv") or {}).get("name")
        return EpisodeInfo(
            title=title,
            show_title=show_title,
            season=int(season),
            episode=int(episode),
            plot=translated.get("overview") or match.get("overview"),
            aired=match.get("aired"),
            runtime=match.get("runtime") or None,
            ids={"tvdb": str(match["id"])},
            image_url=self._image(match.get("image")),
        )

    # ── series ────────────────────────────────────────────────────────────
    def series(self, series_id):
        detail = tvdb_client.extended(series_id, "tv", full=True)
        if not detail or not detail.get("name"):
            return None
        
        translated = tvdb_client.translation("series", series_id)
        status = (detail.get("status") or {}).get("name")
        network = detail.get("originalNetwork") or detail.get("latestNetwork") or {}
        return SeriesInfo(
            title=translated.get("name") or detail["name"],
            original_title=detail["name"],
            year=tmdb_artwork.year_of(detail.get("year") or detail.get("firstAired")),
            premiered=detail.get("firstAired") if _ISO_DATE.fullmatch(str(detail.get("firstAired") or "")) else None,
            status="Continuing" if status == "Continuing" else "Ended" if status else None,
            plot=translated.get("overview"),
            mpaa=self._content_rating(detail),
            runtime=detail.get("averageRuntime") or None,
            genres=[g["name"] for g in detail.get("genres") or [] if g.get("name")],
            studios=[network["name"]] if isinstance(network, dict) and network.get("name") else [],
            actors=self._actors(detail),
            ids=self._ids(detail.get("remoteIds"), series_id),
            image_url=self._image(detail.get("image")) or self._artwork(detail, _POSTER["tv"]),
            fanart_url=self._artwork(detail, _BACKGROUND["tv"]),
        )
