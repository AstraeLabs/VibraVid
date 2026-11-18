# 01.10.26

from VibraVid.provider.tmdb import tmdb_client
from VibraVid.services._base import tmdb_artwork

from .base import BaseMetadataProvider, EpisodeInfo, MovieInfo, Person, SeriesInfo

_MAX_ACTORS = 15
_WRITER_JOBS = ("Writer", "Screenplay", "Story")
_ONGOING = {"Returning Series", "In Production", "Planned", "Pilot"}


class TmdbProvider(BaseMetadataProvider):
    NAME = "tmdb"

    def available(self) -> bool:
        return bool(tmdb_client.api_key)

    def find(self, media_type, name, slug, year, site_tmdb_id):
        tmdb_id = tmdb_artwork.resolve_tmdb_id_near_year(media_type, site_tmdb_id, name, slug, year)
        if tmdb_id and tmdb_artwork.is_trusted_match(media_type, tmdb_id, site_tmdb_id, name, slug, year):
            return str(tmdb_id)
        return None

    def find_by_title(self, media_type, name):
        if not name:
            return None
        search = tmdb_client._search_movie_with_fallback if media_type == "movie" else tmdb_client._search_tv_with_fallback
        keys = ("title", "original_title") if media_type == "movie" else ("name", "original_name")
        return self.unique_match(search(name.replace("-", " "), "it"), name, lambda r: [r.get(k) for k in keys], lambda r: r.get("id"))

    @staticmethod
    def _actors(credits: dict | None, key: str = "cast") -> list[Person]:
        people = sorted((credits or {}).get(key) or [], key=lambda p: p.get("order", 999))
        return [
            Person(p["name"], p.get("character") or None, tmdb_client._image_url(p.get("profile_path"), "w185"))
            for p in people[:_MAX_ACTORS]
            if p.get("name")
        ]

    @staticmethod
    def _crew(credits: dict | None, jobs: tuple[str, ...]) -> list[str]:
        names = [c["name"] for c in (credits or {}).get("crew") or [] if c.get("job") in jobs and c.get("name")]
        return list(dict.fromkeys(names))  # unique, in order

    # ── film ──────────────────────────────────────────────────────────────
    def movie(self, item_id):
        details = tmdb_client._make_request(
            f"movie/{item_id}", {"language": "it", "append_to_response": "external_ids,credits,keywords,release_dates,videos"}
        )
        if not details:
            return None
        
        ids = {"tmdb": str(item_id)}
        imdb_id = details.get("imdb_id") or (details.get("external_ids") or {}).get("imdb_id")
        if imdb_id:
            ids["imdb"] = imdb_id

        collection = details.get("belongs_to_collection") or {}
        collection_overview = None
        if collection.get("id"):
            collection_overview = (tmdb_client._make_request(f"collection/{collection['id']}", {"language": "it"}) or {}).get("overview") or None

        credits = details.get("credits")
        return MovieInfo(
            title=details.get("title"),
            original_title=details.get("original_title"),
            year=tmdb_artwork.year_of(details.get("release_date")),
            premiered=details.get("release_date") or None,
            plot=details.get("overview"),
            tagline=details.get("tagline"),
            runtime=details.get("runtime") or None,
            genres=[g["name"] for g in details.get("genres") or [] if g.get("name")],
            rating=details.get("vote_average") or None,
            votes=details.get("vote_count") or None,
            mpaa=self._certification(details.get("release_dates")),
            tags=[k["name"] for k in (details.get("keywords") or {}).get("keywords") or [] if k.get("name")],
            countries=[c["name"] for c in details.get("production_countries") or [] if c.get("name")],
            studios=[c["name"] for c in details.get("production_companies") or [] if c.get("name")],
            collection=collection.get("name"),
            collection_overview=collection_overview,
            directors=self._crew(credits, ("Director",)),
            writers=self._crew(credits, _WRITER_JOBS),
            actors=self._actors(credits),
            trailer_url=self._trailer(details.get("videos")),
            ids=ids,
            image_url=tmdb_client._image_url(details.get("poster_path"), "w780"),
            fanart_url=tmdb_client._image_url(details.get("backdrop_path"), "w1280"),
        )

    @staticmethod
    def _certification(release_dates: dict | None) -> str | None:
        """Age certification: the Italian one if TMDB has it, else the US one."""
        by_country = {r.get("iso_3166_1"): r.get("release_dates") or [] for r in (release_dates or {}).get("results") or []}
        for country in ("IT", "US"):
            for entry in by_country.get(country, []):
                if entry.get("certification"):
                    return entry["certification"]
        return None

    @staticmethod
    def _trailer(videos: dict | None) -> str | None:
        for video in (videos or {}).get("results") or []:
            if video.get("type") == "Trailer" and video.get("site") == "YouTube" and video.get("key"):
                return f"https://www.youtube.com/watch?v={video['key']}"
        return None

    # ── episode ───────────────────────────────────────────────────────────
    def episode(self, series_id, season, episode):
        season, episode = tmdb_client.resolve_actual_season_episode(int(series_id), season, episode)
        show = tmdb_client._make_request(f"tv/{series_id}", {"language": "it"})
        details = tmdb_client._make_request(
            f"tv/{series_id}/season/{season}/episode/{episode}", {"language": "it", "append_to_response": "credits"}
        )

        if not details or not details.get("name"):
            return None
        
        credits = details.get("credits") or {}
        guests = [
            Person(p["name"], p.get("character") or None, tmdb_client._image_url(p.get("profile_path"), "w185"))
            for p in (details.get("guest_stars") or credits.get("guest_stars") or [])[:_MAX_ACTORS]
            if p.get("name")
        ]

        crew = {"crew": details.get("crew") or credits.get("crew") or []}
        return EpisodeInfo(
            title=details["name"],
            show_title=show.get("name"),
            season=season,
            episode=episode,
            plot=details.get("overview"),
            aired=details.get("air_date"),
            runtime=details.get("runtime") or None,
            rating=details.get("vote_average") or None,
            votes=details.get("vote_count") or None,
            directors=self._crew(crew, ("Director",)),
            writers=self._crew(crew, _WRITER_JOBS),
            actors=guests,
            ids={"tmdb": str(details["id"])} if details.get("id") else {},
            image_url=tmdb_client._image_url(details.get("still_path"), "w780"),
        )

    # ── series ────────────────────────────────────────────────────────────

    def series(self, series_id):
        details = tmdb_client._make_request(f"tv/{series_id}", {"language": "it", "append_to_response": "external_ids,credits,content_ratings"})
        if not details or not details.get("name"):
            return None
        
        ids = {"tmdb": str(series_id)}
        external = details.get("external_ids") or {}
        if external.get("imdb_id"):
            ids["imdb"] = external["imdb_id"]
        if external.get("tvdb_id"):
            ids["tvdb"] = str(external["tvdb_id"])

        ratings = {r.get("iso_3166_1"): r.get("rating") for r in (details.get("content_ratings") or {}).get("results") or [] if r.get("rating")}
        runtimes = details.get("episode_run_time") or []
        return SeriesInfo(
            title=details["name"],
            original_title=details.get("original_name"),
            year=tmdb_artwork.year_of(details.get("first_air_date")),
            premiered=details.get("first_air_date") or None,
            status="Continuing" if details.get("status") in _ONGOING else "Ended" if details.get("status") else None,
            plot=details.get("overview"),
            mpaa=ratings.get("IT") or ratings.get("US") or next(iter(ratings.values()), None),
            runtime=runtimes[0] if runtimes else None,
            rating=details.get("vote_average") or None,
            votes=details.get("vote_count") or None,
            genres=[g["name"] for g in details.get("genres") or [] if g.get("name")],
            studios=[n["name"] for n in details.get("networks") or [] if n.get("name")],
            actors=self._actors(details.get("credits")),
            ids=ids,
            image_url=tmdb_client._image_url(details.get("poster_path"), "w780"),
            fanart_url=tmdb_client._image_url(details.get("backdrop_path"), "w1280"),
            season_posters={
                s["season_number"]: tmdb_client._image_url(s["poster_path"], "w780")
                for s in details.get("seasons") or []
                if s.get("poster_path") and s.get("season_number") is not None
            },
        )
