# 19.10.26

from VibraVid.services._base.site_loader import resolve_service_submodule

from .base import Entries
from .generic import GenericStreamingAPI

GetSerieInfo = resolve_service_submodule("cs_chrome", "downloader").GetSerieInfo


class CsChromeAPI(GenericStreamingAPI):
    site_name = "cs_chrome"
    base_url = "https://www.netflix.com"
    log_label = "CS Chrome"

    def _build_scraper(self, media_item: Entries):
        return GetSerieInfo(str(media_item.id))
