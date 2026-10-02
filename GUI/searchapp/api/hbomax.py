# 02.10.26

from VibraVid.services._base.site_loader import resolve_service_submodule

from .base import Entries
from .generic import GenericStreamingAPI

GetSerieInfo = resolve_service_submodule("hbomax", "scrapper").GetSerieInfo


class HBOMaxAPI(GenericStreamingAPI):
    site_name = "hbomax"
    base_url = "https://play.hbomax.com"
    log_label = "HBOMax"

    def _build_entry(self, item_dict) -> Entries:
        entry = super()._build_entry(item_dict)
        if entry.type == "live":
            entry.type = "movie"
        return entry

    def _build_scraper(self, media_item: Entries):
        return GetSerieInfo(media_item.id)
