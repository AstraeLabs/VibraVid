from GUI.searchapp.api.generic import GenericStreamingAPI
from GUI.searchapp.api.base import Entries


class _API(GenericStreamingAPI):
    site_name = "test"

    def _build_scraper(self, media_item):
        return None


def test_start_download_propagates_explicit_provider_failure():
    api = _API()
    api._search_fn = lambda **kwargs: False

    item = Entries(
        id="1",
        name="Example",
        slug="example",
        path_id=None,
        type="film",
        url="https://example.invalid/item",
        poster=None,
        year="2026",
        tmdb_id=None,
        provider_language=None,
        raw_data={"name": "Example", "type": "film"},
        desc=None,
    )

    assert api.start_download(item) is False


def test_start_download_keeps_non_false_provider_result_successful():
    api = _API()
    api._search_fn = lambda **kwargs: None

    item = Entries(
        id="1",
        name="Example",
        slug="example",
        path_id=None,
        type="film",
        url="https://example.invalid/item",
        poster=None,
        year="2026",
        tmdb_id=None,
        provider_language=None,
        raw_data={"name": "Example", "type": "film"},
        desc=None,
    )

    assert api.start_download(item) is True
