# 13.10.26
# timvision.scrapper - Search and parsing

import logging

from VibraVid.services._base.object import Episode, Season, SeasonManager

from .client import TimvisionClient

logger = logging.getLogger(__name__)

_client: TimvisionClient | None = None


def _get_client() -> TimvisionClient:
    global _client
    if _client is None:
        _client = TimvisionClient()
    return _client


def _is_included_in_subscription(availability: str, item: dict = None) -> bool:
    """Check if content is included in standard TIMvision subscription."""
    avail_upper = availability.upper()
    
    # Explicitly excluded: ANY TVOD (pay-per-view) and Prime Video (deeplink)
    if "TVOD" in avail_upper:
        return False
    if "PRIME" in avail_upper:
        return False
    
    # For series, check the partner channel (altLogoChannel)
    if item and availability == "Serie":
        alt_logo = (item.get("altLogoChannel") or "").upper()
        if "PRIME" in alt_logo:
            return False
        # Only include series from known partners that use TIMvision player
        included_partners = (
            "TIMVISION", 
            "MEDIASET", 
            "DISCOVERY", 
            "KIDS", 
            "PARAMOUNT",
            "LA7",
        )
        for partner in included_partners:
            if partner in alt_logo:
                return True
        # Unknown partner -> exclude (likely requires external player)
        return False
    
    # Explicitly included providers (use TIMvision player)
    included_keywords = (
        "PARAMOUNT",      # Paramount+ via TIMvision player
        "MEDIASET",       # Mediaset catch up / SVOD
        "DISCOVERY",      # Discovery+ via TIMvision
        "KIDS",           # TIMvision kids
        "LA7",            # la7svod
    )
    for keyword in included_keywords:
        if keyword in avail_upper:
            return True
    
    # TIMvision SVOD (but not TVOD)
    if "SVOD (TIMVISION)" in avail_upper:
        return True
    
    # Series are typically included (fallback for items without partner info)
    if availability == "Serie":
        return True
    
    return False


def search_timvision(query: str, only_included: bool = True) -> list[dict]:
    """Search TIMvision and return list of content items.
    
    Args:
        query: Search query string
        only_included: If True, filter to only content included in TIMvision subscription
                      (SVOD from TIMvision, Mediaset, Discovery). Default True.
    """
    try:
        client = _get_client()
        results = client.search(query)
        processed = []
        for item in results:
            # Handle different possible field names from hodor API
            item_id = item.get("contentID", item.get("id", item.get("contentId", item.get("pid", ""))))
            title = item.get("title", item.get("name", item.get("displayName", "")))
            item_type = item.get("type", item.get("contentType", "")).lower()
            year = str(item.get("year", item.get("releaseYear", item.get("productionYear", "9999"))))
            image = item.get("URLImage", item.get("image", item.get("poster", item.get("thumbnail", item.get("cover", "")))))
            
            # Skip purely numeric IDs (likely people/actors, not playable content)
            if item_id and item_id.isdigit():
                logger.debug(f"Skipping person/actor: {item_id} - {title}")
                continue
            
            # Skip personality type
            if item_type == "personality":
                logger.debug(f"Skipping personality: {item_id} - {title}")
                continue
            
            # Determine type: movie vs series
            if item_type in ("movie", "film", "vod", "unit", "vod"):
                content_type = "movie"
            elif item_type in ("series", "tv", "show", "season", "episode", "folder"):
                content_type = "tv"
            else:
                # Default to movie if unsure
                content_type = "movie"
            
            # Get detailed availability label (like RakutenTV)
            availability = client.get_availability_label(item)
            
            # Filter by subscription inclusion (pass full item for partner check on series)
            if only_included and not _is_included_in_subscription(availability, item):
                logger.debug(f"Skipping excluded content: {item_id} - {title} ({availability})")
                continue
            
            processed.append({
                "id": item_id,
                "name": title,
                "type": content_type,
                "year": year,
                "image": image,
                "duration": item.get("duration", item.get("runtime", "")),
                "availability": availability,
            })
        return processed
    except Exception as e:
        logger.error(f"TIMvision search error: {e}")
        return []


def get_content_metadata(content_id: str) -> dict | None:
    """Get full metadata for a content item."""
    try:
        client = _get_client()
        return client.get_content(content_id)
    except Exception as e:
        logger.error(f"TIMvision metadata error: {e}")
        return None


class GetSerieInfo:
    """Season/episode tree for a TIMvision series brand ID."""

    def __init__(self, brand_id: str, series_name: str | None = None, year=None):
        self.brand_id = brand_id
        self.series_name = series_name or brand_id
        self.year = year
        self.seasons_manager = SeasonManager()
        self._loaded = False

    def _load_episodes(self) -> None:
        if self._loaded:
            return
        episodes = _get_client().get_episodes(self.brand_id)
        for ep in episodes:
            season_number = ep.get("season") or 1
            episode_number = ep.get("episode") or 0
            season = self.seasons_manager.get_season_by_number(season_number)
            if season is None:
                season = self.seasons_manager.add(
                    Season(
                        id=self.brand_id,
                        number=season_number,
                        name=f"Stagione {season_number}",
                        slug=self.brand_id,
                    )
                )
            season.episodes.add(
                Episode(
                    id=ep["id"],
                    number=episode_number,
                    name=ep.get("name") or f"Episodio {episode_number}",
                    image=ep.get("image", ""),
                    year=self.year,
                )
            )
        self._loaded = True

    def getNumberSeason(self) -> int:
        self._load_episodes()
        return len(self.seasons_manager.seasons)

    def getEpisodeSeasons(self, season_number: int) -> list:
        self._load_episodes()
        season = self.seasons_manager.get_season_by_number(season_number)
        if not season:
            logger.error(f"TIMvision: season {season_number} not found")
            return []
        return season.episodes.episodes