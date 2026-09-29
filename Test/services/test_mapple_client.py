from types import SimpleNamespace

from VibraVid.services.mapple import client, downloader


def test_get_stream_normalizes_resolved_stream(monkeypatch):
    class FakeResolver:
        def resolve_stream(self, tmdb_id, media_type, season=None, episode=None):
            assert tmdb_id == 27205
            assert media_type == "movie"
            return SimpleNamespace(
                url="https://cdn.example/master.m3u8",
                headers={
                    "Referer": "https://mapple.fun/",
                    "Origin": "https://mapple.fun",
                },
            )

    monkeypatch.setattr(client, "MappleResolver", FakeResolver)

    url, headers, subtitles = client.get_stream(27205, "movie")

    assert url == "https://cdn.example/master.m3u8"
    assert headers["Origin"] == "https://mapple.fun"
    assert subtitles == []


def test_download_hls_forces_curl_cffi_segments(monkeypatch):
    captured = {}

    class FakeDownloader:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        def start(self):
            return "ok"

    monkeypatch.setattr(downloader, "HLS_Downloader", FakeDownloader)

    result = downloader._download_hls(
        "https://cdn.example/master.m3u8",
        {"Referer": "https://mapple.fun/"},
        [],
        "/tmp/mapple.mkv",
    )

    assert result == "ok"
    assert captured["use_curl_cffi_segments"] is True
    assert captured["hls_playlist_retry_statuses"] == (404,)
    assert captured["hls_playlist_retry_attempts"] == 3
