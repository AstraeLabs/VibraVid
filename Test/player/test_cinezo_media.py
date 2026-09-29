import pytest

from VibraVid.player import cinezo_media
from VibraVid.player.cinezo import CinezoSourceProbe


def test_media_resolver_rejects_unavailable_source():
    source = CinezoSourceProbe(
        name="berlin",
        endpoint="https://example.test/berlin",
        available=False,
    )
    with pytest.raises(RuntimeError, match="Source backend is not available"):
        cinezo_media.resolve_cinezo_media(source, 27205, "movie")


def test_media_resolver_requires_tv_coordinates():
    source = CinezoSourceProbe(
        name="berlin",
        endpoint="https://example.test/berlin",
        available=True,
    )
    with pytest.raises(ValueError, match="season and episode are required"):
        cinezo_media.resolve_cinezo_media(source, 1399, "tv")


def test_media_resolver_fetches_selected_backend_payload(monkeypatch):
    source = CinezoSourceProbe(
        name="berlin",
        endpoint="https://example.test/berlin",
        available=True,
    )

    class _Response:
        ok = True
        status_code = 200

        def json(self):
            return {
                "source": {
                    "url": "https://example.test/authorized-master.m3u8",
                    "headers": {"Referer": "https://example.test/"},
                },
                "subtitles": [
                    {"file": "https://example.test/it.vtt", "label": "Italiano"}
                ],
            }

    class _Client:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def get(self, url, timeout=30):
            assert url == "https://example.test/berlin"
            assert timeout == 30
            return _Response()

    monkeypatch.setattr(cinezo_media, "create_client", lambda headers=None: _Client())
    monkeypatch.setattr(cinezo_media, "get_userAgent", lambda: "test-agent")

    result = cinezo_media.resolve_cinezo_media(source, 27205, "movie")

    assert result == {
        "url": "https://example.test/authorized-master.m3u8",
        "headers": {
            "User-Agent": "test-agent",
            "Referer": "https://example.test/",
        },
        "subtitles": [
            {
                "type": "subtitle",
                "language": "Italiano",
                "name": "Italiano",
                "url": "https://example.test/it.vtt",
                "extension": "vtt",
            }
        ],
    }


def test_subtitles_to_tracks_supports_legacy_cinezo_shape():
    tracks = cinezo_media._subtitles_to_tracks(
        [{"file": "https://example.test/it.vtt", "label": "Italiano"}]
    )

    assert tracks == [
        {
            "type": "subtitle",
            "language": "Italiano",
            "name": "Italiano",
            "url": "https://example.test/it.vtt",
            "extension": "vtt",
        }
    ]


def test_build_result_merges_headers_and_normalizes_subtitles(monkeypatch):
    monkeypatch.setattr(
        cinezo_media,
        "get_userAgent",
        lambda: "test-agent",
    )

    result = cinezo_media._build_result(
        "https://example.test/master.m3u8",
        {"Referer": "https://example.test/", "Origin": "https://example.test"},
        [{"url": "https://example.test/it.vtt", "language": "it", "name": "Italiano"}],
    )

    assert result["url"] == "https://example.test/master.m3u8"
    assert result["headers"] == {
        "User-Agent": "test-agent",
        "Referer": "https://example.test/",
        "Origin": "https://example.test",
    }
    assert result["subtitles"] == [
        {
            "type": "subtitle",
            "language": "it",
            "name": "Italiano",
            "url": "https://example.test/it.vtt",
            "extension": "vtt",
        }
    ]


def test_build_result_rejects_empty_stream_url():
    with pytest.raises(RuntimeError, match="no playable source"):
        cinezo_media._build_result("", {}, [])
