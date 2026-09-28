import pytest

from VibraVid.services.cineblog01 import client


def _domain_reader(sections):
    def get_section(name):
        return dict(sections.get(name, {}))

    return get_section


class _Response:
    def __init__(self, url, text="ok", error=None):
        self.url = url
        self.text = text
        self._error = error

    def raise_for_status(self):
        if self._error is not None:
            raise self._error


class _HTTPClient:
    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def get(self, url, params=None, timeout=None):
        self.calls.append(
            {
                "url": url,
                "params": params,
                "timeout": timeout,
            }
        )
        return self._responses.pop(0)


def _set_domains(monkeypatch, sections):
    monkeypatch.setattr(
        client.config_manager.domain,
        "get_section",
        _domain_reader(sections),
    )


def test_get_base_urls_preserves_tracker_order(monkeypatch):
    _set_domains(
        monkeypatch,
        {
            "Cineblog_1": {"full_url": "https://cineblog001.download/"},
            "Cineblog_2": {"full_url": "https://cineblog01.world/"},
            "Cineblog_3": {"full_url": "https://cineblog01.watch/"},
        },
    )

    assert client.get_base_urls() == [
        "https://cineblog001.download/",
        "https://cineblog01.world/",
        "https://cineblog01.watch/",
    ]
    assert client.get_base_url() == "https://cineblog001.download/"


def test_get_base_urls_skips_missing_and_duplicate_entries(monkeypatch):
    _set_domains(
        monkeypatch,
        {
            "Cineblog_1": {"full_url": "https://cineblog001.download"},
            "Cineblog_2": {},
            "Cineblog_3": {"full_url": "https://cineblog001.download/"},
        },
    )

    assert client.get_base_urls() == ["https://cineblog001.download/"]


def test_get_base_urls_fails_when_tracker_has_no_cb01_domains(monkeypatch):
    _set_domains(monkeypatch, {})

    with pytest.raises(ValueError, match="No Cineblog01 domain is available"):
        client.get_base_urls()


def test_search_page_falls_back_to_second_mirror(monkeypatch):
    _set_domains(
        monkeypatch,
        {
            "Cineblog_1": {"full_url": "https://cineblog001.download/"},
            "Cineblog_2": {"full_url": "https://cineblog01.world/"},
            "Cineblog_3": {"full_url": "https://cineblog01.watch/"},
        },
    )

    http = _HTTPClient(
        [
            _Response(
                "https://cineblog001.download/index.php",
                error=RuntimeError("primary unavailable"),
            ),
            _Response(
                "https://cineblog01.world/index.php",
                text="second mirror",
            ),
        ]
    )
    monkeypatch.setattr(client, "create_client", lambda **kwargs: http)
    monkeypatch.setattr(client, "get_headers", lambda: {"User-Agent": "test"})

    text, final_url = client.fetch_search_page("example")

    assert text == "second mirror"
    assert final_url == "https://cineblog01.world/index.php"
    assert [call["url"] for call in http.calls] == [
        "https://cineblog001.download/index.php",
        "https://cineblog01.world/index.php",
    ]
    assert http.calls[0]["params"] == {
        "story": "example",
        "do": "search",
        "subaction": "search",
    }


def test_detail_page_retargets_absolute_url_to_fallback_mirror(monkeypatch):
    _set_domains(
        monkeypatch,
        {
            "Cineblog_1": {"full_url": "https://cineblog001.download/"},
            "Cineblog_2": {"full_url": "https://cineblog01.world/"},
        },
    )

    http = _HTTPClient(
        [
            _Response(
                "https://cineblog001.download/movie/example/?ref=1",
                error=RuntimeError("primary unavailable"),
            ),
            _Response(
                "https://cineblog01.world/movie/example/?ref=1",
                text="detail",
            ),
        ]
    )
    monkeypatch.setattr(client, "create_client", lambda **kwargs: http)
    monkeypatch.setattr(client, "get_headers", lambda: {"User-Agent": "test"})

    text, final_url = client.fetch_detail_page(
        "https://cineblog001.download/movie/example/?ref=1"
    )

    assert text == "detail"
    assert final_url == "https://cineblog01.world/movie/example/?ref=1"
    assert [call["url"] for call in http.calls] == [
        "https://cineblog001.download/movie/example/?ref=1",
        "https://cineblog01.world/movie/example/?ref=1",
    ]


def test_fetch_raises_last_error_when_all_mirrors_fail(monkeypatch):
    _set_domains(
        monkeypatch,
        {
            "Cineblog_1": {"full_url": "https://cineblog001.download/"},
            "Cineblog_2": {"full_url": "https://cineblog01.world/"},
        },
    )

    first_error = RuntimeError("primary unavailable")
    second_error = RuntimeError("secondary unavailable")
    http = _HTTPClient(
        [
            _Response("https://cineblog001.download/index.php", error=first_error),
            _Response("https://cineblog01.world/index.php", error=second_error),
        ]
    )
    monkeypatch.setattr(client, "create_client", lambda **kwargs: http)
    monkeypatch.setattr(client, "get_headers", lambda: {"User-Agent": "test"})

    with pytest.raises(RuntimeError, match="secondary unavailable"):
        client.fetch_search_page("example")
