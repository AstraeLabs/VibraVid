# 27-01-26

from VibraVid.services.discoveryplus.scrapper import GetSerieInfo

from .base import Entries
from .generic import GenericStreamingAPI


class DiscoveryPlus(GenericStreamingAPI):
    site_name = "discoveryplus"
    log_label = "DiscoveryPlus"

    def _build_scraper(self, media_item: Entries):
        return GetSerieInfo(media_item.id)
