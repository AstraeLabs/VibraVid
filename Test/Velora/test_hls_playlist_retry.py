from types import SimpleNamespace

import pytest

import VibraVid.core.velora._stream_vod as stream_vod
from VibraVid.core.velora._stream_vod import VodStreamMixin


class DummyVod(VodStreamMixin):
    def __init__(self, statuses, attempts):
        self.hls_playlist_retry_statuses = statuses
        self.hls_playlist_retry_attempts = attempts


class FakeHttpError(RuntimeError):
    def __init__(self, status_code):
        super().__init__("HTTP %s" % status_code)
        self.response = SimpleNamespace(status_code=status_code)


def test_hls_playlist_retries_configured_status(monkeypatch):
    calls = []

    def fake_get_with_retry(client, url):
        calls.append(url)
        if len(calls) < 3:
            raise FakeHttpError(404)
        return SimpleNamespace(text="#EXTM3U")

    monkeypatch.setattr(stream_vod, "get_with_retry", fake_get_with_retry)
    monkeypatch.setattr(stream_vod.time, "sleep", lambda _: None)

    downloader = DummyVod((404,), 3)
    response = downloader._get_hls_playlist(object(), "https://cdn.example/high.m3u8")

    assert response.text == "#EXTM3U"
    assert len(calls) == 3


def test_hls_playlist_does_not_retry_unconfigured_status(monkeypatch):
    calls = []

    def fake_get_with_retry(client, url):
        calls.append(url)
        raise FakeHttpError(403)

    monkeypatch.setattr(stream_vod, "get_with_retry", fake_get_with_retry)
    monkeypatch.setattr(stream_vod.time, "sleep", lambda _: None)

    downloader = DummyVod((404,), 3)

    with pytest.raises(FakeHttpError):
        downloader._get_hls_playlist(object(), "https://cdn.example/high.m3u8")

    assert len(calls) == 1
