# 16.03.25

from typing import Any

from VibraVid.services.crunchyroll.scrapper import GetSerieInfo

from .base import Entries
from .generic import GenericStreamingAPI


class CrunchyrollAPI(GenericStreamingAPI):
    site_name = "crunchyroll"
    base_url = "https://www.crunchyroll.com"
    log_label = "Crunchyroll"

    def _build_entry(self, item_dict: dict[str, Any]) -> Entries:
        entry = super()._build_entry(item_dict)
        entry.slug = item_dict.get("id")  # crunchyroll uses the id as slug
        return entry

    def _build_scraper(self, media_item: Entries):
        series_id = media_item.url.split("/")[-1]
        return GetSerieInfo(series_id)
