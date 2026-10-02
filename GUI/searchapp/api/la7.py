# 02.10.26

from .generic import GenericStreamingAPI


class La7API(GenericStreamingAPI):
    """La7 only lists single videos (episodes/clips as ``movie`` entries), so there is no series scraper."""

    site_name = "la7"
    base_url = "https://www.la7.it"
    log_label = "La7"
