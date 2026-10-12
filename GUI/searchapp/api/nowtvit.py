# 12.10.26

from VibraVid.services._base.site_loader import resolve_service_submodule

from .base import Entries
from .generic import GenericStreamingAPI

_scrapper = resolve_service_submodule("nowtvit", "scrapper")
GetSerieInfo = _scrapper.GetSerieInfo
parse_slug = _scrapper.parse_slug


class NowTvItAPI(GenericStreamingAPI):
    site_name = "nowtvit"
    base_url = "https://www.nowtv.it"
    log_label = "NOW"

    def _build_scraper(self, media_item: Entries):
        slug = parse_slug(str(media_item.slug or media_item.id or "")) or media_item.id
        return GetSerieInfo(slug)
