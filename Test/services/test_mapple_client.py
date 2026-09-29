from types import SimpleNamespace

from VibraVid.services.mapple import client


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
