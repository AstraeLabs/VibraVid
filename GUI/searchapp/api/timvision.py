# 13.10.26

from typing import Any

from VibraVid.services.timvision.scrapper import GetSerieInfo

from .base import Entries
from .generic import GenericStreamingAPI

_UNKNOWN_YEAR = "9999"


class TimvisionAPI(GenericStreamingAPI):
    site_name = "timvision"
    base_url = "https://www.timvision.it"
    log_label = "TIMvision"
    entry_default_type = "movie"

    def _build_entry(self, item_dict: dict[str, Any]) -> Entries:
        entry = super()._build_entry(item_dict)
        if str(entry.year) == _UNKNOWN_YEAR:
            entry.year = None
        entry.desc = item_dict.get("availability")
        return entry

    def _build_scraper(self, media_item: Entries):
        brand_id = str(getattr(media_item, "id", "") or getattr(media_item, "slug", "") or "").strip()
        if not brand_id:
            print(f"[TIMvision] Error: Missing series id for {media_item.name}")
            return None
        return GetSerieInfo(brand_id, media_item.name, media_item.year)
