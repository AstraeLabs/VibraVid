# 09.10.26

from VibraVid.services.rakutentv.scrapper import GetSerieInfo

from .base import Entries
from .generic import GenericStreamingAPI

_UNKNOWN_YEAR = "9999"


class RakutenTVAPI(GenericStreamingAPI):
    site_name = "rakutentv"
    base_url = "https://www.rakuten.tv"
    log_label = "Rakuten TV"
    entry_default_type = "movie"

    def _build_entry(self, item_dict: dict) -> Entries:
        entry = super()._build_entry(item_dict)
        if str(entry.year) == _UNKNOWN_YEAR:
            entry.year = None
        return entry

    def search(self, query: str) -> list[Entries]:
        return [entry for entry in super().search(query) if str(entry.type).lower() != "live"]

    def _build_scraper(self, media_item: Entries):
        show_id = str(getattr(media_item, "id", "") or getattr(media_item, "slug", "") or "").strip()
        if not show_id:
            print(f"[Rakuten TV] Error: Missing show id for {media_item.name}")
            return None

        market = (media_item.raw_data or {}).get("market")
        scraper = GetSerieInfo(show_id, media_item.name, media_item.year, market)
        scraper.load_stream_info()
        return scraper
