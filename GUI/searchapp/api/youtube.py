# 29.10.26

from .generic import GenericStreamingAPI


class YoutubeAPI(GenericStreamingAPI):
    """YouTube lists single videos (``film`` entries), so there is no series scraper."""
    site_name = "youtube"
    base_url = "https://www.youtube.com"
    log_label = "YouTube"
