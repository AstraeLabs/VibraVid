# 02.10.26

import logging
from urllib.parse import urlsplit

from .generic import GenericStreamingAPI
from .la7_artwork import archive_artwork

logger = logging.getLogger(__name__)


class La7API(GenericStreamingAPI):
    """La7 only lists single videos (episodes/clips as ``movie`` entries), so there is no series scraper."""

    site_name = "la7"
    base_url = "https://www.la7.it"
    log_label = "La7"

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
