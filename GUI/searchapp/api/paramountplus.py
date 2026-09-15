# 05.08.26

from VibraVid.services._base.site_loader import resolve_service_submodule

from .base import Entries
from .generic import GenericStreamingAPI

GetSerieInfo = resolve_service_submodule("paramountplus", "scrapper").GetSerieInfo


class ParamountPlus(GenericStreamingAPI):
    site_name = "paramountplus"
    log_label = "ParamountPlus"

    def _build_scraper(self, media_item: Entries):
        return GetSerieInfo(media_item.id)
