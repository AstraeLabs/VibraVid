# 06.06.25

from VibraVid.services.mediasetinfinity.scrapper import GetSerieInfo

from .base import Entries
from .generic import GenericStreamingAPI


class MediasetInfinityAPI(GenericStreamingAPI):
    site_name = "mediasetinfinity"
    base_url = "https://mediasetinfinity.mediaset.it"
    log_label = "MediasetInfinity"

    def _build_scraper(self, media_item: Entries):
        return GetSerieInfo(media_item.url)
