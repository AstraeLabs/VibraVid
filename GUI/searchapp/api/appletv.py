# 28.07.26

from VibraVid.core.ui.tracker import context_tracker
from VibraVid.services._base.site_loader import resolve_service_submodule

from .base import Entries
from .generic import GenericStreamingAPI

GetSerieInfo = resolve_service_submodule("appletv", "scrapper").GetSerieInfo


class AppleTVAPI(GenericStreamingAPI):
    site_name = "appletv"
    base_url = "https://tv.apple.com"
    log_label = "AppleTV"

    def _build_scraper(self, media_item: Entries):
        opts = context_tracker.site_options or {}
        drm_type = {"widevine": "WV", "playready": "PR"}.get(opts.get("drm"), "WV")
        return GetSerieInfo(media_item.id, drm_type, opts.get("store"))
