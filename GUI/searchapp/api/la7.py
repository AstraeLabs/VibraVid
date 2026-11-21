# 02.10.26

import logging
from dataclasses import replace
from urllib.parse import urlsplit

from .generic import GenericStreamingAPI
from .la7_artwork import archive_artwork

logger = logging.getLogger(__name__)


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

    def start_download(self, media_item, season=None, episodes=None):
        """Route LA7 episodes directly despite provider raw_data marking them as TV."""
        # GenericStreamingAPI prefers raw_data over the GUI media type.
        # Normalize only the handoff copy, preserving the original search result.
        direct_data = dict(media_item.raw_data or media_item.__dict__)
        direct_data["type"] = "movie"
        direct_item = replace(media_item, type="movie", raw_data=direct_data)
        return super().start_download(direct_item, season, episodes)

    def search(self, query):
        """Enrich individual LA7 episodes with artwork shown in archive cards."""
        entries = super().search(query)
        programme_paths = {
            entry.path_id
            for entry in entries
            if entry.path_id and entry.url
            and ("/puntate/" in entry.url or "/rivedila7/" in entry.url)
        }
        images = {}
        for path in programme_paths:
            try:
                images.update(archive_artwork(path))
            except Exception:
                logger.warning("Unable to retrieve LA7 archive artwork for %s", path, exc_info=True)
        for entry in entries:
            if entry.url:
                artwork = images.get(urlsplit(entry.url).path)
                if artwork:
                    entry.poster = artwork
                    if isinstance(entry.raw_data, dict):
                        entry.raw_data["image"] = artwork
        return entries
