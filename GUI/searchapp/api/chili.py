# 16.10.26

from typing import Any

from VibraVid.services.chili.scrapper import GetSerieInfo

from .base import Entries
from .generic import GenericStreamingAPI

_UNKNOWN_YEAR = "9999"


class ChiliAPI(GenericStreamingAPI):
    site_name = "chili"
    base_url = "https://www.chili.com"
    log_label = "CHILI"
    entry_default_type = "movie"

    def _build_entry(self, item_dict: dict[str, Any]) -> Entries:
        entry = super()._build_entry(item_dict)
        if str(entry.year) == _UNKNOWN_YEAR:
            entry.year = None
        entry.desc = item_dict.get("availability")
        return entry

    def _build_scraper(self, media_item: Entries):
        show_id = str(getattr(media_item, "id", "") or getattr(media_item, "slug", "") or "").strip()
        if not show_id:
            print(f"[CHILI] Error: Missing show id for {media_item.name}")
            return None

        market = (media_item.raw_data or {}).get("market")
        scraper = GetSerieInfo(show_id, media_item.name, media_item.year, market)
        scraper.load_stream_info()
        return scraper
