# 16.03.25

import json
import logging
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import quote, urlparse

from bs4 import BeautifulSoup

from VibraVid.services._base.object import Episode, Season, SeasonManager
from VibraVid.services.mediasetinfinity.client import get_client
from VibraVid.utils.http_client import create_client, get_headers, get_userAgent

from .regions import region_conf

logger = logging.getLogger(__name__)
MIN_DURATION = 10
MAX_WORKERS = 6
FULL_EPISODE_MIN_DURATION = 50
FEED_FETCH_TIMEOUT = 20
IMAGE_BASE_URL = "https://img-prod-api2.mediasetplay.mediaset.it/api/images"


class GetSerieInfo:
    BAD_WORDS = [
        "Trailer",
        "Promo",
        "Teaser",
        "Clip",
        "Backstage",
        "Le interviste",
        "BALLETTI",
        "Anteprime web",
        "I servizi",
        "Le trame della settimana",
        "Esclusive",
        "INTERVISTE",
        "SERVIZI",
        "Gossip",
        "Prossimi appuntamenti tv",
        "DAYTIME",
        "Ballo",
        "Canto",
        "Band",
        "Senza ADV",
        "Il serale",
    ]
    FEED_PAGE_SIZE = 600

    def __init__(self, url):
        """Initialize the GetSerieInfo class for scraping."""
        self.conf = region_conf()
        self.headers = get_headers()
        self.url = url
        self.client = create_client()
        self.seasons_manager = SeasonManager()
        self._collect_lock = threading.Lock()
        self.serie_id = None
        self.public_id = None
        self.series_name = ""
        self.stagioni_disponibili = []

    def close(self):
        """Close the HTTP client session."""
        if self.client:
            self.client.close()

    def _extract_serie_id(self):
        """Extract the series ID from the starting URL"""
        try:
            after = self.url.split("SE", 1)[1]
            after = after.split(",")[0].strip()
            self.serie_id = f"SE{after}"
            return self.serie_id
        except Exception as e:
            logger.error(f"Failed to extract serie id from url {self.url}: {e}")
            self.serie_id = None
            return None

    def _get_public_id(self):
        """Get the public ID for API calls"""
        self.public_id = self.conf["feed_public_id"]
        return self.public_id

    def _feed_url(self, feed):
        """Construct the feed URL for a given feed name."""
        return f"https://feed.entertainment.tv.theplatform.eu/f/{self.public_id}/{feed}"

    def _get_series_data(self):
        """Get series data through the API"""
        try:
            params = {"byGuid": self.serie_id}
            response = self.client.get(
                self._feed_url("mediaset-prod-all-series-v2"), params=params, headers=self.headers
            )
            if response.status_code == 200 and response.text.strip().startswith("{"):
                return response.json()
            logger.error(f"Unexpected response from series API: {response.status_code}")
            return None
        except Exception as e:
            logger.error(f"Failed to get series data with error: {str(e)}")
            return None

    def _process_available_seasons(self, data):
        """Process available seasons from series data"""
        if not data or not data.get("entries"):
            logger.error("No series data found in API")
            return []

        entry = data["entries"][0]
        self.series_name = entry.get("title", "")

        seriesTvSeasons = entry.get("seriesTvSeasons", [])
        availableTvSeasonIds = entry.get("availableTvSeasonIds", [])

        stagioni_disponibili = []
        for url in availableTvSeasonIds:
            season = next((s for s in seriesTvSeasons if s["id"] == url), None)
            if season:
                stagioni_disponibili.append(
                    {
                        "tvSeasonNumber": season["tvSeasonNumber"],
                        "title": season.get("title", ""),
                        "url": url,
                        "id": str(url).split("/")[-1],
                        "guid": season["guid"],
                    }
                )
            else:
                logger.error(f"Season URL not found: {url}")

        stagioni_disponibili.sort(key=lambda s: s["tvSeasonNumber"])
        return stagioni_disponibili

    def _fallback_homepage_scrape(self):
        """Fallback: Scrape carousels directly from the homepage if no seasons are found via API"""
        print(f"Fallback: Scraping homepage directly from {self.url}")
        dummy_season = {
            "tvSeasonNumber": 1,
            "title": "Stagione 1",
            "url": None,
            "id": self.serie_id,
            "guid": self.serie_id,
            "page_url": self.url,
        }
        self._extract_season_sb_ids([dummy_season])
        if dummy_season.get("categories"):
            self.stagioni_disponibili = [dummy_season]
            return True
        return False

    def _build_season_page_urls(self, stagioni_disponibili):
        """Build season page URLs"""
        parsed_url = urlparse(self.url)
        base_url = f"{parsed_url.scheme}://{parsed_url.netloc}"
        path_parts = parsed_url.path.strip("/").split("/")
        section = path_parts[0] if len(path_parts) > 1 else "fiction"
        series_slug = path_parts[-1].split("_")[0]
        for season in stagioni_disponibili:
            page_url = f"{base_url}/{section}/{series_slug}{season['tvSeasonNumber']}_{self.serie_id},{season['guid']}"
            season["page_url"] = page_url

    def _fetch_season_categories(self, season):
        """Fetch and parse a single season page. Uses its own HTTP client (thread-safe for concurrent use)."""
        with create_client() as client:
            response_page = client.get(season["page_url"], headers={"User-Agent": get_userAgent()})

        if not response_page or response_page.status_code != 200:
            logger.error(f"Failed to fetch season page: {season.get('page_url')}")
            return

        soup = BeautifulSoup(response_page.text, "html.parser")
        carousel_links = soup.find_all("a", class_="titleCarousel")
        if not carousel_links:
            carousel_links = soup.find_all("a", attrs={"data-testid": "carousel-title"})
        if not carousel_links:
            carousel_links = [
                link
                for link in soup.find_all("a", href=True)
                if "/episodi_" in link.get("href", "")
            ]
        if not carousel_links:
            logger.error(f"No season categories found for season {season['tvSeasonNumber']}")
            return

        season["categories"] = []
        for carousel_link in carousel_links:
            if carousel_link.has_attr("href"):
                category_title = carousel_link.find("h2")
                category_name = (
                    category_title.text.strip()
                    if category_title
                    else carousel_link.get_text(" ", strip=True) or "Unnamed"
                )
                if any(w.lower() in category_name.lower() for w in self.BAD_WORDS):
                    continue
                href = carousel_link["href"]
                sb_id = href.split(",")[-1] if "," in href else href.split("_")[-1]
                season["categories"].append({"name": category_name, "sb": sb_id})

    def _extract_season_sb_ids(self, stagioni_disponibili):
        """Extract sb_ids for all seasons concurrently."""
        seasons_with_url = [s for s in stagioni_disponibili if s.get("page_url")]
        if not seasons_with_url:
            return

        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
            list(pool.map(self._fetch_season_categories, seasons_with_url))

        total_categories = sum(len(s.get("categories", [])) for s in seasons_with_url)
        print(f"Found {total_categories} categories across {len(seasons_with_url)} seasons")

    @staticmethod
    def _is_full_episode_category(category_name: str) -> bool:
        normalized = (category_name or "").lower()
        return "puntate intere" in normalized or "puntata intera" in normalized

    def _get_season_episodes(self, season, sb_id, category_name, client=None):
        """Get episodes for a specific season"""
        logger.debug(f"Getting episodes for season {season['tvSeasonNumber']} category {category_name} sb_id {sb_id}")
        if any(w.lower() in category_name.lower() for w in self.BAD_WORDS):
            return []

        if "tutti" in category_name.lower() or category_name.lower().startswith("all"):
            episodes = self._get_all_season_episodes(season, category_name, client=client)
        elif sb_id.startswith("sb"):
            episodes = self._get_episodes_from_feed_api(sb_id, season["tvSeasonNumber"], category_name, client=client)
        elif sb_id.startswith("/"):
            episodes = self._extract_episodes_from_rsc_text(
                sb_id, season["tvSeasonNumber"], category_name, season.get("guid"), client=client
            )
        else:
            episodes = self._extract_episodes_from_graphql_listing(
                sb_id, season["tvSeasonNumber"], category_name, client=client
            )
            if not episodes:
                episodes = self._extract_episodes_from_rsc_text(
                    sb_id, season["tvSeasonNumber"], category_name, season.get("guid"), client=client
                )
            if self._is_full_episode_category(category_name) and len(episodes) <= 24:
                fallback = self._get_all_season_episodes(season, category_name, client=client)
                if fallback and len(fallback) > len(episodes):
                    logger.info(f"Using full-season feed fallback for season {season['tvSeasonNumber']} ({category_name})")
                    episodes = fallback

        logger.debug(f"Found {len(episodes)} episodes for season {season['tvSeasonNumber']} ({category_name})")
        return episodes

    def _extract_episodes_from_graphql_listing(self, sb_id, season_number, category_name, client=None):
        """Extract episodes from the Mediaset GraphQL listing feed."""
        client = client or self.client
        listing_id = sb_id[1:] if sb_id.startswith("e") else sb_id
        if not listing_id:
            return []

        api = get_client()
        if not api.getHash256():
            return []

        try:
            headers = dict(api.generate_request_headers())
            headers.update(
                {
                    "accept": "*/*",
                    "cache-control": "no-cache",
                    "content-type": "application/json",
                    "origin": self.conf["origin"],
                    "pragma": "no-cache",
                    "referer": self.conf["origin"] + "/",
                    "user-agent": get_userAgent(),
                    "x-m-app-version": "1.1.1",
                }
            )
            extensions = json.dumps(
                {"persistedQuery": {"version": 1, "sha256Hash": api.getHash256()}}, separators=(",", ":")
            )
            context = '{"a":{"flags":["SHOW_TITLE"],"layout":"GRID","template":"KEYFRAME"},"pt":"listing"}'

            episodes = []
            seen_ids = set()
            after = None
            while True:
                variables = {"first": 24, "id": listing_id, "pageType": "listing", "context": context}
                if after is not None:
                    variables["after"] = str(after)

                response = client.get(
                    self.conf["graphql_url"],
                    params={
                        "extensions": extensions,
                        "variables": json.dumps(variables, separators=(",", ":")),
                    },
                    headers=headers,
                )

                if response.status_code != 200:
                    logger.error(f"GraphQL listing request failed with status {response.status_code}")
                    return []

                payload = response.json()
                data = payload.get("data") or {}
                if not data:
                    return []

                result = next(iter(data.values()))
                if not result:
                    return []

                items_connection = result.get("itemsConnection") or {}
                items = items_connection.get("items") or []
                page_info = items_connection.get("pageInfo") or {}
                end_cursor = page_info.get("endCursor")
                has_next_page = bool(page_info.get("hasNextPage"))

                for item in items:
                    item_id = item.get("guid") or item.get("id") or item.get("url")
                    if not item_id or item_id in seen_ids:
                        continue

                    duration_raw = item.get("duration") or 0
                    try:
                        duration = int(duration_raw / 60) if duration_raw else 0
                    except Exception:
                        duration = 0

                    if duration < MIN_DURATION:
                        continue

                    episodes.append(
                        Episode(
                            id=item_id,
                            name=item.get("cardTitle") or item.get("cardEyelet") or "",
                            url=item.get("url") or item.get("cardLink", {}).get("value", ""),
                            duration=duration,
                            number=len(episodes) + 1,
                            category=category_name,
                            description=item.get("cardText") or item.get("description", ""),
                            season_number=season_number,
                            image=self._episode_image(item.get("guid")),
                        )
                    )
                    seen_ids.add(item_id)

                if not has_next_page or not end_cursor or not items:
                    break
                after = end_cursor
            return episodes
        except Exception as e:
            logger.error(f"GraphQL listing extraction failed: {e}")
            return []

    def _fetch_feed_page(self, client, page_params):
        """Fetch a single feed page, retrying once on transient/timeout errors."""
        last_error = None
        for attempt in range(2):
            try:
                return client.get(
                    self._feed_url("mediaset-prod-all-programs-v2"),
                    params=page_params,
                    headers={"user-agent": get_userAgent()},
                    timeout=FEED_FETCH_TIMEOUT,
                ).json()
            except Exception as e:
                last_error = e
                if attempt == 0:
                    time.sleep(0.3)
        logger.error(f"Feed page fetch failed after retry: {last_error}")
        return {}

    def _fetch_all_feed_entries(self, base_params, client=None):
        """Page through the theplatform programs feed with base_params, returning all entries."""
        client = client or self.client
        entries = []
        start = 1
        while True:
            page_params = dict(base_params, range=f"{start}-{start + self.FEED_PAGE_SIZE - 1}")
            data = self._fetch_feed_page(client, page_params)
            page_entries = (data or {}).get("entries", [])
            entries.extend(page_entries)
            if len(page_entries) < self.FEED_PAGE_SIZE:
                break
            start += self.FEED_PAGE_SIZE
        return entries

    def _episode_image_from_entry(self, entry):
        """Return the best feed artwork, falling back to the guid-based CDN URL."""
        if not isinstance(entry, dict):
            return self._episode_image(entry)

        thumbnails = entry.get("thumbnails") or {}

        preferred = (
            "image_keyframe_poster-652x367",
            "image_keyframe_poster-1280x720",
            "image_horizontal_cover-704x396",
        )

        for key in preferred:
            thumbnail = thumbnails.get(key)
            if isinstance(thumbnail, dict) and thumbnail.get("url"):
                return thumbnail["url"]

        candidates = []
        for key, thumbnail in thumbnails.items():
            if not isinstance(thumbnail, dict) or not thumbnail.get("url"):
                continue
            if not any(kind in key for kind in ("keyframe", "horizontal", "header_poster")):
                continue

            width = thumbnail.get("width") or 0
            height = thumbnail.get("height") or 0
            try:
                area = int(width) * int(height)
            except (TypeError, ValueError):
                area = 0

            candidates.append((area, thumbnail["url"]))

        if candidates:
            return max(candidates)[1]

        return self._episode_image(entry.get("guid"))

    def _get_all_season_episodes(self, season, category_name="programs_feed", client=None):
        """Fetch the full programs feed for the season and return a list of Episode objects for all entries."""
        logger.debug(f"Getting all episodes for season {season['tvSeasonNumber']}")
        try:
            base_params = {
                "byTvSeasonId": season.get("url") or season.get("id"),
                "sort": ":publishInfo_lastPublished|asc,tvSeasonEpisodeNumber|asc",
            }
            entries = self._fetch_all_feed_entries(base_params, client=client)
            episodes = []
            for entry in entries:
                duration = (
                    int(entry.get("mediasetprogram$duration", 0) / 60) if entry.get("mediasetprogram$duration") else 0
                )
                if duration < MIN_DURATION:
                    continue

                ep_num = entry.get("tvSeasonEpisodeNumber") or entry.get("mediasetprogram$episodeNumber")
                try:
                    ep_num = int(ep_num) if ep_num else 0
                except Exception:
                    ep_num = 0

                category = entry.get("mediasetprogram$category")
                if not category:
                    category = category_name if duration >= FULL_EPISODE_MIN_DURATION else "Clip"

                episodes.append(
                    Episode(
                        id=entry.get("guid"),
                        name=entry.get("title"),
                        url=entry.get("media")[0].get("publicUrl"),
                        duration=duration,
                        number=ep_num,
                        category=category,
                        description=entry.get("description", ""),
                        season_number=season.get("tvSeasonNumber"),
                        release_date=self._feed_release_date(entry),
                        image=self._episode_image_from_entry(entry),
                    )
                )
            return episodes
        except Exception as e:
            logger.error(f"_get_all_season_episodes failed: {e}")
            return []

    @staticmethod
    def _feed_release_date(entry):
        """Original broadcast date from a theplatform feed entry (mediasetprogram$publishInfo.last_published, ISO 8601)."""
        publish_info = entry.get("mediasetprogram$publishInfo") or {}
        last_published = publish_info.get("last_published")
        return last_published.split("T")[0] if last_published else last_published

    def _episode_image(self, guid):
        """Episode keyframe thumbnail URL, built directly from the episode's own guid."""
        if not guid:
            return None
        return f"{IMAGE_BASE_URL}/mp/v5/{self.conf['image_region']}/{guid}/image_keyframe_poster/292/165@2"

    def _season_hero_image(self, guid):
        """Season hero/backdrop image URL, built directly from the season's own guid."""
        if not guid:
            return None
        return f"{IMAGE_BASE_URL}/mst/v5/{self.conf['image_region']}/{guid}/image_header_poster/768/630@2"

    @staticmethod
    def _rsc_unescape(s):
        """Unescape a raw JSON string fragment captured by regex (handles \\", \\n, unicode escapes, ...)."""
        if not s:
            return s
        try:
            return json.loads(f'"{s}"')
        except Exception:
            return s

    def _parse_video_item_blocks(self, text, video_url_re, str_re, category_name, season_number):
        """Parse the GraphQL-shaped 'VideoItem' blocks some listing categories render (has duration/guid/description)."""
        episodes = []
        pattern = r'"__typename":"VideoItem".*?"url":"' + video_url_re + r'"'
        for match in re.finditer(pattern, text, re.DOTALL):
            block = match.group(0)
            ep = {}
            fields = {
                "title": r'"cardTitle":"(' + str_re + r')"',
                "description": r'"description":"(' + str_re + r')"',
                "duration": r'"duration":(\d+)',
                "guid": r'"guid":"(' + str_re + r')"',
                "url": r'"url":"(' + video_url_re + r')"',
            }
            for key, regex in fields.items():
                m = re.search(regex, block)
                if m:
                    ep[key] = int(m.group(1)) if key == "duration" else m.group(1)
            if ep:
                duration = int(ep.get("duration", 0) / 60) if ep.get("duration") else 0
                if duration < MIN_DURATION:
                    continue
                episodes.append(
                    Episode(
                        id=ep.get("guid", ""),
                        name=self._rsc_unescape(ep.get("title", "")),
                        url=ep.get("url", ""),
                        duration=duration,
                        number=len(episodes) + 1,
                        category=category_name,
                        description=self._rsc_unescape(ep.get("description", "")),
                        season_number=season_number,
                    )
                )
        return episodes

    def _parse_card_link_blocks(self, text, video_url_re, str_re, category_name, season_number):
        """Parse the pre-rendered React 'Link' card blocks (no duration/guid, only referenceId + image alt/title)."""
        episodes = []
        seen = set()
        link_pattern = r'"referenceId":"([^"]+)","referenceType":"video".{0,150}?"value":"(' + video_url_re + r')"'
        alt_pattern = r'"alt":"(' + str_re + r')"'
        for match in re.finditer(link_pattern, text, re.DOTALL):
            ref_id, url = match.group(1), match.group(2)
            if ref_id in seen:
                continue

            seen.add(ref_id)
            title_match = re.search(alt_pattern, text[match.end() : match.end() + 1200], re.DOTALL)
            title = title_match.group(1) if title_match else ""
            episodes.append(
                Episode(
                    id=ref_id,
                    name=self._rsc_unescape(title),
                    url=url,
                    duration=MIN_DURATION,
                    number=len(episodes) + 1,
                    category=category_name,
                    description="",
                    season_number=season_number,
                )
            )
        return episodes

    def _extract_episodes_from_rsc_text(self, sb_id, season_number, category_name, guid=None, client=None):
        """Extract episodes from RSC response text."""
        client = client or self.client
        episodes = []
        if sb_id.startswith("/"):
            href = sb_id
        else:
            href = f"/browse/{category_name.lower().replace(' ', '-')}_{sb_id}"
        browse_url = f"{self.conf['site_base']}{href}"
        logger.debug(f"Constructed browse URL for RSC: {browse_url}")

        host_marker = self.conf["site_base"].split("://", 1)[-1] + "/"
        url_path = browse_url.split(host_marker)[1] if host_marker in browse_url else browse_url
        state = [
            "",
            {"children": [["path", url_path, "c"], {"children": ["__PAGE__", {}, None, "refetch"]}, None, None]},
            None,
            None,
        ]
        router_state_tree = quote(json.dumps(state, separators=(",", ":")))

        rsc_headers = {"rsc": "1", "next-router-state-tree": router_state_tree, "User-Agent": get_userAgent()}
        base = re.escape(self.conf["site_base"])
        video_url_re = r"(?:" + base + r'/video/[^"]*?' + r"|" + base + r'/[^"]*?/player/?' + r")"
        str_re = r'(?:[^"\\]|\\.)*'

        for attempt in range(2):
            try:
                episode_response = client.get(browse_url, headers=rsc_headers)
                status = getattr(episode_response, "status_code", None)
                if status and status >= 400:
                    episode_response.raise_for_status()

                text = episode_response.text
                episodes = self._parse_video_item_blocks(text, video_url_re, str_re, category_name, season_number)
                if not episodes:
                    episodes = self._parse_card_link_blocks(text, video_url_re, str_re, category_name, season_number)
                return episodes
            except Exception as e:
                logger.error(f"Attempt {attempt + 1} failed for season {season_number}: {e}")
                if attempt == 0:
                    time.sleep(0.3)
        return episodes

    def _get_episodes_from_feed_api(self, sb_id, season_number, category_name, client=None):
        """Get episodes from programs feed API for sb-prefixed IDs"""
        episodes = []
        try:
            clean_sb_id = sb_id[2:] if sb_id.startswith("sb") else sb_id
            base_params = {
                "byCustomValue": "{subBrandId}{" + str(clean_sb_id) + "}",
                "sort": ":publishInfo_lastPublished|asc,tvSeasonEpisodeNumber|asc",
            }
            entries = self._fetch_all_feed_entries(base_params, client=client)
            for entry in entries:
                duration = (
                    int(entry.get("mediasetprogram$duration", 0) / 60) if entry.get("mediasetprogram$duration") else 0
                )
                ep_num = entry.get("tvSeasonEpisodeNumber") or entry.get("mediasetprogram$episodeNumber", 0)
                try:
                    ep_num = int(ep_num)
                except Exception:
                    ep_num = 0
                episodes.append(
                    Episode(
                        id=entry.get("guid", ""),
                        name=entry.get("title", ""),
                        duration=duration,
                        url=entry.get("media", [{}])[0].get("publicUrl") if entry.get("media") else "",
                        number=ep_num,
                        category=category_name,
                        description=entry.get("description", ""),
                        season_number=season_number,
                        release_date=self._feed_release_date(entry),
                        image=self._episode_image_from_entry(entry),
                    )
                )

        except Exception as e:
            logger.error(f"Error fetching episodes from feed API for sb_id {sb_id}: {e}")
        return episodes

    def collect_season(self) -> None:
        """Retrieve all episodes for all seasons using the new Mediaset Infinity API."""
        try:
            self._extract_serie_id()
            if not self._get_public_id():
                logger.error("Failed to get public ID")
                return
            data = self._get_series_data()
            if data:
                self.stagioni_disponibili = self._process_available_seasons(data)
            if not self.stagioni_disponibili:
                logger.info("No seasons found via API. Attempting fallback homepage scrape...")
                self._fallback_homepage_scrape()
            if not self.stagioni_disponibili:
                logger.error("No seasons found even after fallback")
                return

            api_seasons = [s for s in self.stagioni_disponibili if s.get("url")]
            if api_seasons:
                self._build_season_page_urls(api_seasons)

            seasons_to_extract = [s for s in self.stagioni_disponibili if "categories" not in s]
            if seasons_to_extract:
                self._extract_season_sb_ids(seasons_to_extract)

            for season in self.stagioni_disponibili:
                season["episodes"] = []

            tasks = [
                (season, category)
                for season in self.stagioni_disponibili
                for category in season.get("categories", [])
                if not any(w.lower() in category["name"].lower() for w in self.BAD_WORDS)
            ]

            def _fetch_task(task):
                season, category = task
                with create_client() as client:
                    return season, self._get_season_episodes(season, category["sb"], category["name"], client=client)

            if tasks:
                with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
                    for season, episodes in pool.map(_fetch_task, tasks):
                        existing_ids = {ep.id for ep in season["episodes"]}
                        for ep in episodes:
                            if ep.id not in existing_ids:
                                season["episodes"].append(ep)
                                existing_ids.add(ep.id)

            seasons_missing_full_episodes = [
                season
                for season in self.stagioni_disponibili
                if not any(self._is_full_episode_category(c["name"]) for c in season.get("categories", []))
            ]
            if seasons_missing_full_episodes:
                with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
                    fallback_results = pool.map(
                        lambda season: self._get_all_season_episodes(season, category_name="Puntate intere"),
                        seasons_missing_full_episodes,
                    )
                
                for season, fallback_episodes in zip(seasons_missing_full_episodes, fallback_results, strict=True):
                    existing_ids = {ep.id for ep in season["episodes"]}
                    for ep in fallback_episodes:
                        if ep.id not in existing_ids:
                            season["episodes"].append(ep)
                            existing_ids.add(ep.id)

            total_episodes = sum(len(s["episodes"]) for s in self.stagioni_disponibili)
            print(f"Collected {total_episodes} episodes across {len(self.stagioni_disponibili)} seasons")

            self._populate_seasons_manager()
        except Exception as e:
            logger.error(f"Error in collect_season: {str(e)}")

    def _populate_seasons_manager(self):
        for season_data in self.stagioni_disponibili:
            episodes = season_data.get("episodes") or []
            if episodes:
                episodes = sorted(episodes, key=lambda ep: 0 if self._is_full_episode_category(ep.category) else 1)
                season_obj = self.seasons_manager.add(
                    Season(
                        number=season_data["tvSeasonNumber"],
                        name=f"Season {season_data['tvSeasonNumber']}",
                        id=season_data.get("title") or season_data.get("id"),
                        image=self._season_hero_image(season_data.get("guid")),
                    )
                )

                if season_obj:
                    for ep in episodes:
                        season_obj.episodes.add(ep)

    # ------------- FOR GUI -------------
    def getNumberSeason(self) -> int:
        """Get the total number of seasons available for the series."""
        with self._collect_lock:
            if not self.seasons_manager.seasons:
                self.collect_season()
        return len(self.seasons_manager.seasons)

    def getEpisodeSeasons(self, season_number: int) -> list:
        """Get all episodes for a specific season."""
        with self._collect_lock:
            if not self.seasons_manager.seasons:
                self.collect_season()
        season = self.seasons_manager.get_season_by_number(season_number)
        if season:
            return season.episodes.episodes
        available_numbers = [s.number for s in self.seasons_manager.seasons]
        logger.error(f"Season {season_number} not found. Available seasons: {available_numbers}")
        return []
