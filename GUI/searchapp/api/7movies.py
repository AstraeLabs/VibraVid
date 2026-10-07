# 07.10.26

from VibraVid.services._base.site_loader import resolve_service_submodule
from VibraVid.utils import config_manager

from .base import Entries
from .generic import GenericStreamingAPI


class SevenMoviesAPI(GenericStreamingAPI):
    site_name = "7movies"
    log_label = "7Movies"
    entry_default_type = "film"

    def __init__(self):
        super().__init__()
        self.base_url = config_manager.domain.get(self.site_name, "full_url")

    def _build_scraper(self, media_item: Entries):
        scrapper = resolve_service_submodule("7movies", "scrapper")
        tmdb_id = media_item.tmdb_id or media_item.id
        return scrapper.GetSerieInfo(int(tmdb_id), media_item.name)
