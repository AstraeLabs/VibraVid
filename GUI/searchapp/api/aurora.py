# 27-01-26

from VibraVid.services.aurora.scrapper import GetSerieInfo

from .base import Entries
from .generic import GenericStreamingAPI


class AuroraAPI(GenericStreamingAPI):
    """Aurora (Discovery, Nove, Food Network, HGTV, Real Time, DMAX) — searches every brand through the shared scrapper."""
    site_name = "aurora"
    base_url = "https://public.aurora.enhanced.live"
    entry_default_type = "tv"
    log_label = "Aurora"

    def _build_scraper(self, media_item: Entries):
        return GetSerieInfo(media_item.url)
