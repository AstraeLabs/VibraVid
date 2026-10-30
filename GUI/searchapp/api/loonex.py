# 30.10.26

from VibraVid.services.loonex.scrapper import GetSerieInfo
from VibraVid.utils import config_manager

from .base import Entries
from .generic import GenericStreamingAPI


class LoonexAPI(GenericStreamingAPI):
    """Loonex (cartoni, anime e film d'animazione)."""
    site_name = "loonex"
    entry_default_type = "tv"
    log_label = "Loonex"

    def __init__(self):
        super().__init__()
        self.base_url = config_manager.domain.get(self.site_name, "full_url").rstrip("/")

    def _build_scraper(self, media_item: Entries):
        return GetSerieInfo(media_item.url, media_item.name, media_item.year, media_item.tmdb_id)
