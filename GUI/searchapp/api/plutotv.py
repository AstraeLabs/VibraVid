# 26.09.26

from VibraVid.services.plutotv.scrapper import GetSerieInfo, GetSerieInfoBySlug

from .base import Entries
from .generic import GenericStreamingAPI


class PlutoTVAPI(GenericStreamingAPI):
    site_name = "plutotv"
    base_url = "https://pluto.tv"
    log_label = "Pluto TV"
    entry_default_type = "tv"

    def _build_scraper(self, media_item: Entries):
        slug = str(getattr(media_item, "slug", "") or "").strip()
        if slug:
            return GetSerieInfoBySlug(slug)

        series_id = str(getattr(media_item, "id", "") or "").strip()
        if not series_id:
            print(f"[Pluto TV] Error: Missing series id for {media_item.name}")
            return None

        url = f"https://service-vod.clusters.pluto.tv/v4/vod/series/{series_id}/seasons"
        return GetSerieInfo(url)
