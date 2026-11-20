# 02.10.26

from .generic import GenericStreamingAPI


class La7API(GenericStreamingAPI):
    """LA7 search results represent standalone videos, not browsable series."""

    site_name = "la7"
    base_url = "https://www.la7.it"
    log_label = "La7"

    def _build_entry(self, item_dict):
        """Route LA7 videos directly while retaining the original provider payload."""
        entry = super()._build_entry(item_dict)
        entry.type = "movie"
        return entry
