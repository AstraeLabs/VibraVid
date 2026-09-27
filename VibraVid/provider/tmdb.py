# 24.08.24

import logging
import os
import re
import time
import unicodedata
from difflib import SequenceMatcher

from rich.console import Console

from VibraVid.utils import config_manager
from VibraVid.utils.http_client import create_client, get_headers

console = Console()
logger = logging.getLogger(__name__)
_warned_legacy_login_key = False


def _configured_api_key() -> str | None:
    """Return the TMDB key: TMDB_API_KEY env var takes priority; falls back to the legacy Conf/login.json Provider.tmdb key (deprecated)."""
    global _warned_legacy_login_key

    env_key = str(os.environ.get("TMDB_API_KEY") or "").strip()
    if env_key:
        return env_key

    legacy_key = config_manager.login.get("Provider", "tmdb", default=None)
    if legacy_key:
        if not _warned_legacy_login_key:
            logger.warning(
                "TMDB key read from Conf/login.json (Provider.tmdb) — deprecated, "
                "set the TMDB_API_KEY environment variable instead. Legacy support "
                "will be removed in a future release."
            )
            _warned_legacy_login_key = True
        return legacy_key

    return None


class TMDBClient:
    ANIMATION_GENRE_ID = 16

    def __init__(self, api_key: str | None = None):
        """Initialize the class with the API key (resolved lazily if not given)."""
        self._api_key = api_key
        self.base_url = "https://api.themoviedb.org/3"
        self._cache: dict = {}
        self._warned_no_api_key = False

    @property
    def api_key(self) -> str | None:
        return self._api_key if self._api_key is not None else _configured_api_key()

    def _make_request(self, endpoint, params=None, retries=3):
        """Make a request to the given API endpoint with optional parameters."""
        if params is None:
            params = {}

        if self.api_key is None or self.api_key == "":
            if not self._warned_no_api_key:
                logger.error(
                    "TMDB API key is not set. Set the TMDB_API_KEY environment variable "
                    "(create a key at https://www.themoviedb.org/settings/api)."
                )
                self._warned_no_api_key = True
            return {}

        params["api_key"] = self.api_key

        cache_key = endpoint + str(sorted((k, v) for k, v in params.items() if k != "api_key"))
        if cache_key in self._cache:
            logger.debug(f"Cache hit: {endpoint}")
            return self._cache[cache_key]

        url = f"{self.base_url}/{endpoint}"

        for attempt in range(retries + 1):
            try:
                with create_client(headers=get_headers()) as client:
                    logger.debug(f"Make req: {url} with params: {params}")
                    response = client.get(url, params=params)
                response.raise_for_status()
                data = response.json()
                self._cache[cache_key] = data
                return data

            except Exception as e:
                if attempt < retries:
                    if hasattr(e, "response") and e.response:
                        status_code = e.response.status_code
                        if status_code in [429, 500, 502, 503, 504]:
                            wait_time = 2**attempt
                            console.log(
                                f"[yellow]TMDB API error {status_code}, retrying in {wait_time}s... ({attempt + 1}/{retries})[/yellow]"
                            )
                            time.sleep(wait_time)
                            continue

                console.log(f"[red]Error making request to {endpoint}: {e}[/red]")
                return {}

        return {}

    def _slugify(self, text):
        """Normalize and slugify a given text."""
        if not text:
            return ""

        text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
        text = re.sub(r"[^\w\s-]", "", text).strip().lower()
        text = re.sub(r"[-\s]+", "-", text)
        return text

    def _slugs_match(self, slug1: str, slug2: str, threshold: float = 0.85) -> bool:
        """Check if two slugs are similar enough using fuzzy matching."""
        if not slug1 or not slug2:
            return False

        ratio = SequenceMatcher(None, slug1, slug2).ratio()
        return ratio >= threshold

    def _search_tv_with_fallback(self, query: str, language_preference: str, include_adult: bool = True):
        """Search TV shows, falling back to en-US if no results in the preferred language."""
        results = self._make_request(
            "search/tv", {"query": query, "language": language_preference, "include_adult": include_adult}
        ).get("results", [])

        if not results and language_preference != "en-US":
            logger.info(f"No TV results in '{language_preference}' for query '{query}', retrying in en-US")
            results = self._make_request(
                "search/tv", {"query": query, "language": "en-US", "include_adult": include_adult}
            ).get("results", [])

        return results

    def _search_movie_with_fallback(self, query: str, language_preference: str, include_adult: bool = True):
        """Search movies, falling back to en-US if no results in the preferred language."""
        results = self._make_request(
            "search/movie", {"query": query, "language": language_preference, "include_adult": include_adult}
        ).get("results", [])

        if not results and language_preference != "en-US":
            logger.info(f"No movie results in '{language_preference}' for query '{query}', retrying in en-US")
            results = self._make_request(
                "search/movie", {"query": query, "language": "en-US", "include_adult": include_adult}
            ).get("results", [])

        return results

    def _prefer_animation(self, results: list) -> list:
        """Put animated titles first, keeping the rest as fallback."""
        animated = [r for r in results if self.ANIMATION_GENRE_ID in (r.get("genre_ids") or [])]
        others = [r for r in results if self.ANIMATION_GENRE_ID not in (r.get("genre_ids") or [])]
        return animated + others

    def _match_movie_result(self, movie_results, slug: str, year):
        """Return the TMDB id of the most popular movie result whose title/original_title slug-matches, honouring year if given."""
        exact_candidates = []
        slug_matches = []
        for movie in movie_results:
            title = movie.get("title")
            original_title = movie.get("original_title")
            release_date = movie.get("release_date")
            logger.debug(f"Candidate movie: title='{title}', original='{original_title}', year={release_date}")

            if release_date:
                movie_year = int(release_date[:4])
            else:
                continue

            movie_slug = self._slugify(title)
            original_slug = self._slugify(original_title) if original_title else None
            slug_matched = self._slugs_match(movie_slug, slug) or (original_slug and self._slugs_match(original_slug, slug))
            if not slug_matched:
                continue

            slug_matches.append((movie.get("popularity") or 0, movie["id"], movie_year))
            if not year or movie_year == year:
                exact_candidates.append((movie.get("popularity") or 0, movie["id"]))

        if exact_candidates:
            exact_candidates.sort(key=lambda c: c[0], reverse=True)
            return exact_candidates[0][1]

        # No exact-year match: if the slug uniquely identifies one title (release-year
        # ambiguity between the provider and TMDB, e.g. timezone/regional release dates),
        # accept it when its year is within 1 of the requested one.
        if year and len({c[1] for c in slug_matches}) == 1:
            _, movie_id, movie_year = slug_matches[0]
            if abs(movie_year - year) <= 1:
                logger.info(f"Matched movie by unique slug '{slug}' with year off by {abs(movie_year - year)} ({movie_year} vs {year})")
                return movie_id

        return None

    def _match_tv_result(self, tv_results, slug: str, year):
        """Return the TMDB id of the most popular TV result whose name/original_name slug-matches, honouring year if given."""
        exact_candidates = []
        slug_matches = []
        for show in tv_results:
            name = show.get("name")
            original_name = show.get("original_name")
            first_air_date = show.get("first_air_date")
            logger.debug(f"Candidate TV: name='{name}', original='{original_name}', year={first_air_date}")

            if first_air_date:
                show_year = int(first_air_date[:4])
            else:
                continue

            show_slug = self._slugify(name)
            original_slug = self._slugify(original_name) if original_name else None
            slug_matched = self._slugs_match(show_slug, slug) or (original_slug and self._slugs_match(original_slug, slug))
            if not slug_matched:
                continue

            slug_matches.append((show.get("popularity") or 0, show["id"], show_year))
            if not year or show_year == year:
                exact_candidates.append((show.get("popularity") or 0, show["id"]))

        if exact_candidates:
            exact_candidates.sort(key=lambda c: c[0], reverse=True)
            return exact_candidates[0][1]

        # No exact-year match: if the slug uniquely identifies one title (release-year
        # ambiguity between the provider and TMDB, e.g. timezone/regional release dates),
        # accept it when its year is within 1 of the requested one.
        if year and len({c[1] for c in slug_matches}) == 1:
            _, show_id, show_year = slug_matches[0]
            if abs(show_year - year) <= 1:
                logger.info(f"Matched TV by unique slug '{slug}' with year off by {abs(show_year - year)} ({show_year} vs {year})")
                return show_id

        return None

    def get_type_and_id_by_slug_year(
        self, slug: str, year: str = None, media_type: str = None,
        language_preference: str = "it",
        prefer_animation: bool = False,
    ):
        """Get the type (movie or tv) and ID from TMDB based on slug and year.

        Known limitation: this only searches the given `media_type`. A provider
        entry tagged e.g. "tv" for an OVA/special that TMDB catalogs as "movie"
        (or vice versa) still yields no match here — cross-media_type retry is
        not implemented (deliberately, to avoid false positives from title-only
        matching across catalogs).
        """
        if year:
            year = int(year)

        query = slug.replace("-", " ")

        if media_type == "movie":
            movie_results = self._search_movie_with_fallback(query, language_preference)
            if prefer_animation:
                movie_results = self._prefer_animation(movie_results)
            logger.info(f"Found {len(movie_results)} movie results for slug '{slug}' and year '{year}'")

            movie_id = self._match_movie_result(movie_results, slug, year)

            if not movie_id and language_preference != "en-US":
                
                # Preferred-language results may exist but not match (e.g. an Italian-only title) — retry in en-US.
                en_results = self._make_request(
                    "search/movie", {"query": query, "language": "en-US", "include_adult": True}
                ).get("results", [])
                if prefer_animation:
                    en_results = self._prefer_animation(en_results)
                movie_id = self._match_movie_result(en_results, slug, year)

            if movie_id:
                return {"type": "movie", "id": movie_id}

            logger.info(f"No movie result matched slug '{slug}' and year '{year}'")

        elif media_type == "tv":
            tv_results = self._search_tv_with_fallback(query, language_preference)
            if prefer_animation:
                tv_results = self._prefer_animation(tv_results)
            logger.info(f"Found {len(tv_results)} TV results for slug '{slug}' and year '{year}'")

            tv_id = self._match_tv_result(tv_results, slug, year)

            if not tv_id and language_preference != "en-US":

                # Preferred-language results may exist but not match (e.g. an Italian-only title) — retry in en-US.
                en_results = self._make_request(
                    "search/tv", {"query": query, "language": "en-US", "include_adult": True}
                ).get("results", [])
                if prefer_animation:
                    en_results = self._prefer_animation(en_results)
                tv_id = self._match_tv_result(en_results, slug, year)

            if tv_id:
                return {"type": "tv", "id": tv_id}

            logger.info(f"No TV result matched slug '{slug}' and year '{year}'")

        return None

    def get_year_by_slug_and_type(self, slug: str, media_type: str, language_preference: str = "it"):
        """Returns the year from the first search result that matches the slug."""
        if media_type == "movie":
            results = self._search_movie_with_fallback(slug.replace("-", " "), language_preference)
            logger.info(f"Found {len(results)} movie results for slug '{slug}'")

            if len(results) == 1 and results[0].get("release_date"):
                return int(results[0]["release_date"][:4])

            for movie in results:
                title = movie.get("title")
                original_title = movie.get("original_title")
                release_date = movie.get("release_date")

                if not release_date:
                    continue

                movie_slug = self._slugify(title)
                original_slug = self._slugify(original_title) if original_title else None

                if self._slugs_match(movie_slug, slug) or (original_slug and self._slugs_match(original_slug, slug)):
                    return int(release_date[:4])

        elif media_type == "tv":
            results = self._search_tv_with_fallback(slug.replace("-", " "), language_preference)
            logger.info(f"Found {len(results)} TV results for slug '{slug}'")

            if len(results) == 1 and results[0].get("first_air_date"):
                return int(results[0]["first_air_date"][:4])

            for show in results:
                name = show.get("name")
                original_name = show.get("original_name")
                first_air_date = show.get("first_air_date")

                if not first_air_date:
                    continue

                show_slug = self._slugify(name)
                original_slug = self._slugify(original_name) if original_name else None

                if self._slugs_match(show_slug, slug) or (original_slug and self._slugs_match(original_slug, slug)):
                    return int(first_air_date[:4])

        return None

    def _image_url(self, path: str | None, size: str) -> str | None:
        """Build a full TMDB image URL from an image path (e.g. poster_path/backdrop_path/still_path)."""
        if not path:
            return None
        return f"https://image.tmdb.org/t/p/{size}{path}"

    def get_backdrop_url(self, media_type: str, tmdb_id: int, size: str = "w1280"):
        """Get the backdrop URL for a movie or TV show."""
        try:
            logger.info(f"Getting backdrop for {media_type} with TMDB ID {tmdb_id}")
            details = self._make_request(f"{media_type}/{tmdb_id}", {"language": "it"})
            return self._image_url(details.get("backdrop_path"), size)

        except Exception as e:
            logger.error(f"Error getting backdrop for {media_type} {tmdb_id}: {e}")

        return None

    def get_poster_url(self, media_type: str, tmdb_id: int, size: str = "w780") -> str | None:
        """Get the poster URL for a movie or TV show."""
        try:
            details = self._make_request(f"{media_type}/{tmdb_id}", {"language": "it"})
            return self._image_url(details.get("poster_path"), size)
        except Exception as e:
            logger.error(f"Error getting poster for {media_type} {tmdb_id}: {e}")
        return None

    def get_season_poster_url(self, tmdb_id: int, season_number: int, size: str = "w780") -> str | None:
        """Get the poster URL for a specific TV season."""
        try:
            details = self._make_request(f"tv/{tmdb_id}/season/{season_number}", {"language": "it"})
            return self._image_url(details.get("poster_path"), size)
        except Exception as e:
            logger.error(f"Error getting season poster for tv {tmdb_id} season {season_number}: {e}")
        return None

    def get_episode_still_url(
        self, tmdb_id: int, season_number: int, episode_number: int, size: str = "w780"
    ) -> str | None:
        """Get the still (thumbnail) URL for a specific episode."""
        try:
            details = self._make_request(
                f"tv/{tmdb_id}/season/{season_number}/episode/{episode_number}", {"language": "it"}
            )
            return self._image_url(details.get("still_path"), size)
        except Exception as e:
            logger.error(f"Error getting episode still for tv {tmdb_id} S{season_number}E{episode_number}: {e}")
        return None

    def get_episode_title(self, tmdb_id: int, season_number: int, episode_number: int, language_preference: str = "it"):
        """Return the TMDB episode title for a specific TV episode."""
        details = self._make_request(
            f"tv/{tmdb_id}/season/{season_number}/episode/{episode_number}", {"language": language_preference}
        )
        name = details.get("name")
        if name:
            return name

        logger.info(f"No episode title found for tv TMDB ID {tmdb_id} S{season_number}E{episode_number}")
        return None

    def get_season_name(self, tmdb_id: int, season_number: int, language_preference: str = "it"):
        """Return the TMDB season name for a known, already-correct season number (no absolute remapping)."""
        details = self._make_request(f"tv/{tmdb_id}", {"language": language_preference})
        seasons = details.get("seasons") or []
        season = next((s for s in seasons if s.get("season_number") == season_number), None)
        return season.get("name") if season else None

    def get_season_for_absolute_episode(self, tmdb_id: int, absolute_episode: int, language_preference: str = "it"):
        """Map an absolute (cross-season) episode number to its real TMDB season."""
        if not absolute_episode or absolute_episode < 1:
            return None

        details = self._make_request(f"tv/{tmdb_id}", {"language": language_preference})
        seasons = details.get("seasons") or []

        remaining = absolute_episode
        for season in sorted(seasons, key=lambda s: s.get("season_number", 0)):
            season_number = season.get("season_number")
            if season_number is None or season_number == 0:
                continue  # skip "Specials"

            episode_count = season.get("episode_count") or 0
            if remaining <= episode_count:
                season_details = self._make_request(
                    f"tv/{tmdb_id}/season/{season_number}", {"language": language_preference}
                )
                episodes = season_details.get("episodes") or []
                if 0 < remaining <= len(episodes):
                    real_episode_number = episodes[remaining - 1].get("episode_number", remaining)
                else:
                    real_episode_number = remaining
                return {
                    "season_number": season_number,
                    "episode_number": real_episode_number,
                    "season_name": season.get("name"),
                }

            remaining -= episode_count

        logger.info(f"Absolute episode {absolute_episode} exceeds known episode count for tv TMDB ID {tmdb_id}")
        return None

    def resolve_actual_season_episode(
        self, tmdb_id: int, season_number: int, episode_number: int, language_preference: str = "it"
    ):
        """Correct a possibly-flat/absolute (season_number, episode_number) pair against the show's real TMDB season structure."""
        details = self._make_request(f"tv/{tmdb_id}", {"language": language_preference})
        seasons = details.get("seasons") or []
        target = next((s for s in seasons if s.get("season_number") == season_number), None)

        if target and episode_number and episode_number <= (target.get("episode_count") or 0):
            return season_number, episode_number

        mapped = self.get_season_for_absolute_episode(tmdb_id, episode_number, language_preference)
        if mapped:
            return mapped["season_number"], mapped["episode_number"]

        return season_number, episode_number

    def get_episode_artwork_url(self, tmdb_id: int, season_number: int, episode_number: int) -> str | None:
        """Episode still -> season poster -> series poster fallback chain."""
        return (
            self.get_episode_still_url(tmdb_id, season_number, episode_number)
            or self.get_season_poster_url(tmdb_id, season_number)
            or self.get_poster_url("tv", tmdb_id)
        )

    def get_imdb_id(self, tmdb_id: int, media_type: str, language_preference: str = "it"):
        """Return the IMDb ID associated with a TMDB movie or TV entry."""
        details = self._make_request(
            f"{media_type}/{tmdb_id}", {"language": language_preference, "append_to_response": "external_ids"}
        )

        imdb_id = details.get("imdb_id")
        if imdb_id:
            return imdb_id

        external_ids = details.get("external_ids", {}) or {}
        imdb_id = external_ids.get("imdb_id")
        if imdb_id:
            return imdb_id

        logger.info(f"No IMDb ID found for {media_type} TMDB ID {tmdb_id}")
        return None

    def get_tmdb_id_by_external_id(
        self,
        external_id: str | int,
        external_source: str,
        media_type: str,
        language_preference: str = "en-US",
    ) -> int | None:
        """Resolve a TVDB/IMDb identifier to a TMDB id without title matching."""
        if not external_id or media_type not in {"movie", "tv"}:
            return None

        result_key = "movie_results" if media_type == "movie" else "tv_results"
        details = self._make_request(
            f"find/{external_id}",
            {"external_source": external_source, "language": language_preference},
        )
        results = details.get(result_key) or []
        ids = {result.get("id") for result in results if result.get("id")}
        if len(ids) == 1:
            return int(ids.pop())

        if len(ids) > 1:
            logger.warning(
                "External id %s (%s) maps to multiple TMDB %s entries; refusing an ambiguous match",
                external_id,
                external_source,
                media_type,
            )
        return None

    def get_external_id(self, tmdb_id: int, media_type: str, external_id_type: str) -> str | int | None:
        """Return one external identifier attached to a canonical TMDB entry."""
        if not tmdb_id or media_type not in {"movie", "tv"} or not external_id_type:
            return None
        details = self._make_request(f"{media_type}/{tmdb_id}/external_ids")
        return details.get(external_id_type)

    def get_original_title(self, tmdb_id: int, media_type: str, language_preference: str = "it"):
        """Return the original-language title for a movie or TV entry."""
        details = self._make_request(f"{media_type}/{tmdb_id}", {"language": language_preference})
        original = details.get("original_title") if media_type == "movie" else details.get("original_name")
        if original:
            return original

        logger.info(f"No original title found for {media_type} TMDB ID {tmdb_id}")
        return None

    def get_title(self, tmdb_id: int, media_type: str, language_preference: str = "it"):
        """Return the TMDB title/name in language_preference for a movie or TV entry."""
        details = self._make_request(f"{media_type}/{tmdb_id}", {"language": language_preference})
        title = details.get("title") if media_type == "movie" else details.get("name")
        if title:
            return title

        logger.info(f"No title found for {media_type} TMDB ID {tmdb_id}")
        return None

    def get_original_language(self, tmdb_id: int, media_type: str, language_preference: str = "it"):
        """Return the ISO 639-1 original language code for a movie or TV entry."""
        details = self._make_request(f"{media_type}/{tmdb_id}", {"language": language_preference})
        original_language = details.get("original_language")
        if original_language:
            return original_language

        logger.info(f"No original language found for {media_type} TMDB ID {tmdb_id}")
        return None

    def search_movies(self, query: str, language_preference: str = "it"):
        """Search for movies and return a list of results with details."""
        results = self._search_movie_with_fallback(query, language_preference)
        logger.info(f"Found {len(results)} movie results for query '{query}'")

        movies = []
        for movie in results:
            logger.info(f"Movie ID {movie.get('id')} - '{movie.get('title')}'.")

            movies.append(
                {
                    "id": movie.get("id"),
                    "title": movie.get("title"),
                    "original_title": movie.get("original_title"),
                    "release_date": movie.get("release_date"),
                    "poster_path": movie.get("poster_path"),
                }
            )

        return movies

    def search_series(self, query: str, language_preference: str = "it"):
        """Search for TV series and return a list of results with details"""
        results = self._search_tv_with_fallback(query, language_preference)
        logger.info(f"Found {len(results)} TV results for query '{query}'")

        series = []
        for show in results:
            logger.info(f"TV ID {show.get('id')} - '{show.get('name')}'")

            series.append(
                {
                    "id": show.get("id"),
                    "name": show.get("name"),
                    "original_name": show.get("original_name"),
                    "first_air_date": show.get("first_air_date"),
                    "poster_path": show.get("poster_path"),
                }
            )

        return series

    def get_alternative_titles(self, tmdb_id: int, media_type: str, language: str = "it") -> list:
        """Get alternative titles for a movie or TV show."""
        details = self._make_request(
            f"{media_type}/{tmdb_id}", {"language": language, "append_to_response": "alternative_titles"}
        )
        titles = []

        alt_titles_data = details.get("alternative_titles", {})
        for title_data in alt_titles_data.get("titles", []):
            if title_data.get("iso_3166_1") == language.upper() or title_data.get("type") == "":
                titles.append(title_data.get("title", ""))

        main_title = details.get("title" if media_type == "movie" else "name", "")
        if main_title and main_title not in titles:
            titles.append(main_title)

        return titles

# Istance
tmdb_client = TMDBClient()
tmdb = tmdb_client
