import pytest

from VibraVid.player import cinezo_media
from VibraVid.player.cinezo import CinezoSourceProbe
from VibraVid.services.cinezo import client as cinezo_client


def test_movie_player_url():
    assert cinezo_client.get_player_url(27205, "movie") == (
        "https://player.cinezo.live/embed/movie/27205"
    )


def test_tv_player_url():
    assert cinezo_client.get_player_url(1399, "tv", 1, 2) == (
        "https://player.cinezo.live/embed/tv/1399/1/2"
    )


def test_tv_player_url_requires_episode_coordinates():
    with pytest.raises(ValueError, match="season and episode are required"):
        cinezo_client.get_player_url(1399, "tv")


def test_probe_sources_delegates_to_chain():
    class _Chain:
        def probe_sources(self, tmdb_id, media_type, season=None, episode=None):
            return [
                CinezoSourceProbe(
                    name="fixture",
                    endpoint="https://example.test/source",
                    status_code=200,
                    content_type="application/json",
                    available=True,
                    source_shape="source",
                    source_keys=["type", "url"],
                )
            ]

    results = cinezo_client.probe_sources(
        27205,
        "movie",
        resolver_chain=_Chain(),
    )

    assert len(results) == 1
    assert results[0].name == "fixture"
    assert results[0].available is True


def test_get_stream_reaches_default_media_resolver(monkeypatch):
    monkeypatch.setattr(
        cinezo_client,
        "probe_sources",
        lambda *args, **kwargs: [
            CinezoSourceProbe(
                name="zendaya",
                endpoint="https://example.test/source",
                available=True,
            )
        ],
    )
    monkeypatch.setattr(
        cinezo_media,
        "AUTHORIZED_MEDIA_URL",
        "https://example.test/authorized-master.m3u8",
    )

    stream_url, headers, subtitles = cinezo_client.get_stream(27205, "movie")

    assert stream_url == "https://example.test/authorized-master.m3u8"
    assert headers["Referer"] == "https://player.cinezo.live/"
    assert "User-Agent" in headers
    assert subtitles == []


def test_get_stream_uses_first_available_backend(monkeypatch):
    monkeypatch.setattr(
        cinezo_client,
        "probe_sources",
        lambda *args, **kwargs: [
            CinezoSourceProbe(
                name="zendaya",
                endpoint="https://example.test/zendaya",
                available=False,
            ),
            CinezoSourceProbe(
                name="berlin",
                endpoint="https://example.test/berlin",
                available=True,
            ),
        ],
    )
    seen = {}

    def resolver(source, tmdb_id, media_type, season, episode):
        seen["source"] = source.name
        seen["tmdb_id"] = tmdb_id
        seen["media_type"] = media_type
        seen["season"] = season
        seen["episode"] = episode
        return (
            "https://authorized.example/master.m3u8",
            {"Referer": "https://authorized.example/"},
            [{"type": "subtitle", "url": "https://authorized.example/it.vtt"}],
        )

    stream_url, headers, subtitles = cinezo_client.get_stream(
        1399,
        "tv",
        season=1,
        episode=1,
        media_resolver=resolver,
    )

    assert seen == {
        "source": "berlin",
        "tmdb_id": 1399,
        "media_type": "tv",
        "season": 1,
        "episode": 1,
    }
    assert stream_url == "https://authorized.example/master.m3u8"
    assert headers == {"Referer": "https://authorized.example/"}
    assert subtitles == [
        {"type": "subtitle", "url": "https://authorized.example/it.vtt"}
    ]


def test_get_stream_accepts_mapping_from_authorized_resolver(monkeypatch):
    monkeypatch.setattr(
        cinezo_client,
        "probe_sources",
        lambda *args, **kwargs: [
            CinezoSourceProbe(
                name="berlin",
                endpoint="https://example.test/berlin",
                available=True,
            )
        ],
    )

    result = cinezo_client.get_stream(
        27205,
        "movie",
        media_resolver=lambda *args: {
            "url": "https://authorized.example/video.mp4",
            "headers": None,
            "subtitles": None,
        },
    )

    assert result == ("https://authorized.example/video.mp4", {}, [])


def test_get_stream_rejects_invalid_authorized_result(monkeypatch):
    monkeypatch.setattr(
        cinezo_client,
        "probe_sources",
        lambda *args, **kwargs: [
            CinezoSourceProbe(
                name="berlin",
                endpoint="https://example.test/berlin",
                available=True,
            )
        ],
    )

    with pytest.raises(RuntimeError, match="empty stream URL"):
        cinezo_client.get_stream(
            27205,
            "movie",
            media_resolver=lambda *args: {"url": ""},
        )


def test_get_stream_reports_source_failures(monkeypatch):
    monkeypatch.setattr(
        cinezo_client,
        "probe_sources",
        lambda *args, **kwargs: [
            CinezoSourceProbe(
                name="jennifer",
                endpoint="https://example.test/source",
                status_code=530,
                error="HTTP 530",
            )
        ],
    )

    with pytest.raises(RuntimeError, match="jennifer=HTTP 530"):
        cinezo_client.get_stream(27205, "movie")
