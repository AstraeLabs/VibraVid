# 08.03.26

from typing import Any

from VibraVid.services._base.site_loader import resolve_service_submodule

from .base import Entries, Episode
from .generic import GenericStreamingAPI

GetSerieInfo = resolve_service_submodule("primevideo", "scrapper").GetSerieInfo


class PrimeVideoAPI(GenericStreamingAPI):
    site_name = "primevideo"
    base_url = "https://www.primevideo.com"
    log_label = "PrimeVideo"

    def _build_entry(self, item_dict: dict[str, Any]) -> Entries:
        entry = super()._build_entry(item_dict)
        entry.id = item_dict.get("slug")
        entry.desc = item_dict.get("availability")
        return entry

    def _map_episode(self, ep: Any, idx: int) -> Episode:
        if isinstance(ep, dict):
            return Episode(
                number=ep.get("episodeNumber") or idx,
                name=ep.get("title") or f"Episodio {idx}",
                id=ep.get("id", idx),
                duration=ep.get("runtime") or None,
            )
        return super()._map_episode(ep, idx)

    def _build_scraper(self, media_item: Entries):
        return GetSerieInfo(url=media_item.url)
