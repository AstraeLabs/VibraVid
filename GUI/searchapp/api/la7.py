# 02.10.26

from dataclasses import replace

from .generic import GenericStreamingAPI


class La7API(GenericStreamingAPI):
    """La7 only lists single videos (episodes/clips as ``movie`` entries), so there is no series scraper."""

    site_name = "la7"
    base_url = "https://www.la7.it"
    log_label = "La7"

    def start_download(self, media_item, season=None, episodes=None):
        """Route LA7 episodes directly despite provider raw_data marking them as TV."""
        # GenericStreamingAPI prefers raw_data over the GUI media type.
        # Normalize only the handoff copy, preserving the original search result.
        direct_data = dict(media_item.raw_data or media_item.__dict__)
        direct_data["type"] = "movie"
        direct_item = replace(media_item, type="movie", raw_data=direct_data)
        return super().start_download(direct_item, season, episodes)
