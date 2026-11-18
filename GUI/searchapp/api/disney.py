# 02.10.26

from VibraVid.services._base.site_loader import resolve_service_submodule

from .base import Entries
from .generic import GenericStreamingAPI

GetSerieInfo = resolve_service_submodule("disney", "scrapper").GetSerieInfo
get_client = resolve_service_submodule("disney", "client").get_client


class DisneyAPI(GenericStreamingAPI):
    site_name = "disney"
    base_url = "https://www.disneyplus.com"
    log_label = "Disney+"

    def _build_scraper(self, media_item: Entries):
        return GetSerieInfo(get_client(), media_item.id)
