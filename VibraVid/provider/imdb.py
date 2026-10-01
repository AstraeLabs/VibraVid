# 01.10.26

import hashlib
import json
import logging

from VibraVid.utils.http_client import create_client

logger = logging.getLogger(__name__)

GRAPHQL_URL = "https://caching.graphql.imdb.com/"
GRAPHQL_HEADERS = {
    "Accept": "application/graphql+json, application/json",
    "Content-Type": "application/json",
    "Origin": "https://www.imdb.com",
    "X-Imdb-Client-Name": "imdb-web-next-localized",
    "X-Imdb-User-Country": "US",
}
KIND_TO_TYPES = {"movie": ["movie", "tvMovie"], "tv": ["tvSeries", "tvMiniSeries"]}
_TITLE_FIELDS = (
    "id titleText { text } originalTitleText { text } releaseYear { year } "
    "releaseDate { year month day } plot { plotText { plainText } } primaryImage { url } "
    "runtime { seconds } genres { genres { text } } ratingsSummary { aggregateRating voteCount }"
)
_DETAIL_FIELDS = (
    _TITLE_FIELDS
    + " certificate { rating } countriesOfOrigin { countries { text } }"
    + " taglines(first: 1) { edges { node { text } } }"
    + " keywords(first: 8) { edges { node { keyword { text { text } } } } }"
    + ' companyCredits(first: 5, filter: {categories: ["production"]}) { edges { node { company { companyText { text } } } } }'
    + ' directors: credits(first: 4, filter: {categories: ["director"]}) { edges { node { name { nameText { text } } } } }'
    + ' writers: credits(first: 4, filter: {categories: ["writer"]}) { edges { node { name { nameText { text } } } } }'
    + ' cast: credits(first: 15, filter: {categories: ["actor"]}) { edges { node { name { nameText { text } primaryImage { url } } ... on Cast { characters { name } } } } }'
    + " primaryVideos(first: 1) { edges { node { id } } }"
)
_EPISODE_FIELDS = (
    "id titleText { text } plot { plotText { plainText } } primaryImage { url } "
    "releaseDate { year month day } runtime { seconds } "
    "series { displayableEpisodeNumber { displayableSeason { season } episodeNumber { episodeNumber } } }"
)


def _quote(value: str) -> str:
    """Escape *value* for use inside a GraphQL string literal."""
    return value.replace("\\", "\\\\").replace('"', '\\"')


class IMDbClient:
    def __init__(self):
        self._cache: dict = {}

    def graphql(self, operation: str, query: str) -> dict | None:
        """Run *query* and return its ``data``, or None on any error."""
        if query in self._cache:
            return self._cache[query]

        extensions = {"persistedQuery": {"version": 1, "sha256Hash": hashlib.sha256(query.encode()).hexdigest()}}
        params = {"operationName": operation, "variables": "{}", "extensions": json.dumps(extensions, separators=(",", ":"))}
        try:
            with create_client() as client:
                body = client.get(GRAPHQL_URL, params=params, headers=GRAPHQL_HEADERS, timeout=30).json()
                if self._persisted_query_missing(body):
                    payload = {"operationName": operation, "variables": {}, "query": query, "extensions": extensions}
                    body = client.post(GRAPHQL_URL, json=payload, headers=GRAPHQL_HEADERS, timeout=30).json()
        except Exception as exc:
            logger.warning(f"IMDb request {operation} failed: {exc}")
            return None

        if body.get("errors"):
            logger.debug(f"IMDb {operation} errors: {[e.get('message') for e in body['errors']][:3]}")
            return None

        data = body.get("data")
        self._cache[query] = data
        return data

    @staticmethod
    def _persisted_query_missing(body: dict) -> bool:
        for error in body.get("errors") or []:
            if (error.get("extensions") or {}).get("code") == "PERSISTED_QUERY_NOT_FOUND" or error.get("message") == "PersistedQueryNotFound":
                return True
        return False

    def search_titles(self, name: str, media_type: str, year: int | None = None) -> list[dict]:
        """Up to 5 title nodes matching *name*; with *year* the release year is limited to year-1..year+1 (sources often disagree by one)."""
        constraints = [f'titleTextConstraint: {{searchTerm: "{_quote(name)}"}}']
        if year:
            constraints.append(f'releaseDateConstraint: {{releaseDateRange: {{start: "{year - 1}-01-01", end: "{year + 1}-12-31"}}}}')
        types = KIND_TO_TYPES.get(media_type)
        if types:
            constraints.append(f"titleTypeConstraint: {{anyTitleTypeIds: {json.dumps(types)}}}")

        query = f"query VibraSearch {{ advancedTitleSearch(first: 5, constraints: {{{', '.join(constraints)}}}) {{ edges {{ node {{ title {{ {_TITLE_FIELDS} }} }} }} }} }}"
        edges = ((self.graphql("VibraSearch", query) or {}).get("advancedTitleSearch") or {}).get("edges") or []
        return [edge["node"]["title"] for edge in edges if (edge.get("node") or {}).get("title")]

    def get_title(self, imdb_id: str) -> dict | None:
        """Details of one title (``tt…``): a film, a series or an episode."""
        query = f'query VibraTitle {{ title(id: "{_quote(str(imdb_id))}") {{ {_DETAIL_FIELDS} }} }}'
        return (self.graphql("VibraTitle", query) or {}).get("title")

    def is_ongoing(self, series_id: str) -> bool | None:
        """Whether IMDb lists the series as still running (None when unknown)."""
        query = f'query VibraOngoing {{ title(id: "{_quote(str(series_id))}") {{ episodes {{ isOngoing }} }} }}'
        episodes = ((self.graphql("VibraOngoing", query) or {}).get("title") or {}).get("episodes") or {}
        return episodes.get("isOngoing")

    def get_episode(self, series_id: str, season: int, episode: int) -> tuple[dict | None, str | None]:
        """``(episode node, series title)`` for one episode, or ``(None, None)`` when IMDb lists none with that numbering."""
        query = (
            f'query VibraEpisodes {{ title(id: "{_quote(str(series_id))}") {{ titleText {{ text }} '
            f'episodes {{ episodes(first: 250, filter: {{includeSeasons: ["{int(season)}"]}}) '
            f"{{ edges {{ node {{ {_EPISODE_FIELDS} }} }} }} }} }} }}"
        )
        title = (self.graphql("VibraEpisodes", query) or {}).get("title") or {}
        for edge in ((title.get("episodes") or {}).get("episodes") or {}).get("edges") or []:
            node = edge.get("node") or {}
            numbers = ((node.get("series") or {}).get("displayableEpisodeNumber")) or {}
            found_season = ((numbers.get("displayableSeason") or {}).get("season"))
            found_episode = ((numbers.get("episodeNumber") or {}).get("episodeNumber"))
            if str(found_season) == str(season) and str(found_episode) == str(episode):
                return node, (title.get("titleText") or {}).get("text")
        return None, None


imdb_client = IMDbClient()
