# 27.09.26

from .generic import GenericStreamingAPI


class Cineblog01API(GenericStreamingAPI):
    site_name = "cineblog01"
    entry_default_type = "film"
    log_label = "Cineblog01"

    def _build_scraper(self, media_item):
        return None
