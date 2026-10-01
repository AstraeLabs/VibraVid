# 01.10.26

from VibraVid.provider.imdb import imdb_client
from VibraVid.services._base import tmdb_artwork

from .base import BaseMetadataProvider, EpisodeInfo, MovieInfo, Person, SeriesInfo


def _edges(node: dict, key: str) -> list[dict]:
    """The ``node`` of every edge of ``node[key]`` (a GraphQL connection), tolerating nulls."""
    return [edge.get("node") or {} for edge in (node.get(key) or {}).get("edges") or []]


class ImdbProvider(BaseMetadataProvider):
    NAME = "imdb"

    def find(self, media_type, name, slug, year, site_tmdb_id):
        mapped = self.imdb_id_of_site_tmdb(media_type, site_tmdb_id)
        if mapped:
            return mapped

        wanted_year = tmdb_artwork.year_of(year)
        if not name or wanted_year is None:
            return None
        
        best = self.best_match(
            imdb_client.search_titles(name, media_type, wanted_year),
            name,
            wanted_year,
            titles_of=lambda node: [(node.get("titleText") or {}).get("text"), (node.get("originalTitleText") or {}).get("text")],
            year_of=lambda node: (node.get("releaseYear") or {}).get("year"),
        )
        return best["id"] if best else None

    def find_by_title(self, media_type, name):
        if not name:
            return None
        
        return self.unique_match(
            imdb_client.search_titles(name, media_type),
            name,
            lambda node: [(node.get("titleText") or {}).get("text"), (node.get("originalTitleText") or {}).get("text")],
            lambda node: node.get("id"),
        )

    @staticmethod
    def _names(node: dict, key: str) -> list[str]:
        names = [((n.get("name") or {}).get("nameText") or {}).get("text") for n in _edges(node, key)]
        return list(dict.fromkeys(n for n in names if n))

    @staticmethod
    def _actors(node: dict) -> list[Person]:
        people = []
        for entry in _edges(node, "cast"):
            name = ((entry.get("name") or {}).get("nameText") or {}).get("text")
            if not name:
                continue
            characters = [c.get("name") for c in entry.get("characters") or [] if c.get("name")]
            people.append(Person(name, ", ".join(characters) or None, ((entry.get("name") or {}).get("primaryImage") or {}).get("url")))
        return people

    @staticmethod
    def _trailer(node: dict) -> str | None:
        video = next(iter(_edges(node, "primaryVideos")), {})
        return f"https://www.imdb.com/video/{video['id']}/" if video.get("id") else None

    # ── film ──────────────────────────────────────────────────────────────
    def movie(self, item_id):
        node = imdb_client.get_title(item_id)
        if not node or not (node.get("titleText") or {}).get("text"):
            return None
        
        ratings = node.get("ratingsSummary") or {}
        return MovieInfo(
            title=node["titleText"]["text"],
            original_title=(node.get("originalTitleText") or {}).get("text"),
            year=(node.get("releaseYear") or {}).get("year"),
            premiered=self.iso_date(node.get("releaseDate")),
            plot=((node.get("plot") or {}).get("plotText") or {}).get("plainText"),
            tagline=next((t.get("text") for t in _edges(node, "taglines")), None),
            runtime=self.minutes((node.get("runtime") or {}).get("seconds")),
            genres=[g["text"] for g in (node.get("genres") or {}).get("genres") or [] if g.get("text")],
            rating=ratings.get("aggregateRating"),
            votes=ratings.get("voteCount"),
            mpaa=(node.get("certificate") or {}).get("rating"),
            tags=[((k.get("keyword") or {}).get("text") or {}).get("text") for k in _edges(node, "keywords") if ((k.get("keyword") or {}).get("text") or {}).get("text")],
            countries=[c["text"] for c in (node.get("countriesOfOrigin") or {}).get("countries") or [] if c.get("text")],
            studios=[((c.get("company") or {}).get("companyText") or {}).get("text") for c in _edges(node, "companyCredits") if ((c.get("company") or {}).get("companyText") or {}).get("text")],
            directors=self._names(node, "directors"),
            writers=self._names(node, "writers"),
            actors=self._actors(node),
            trailer_url=self._trailer(node),
            ids={"imdb": node["id"]},
            image_url=(node.get("primaryImage") or {}).get("url"),
        )

    # ── episode ───────────────────────────────────────────────────────────
    def episode(self, series_id, season, episode):
        listed, show_title = imdb_client.get_episode(series_id, season, episode)
        if not listed or not (listed.get("titleText") or {}).get("text"):
            return None
        
        # The season listing is kept light (up to 250 episodes); the crew / cast / rating come from the episode's own record.
        node = {**listed, **(imdb_client.get_title(listed["id"]) or {})}
        ratings = node.get("ratingsSummary") or {}
        return EpisodeInfo(
            title=node["titleText"]["text"],
            show_title=show_title,
            season=int(season),
            episode=int(episode),
            plot=((node.get("plot") or {}).get("plotText") or {}).get("plainText"),
            aired=self.iso_date(node.get("releaseDate")),
            runtime=self.minutes((node.get("runtime") or {}).get("seconds")),
            rating=ratings.get("aggregateRating"),
            votes=ratings.get("voteCount"),
            directors=self._names(node, "directors"),
            writers=self._names(node, "writers"),
            actors=self._actors(node),
            ids={"imdb": node["id"]},
            image_url=(node.get("primaryImage") or {}).get("url"),
        )

    # ── series ────────────────────────────────────────────────────────────
    def series(self, series_id):
        node = imdb_client.get_title(series_id)
        if not node or not (node.get("titleText") or {}).get("text"):
            return None
        
        ratings = node.get("ratingsSummary") or {}
        ongoing = imdb_client.is_ongoing(series_id)
        return SeriesInfo(
            title=node["titleText"]["text"],
            original_title=(node.get("originalTitleText") or {}).get("text"),
            year=(node.get("releaseYear") or {}).get("year"),
            premiered=self.iso_date(node.get("releaseDate")),
            status=None if ongoing is None else "Continuing" if ongoing else "Ended",
            plot=((node.get("plot") or {}).get("plotText") or {}).get("plainText"),
            mpaa=(node.get("certificate") or {}).get("rating"),
            runtime=self.minutes((node.get("runtime") or {}).get("seconds")),
            rating=ratings.get("aggregateRating"),
            votes=ratings.get("voteCount"),
            genres=[g["text"] for g in (node.get("genres") or {}).get("genres") or [] if g.get("text")],
            studios=[((c.get("company") or {}).get("companyText") or {}).get("text") for c in _edges(node, "companyCredits") if ((c.get("company") or {}).get("companyText") or {}).get("text")],
            actors=self._actors(node),
            ids={"imdb": node["id"]},
            image_url=(node.get("primaryImage") or {}).get("url"),
        )
