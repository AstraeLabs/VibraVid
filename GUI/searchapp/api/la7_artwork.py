"""Resolve LA7Play episode artwork from program archive cards."""

import re
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit

from VibraVid.utils.http_client import create_client, get_userAgent

BASE_URL = "https://www.la7.it"


class _ArchiveCards(HTMLParser):
    def __init__(self):
        super().__init__()
        self.artwork = {}
        self._links = []

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if tag == "a":
            self._links.append({"href": values.get("href"), "image": None})
        elif tag == "img" and self._links:
            url = values.get("src", "")
            parts = urlsplit(url)
            if (
                not self._links[-1]["image"]
                and parts.scheme == "https"
                and parts.hostname == "images.la7play.it"
            ):
                self._links[-1]["image"] = url

    def handle_endtag(self, tag):
        if tag != "a" or not self._links:
            return
        item = self._links.pop()
        href, image = item["href"], item["image"]
        if href and image:
            path = urlsplit(urljoin(BASE_URL, href)).path
            if re.search(r"-\d+$", path):
                self.artwork[path] = image


def archive_artwork(programme_path: str) -> dict[str, str]:
    """Map video page paths to artwork; return {} for missing archives."""
    archive_path = programme_path.rstrip("/") + "/puntate"
    with create_client(headers={"user-agent": get_userAgent()}) as client:
        response = client.get(urljoin(BASE_URL, archive_path), timeout=10)
        if response.status_code == 404:
            return {}
        response.raise_for_status()
        pages = [response.text]
        page_numbers = [
            int(number)
            for number in re.findall(
                r'href=["\']' + re.escape(archive_path) + r'\?page=(\d+)["\']',
                pages[0],
            )
        ]
        for number in range(2, min(max(page_numbers, default=1), 100) + 1):
            response = client.get(
                urljoin(BASE_URL, f"{archive_path}?page={number}"), timeout=10
            )
            response.raise_for_status()
            pages.append(response.text)

    images = {}
    for html in pages:
        parser = _ArchiveCards()
        parser.feed(html)
        images.update(parser.artwork)
    return images
