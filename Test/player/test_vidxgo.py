import importlib
import sys

from VibraVid.utils import config_manager


def test_vidxgo_import_does_not_require_guardaserie(monkeypatch):
    original_get = config_manager.domain.get

    def domain_get(section, key, *args, **kwargs):
        if section == "guardaserie":
            raise ValueError("guardaserie is unavailable")
        return original_get(section, key, *args, **kwargs)

    monkeypatch.setattr(config_manager.domain, "get", domain_get)
    sys.modules.pop("VibraVid.player.vidxgo", None)

    module = importlib.import_module("VibraVid.player.vidxgo")

    assert "Referer" not in module.VIDXGO_HEADERS


def test_vidxgo_uses_explicit_referer(monkeypatch):
    from VibraVid.player.vidxgo import VideoSource

    captured = {}

    class Response:
        text = ""

        def raise_for_status(self):
            return None

    class Client:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return None

        def get(self, url, headers=None, timeout=None):
            captured["url"] = url
            captured["headers"] = headers
            captured["timeout"] = timeout
            return Response()

    monkeypatch.setattr("VibraVid.player.vidxgo.create_client", lambda: Client())
    monkeypatch.setattr(
        VideoSource,
        "decode_embed_html",
        staticmethod(lambda text: "https://cdn.example/stream.m3u8"),
    )

    source = VideoSource(
        "tt1234567",
        content_type="movie",
        referer="https://example.test/movie",
    )
    playlist = source.get_playlist()

    assert playlist == "https://cdn.example/stream.m3u8"
    assert captured["headers"]["Referer"] == "https://example.test/movie"
