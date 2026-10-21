# 19.10.26

from VibraVid.services._base.site_loader import resolve_service_submodule

from .base import Entries
from .generic import GenericStreamingAPI

GetSerieInfo = resolve_service_submodule("netflix", "scrapper").GetSerieInfo
get_client = resolve_service_submodule("netflix", "client").get_client


class NetflixAPI(GenericStreamingAPI):
    site_name = "netflix"
    base_url = "https://www.netflix.com"
    log_label = "Netflix"

    def _build_scraper(self, media_item: Entries):
        return GetSerieInfo(get_client(), str(media_item.id))
