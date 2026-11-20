"""Tests for episode-specific LA7 artwork without downloading full videos."""
from unittest.mock import patch

from GUI.searchapp.api.la7_artwork import _ArchiveCards, archive_artwork


def test_archive_card_artwork_and_missing_image_fallback():
    parser = _ArchiveCards()
    parser.feed('''
      <a href="/una-giornata-particolare/puntate/episode-18-11-2026-399809">
        <img src="https://images.la7play.it/video/399809.webp" alt="Episode">
      </a>
      <a href="/una-giornata-particolare/puntate/episode-11-11-2026-399233">
        <span>No image</span>
      </a>
    ''')
    assert parser.artwork == {
        "/una-giornata-particolare/puntate/episode-18-11-2026-399809":
            "https://images.la7play.it/video/399809.webp",
    }


def test_reject_non_image_cdn_or_insecure_image():
    parser = _ArchiveCards()
    parser.feed('''
      <a href="/program/puntate/episode-111"><img src="http://images.la7play.it/evil.webp"></a>
      <a href="/program/puntate/episode-112"><img src="https://untrusted.example/image.webp"></a>
    ''')
    assert not parser.artwork


def test_fetch_pages_and_deduplicate_artwork():
    first = """
      <a href="/programme/puntate?page=2">2</a>
      <a href="/programme/puntate/episode-101"><img src="https://images.la7play.it/a.webp"></a>
    """
    second = """
      <a href="/programme/puntate/episode-102"><img src="https://images.la7play.it/b.webp"></a>
    """
    class Response:
        status_code = 200
        def __init__(self, text):
            self.text = text
        def raise_for_status(self):
            pass
    class Client:
        def __init__(self):
            self.urls = []
        def __enter__(self):
            return self
        def __exit__(self, *exc):
            pass
        def get(self, url, timeout=None):
            self.urls.append(url)
            return Response(second if "?page=2" in url else first)
    client = Client()
    with patch("GUI.searchapp.api.la7_artwork.create_client", return_value=client):
        images = archive_artwork("/programme")
    assert len(client.urls) == 2
    assert images["/programme/puntate/episode-101"].endswith("a.webp")
    assert images["/programme/puntate/episode-102"].endswith("b.webp")
