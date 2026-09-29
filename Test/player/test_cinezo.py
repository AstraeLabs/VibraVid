from VibraVid.player import cinezo as cinezo_player


class _Response:
    def __init__(self, status_code=200, content_type="application/json", data=None):
        self.status_code = status_code
        self.ok = 200 <= status_code < 400
        self.headers = {"content-type": content_type}
        self._data = data

    def json(self):
        if isinstance(self._data, Exception):
            raise self._data
        return self._data


class _Client:
    def __init__(self, response):
        self.response = response

    def get(self, *args, **kwargs):
        return self.response

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


def test_current_source_endpoints():
    assert cinezo_player.ZendayaResolver().build_endpoint(27205, "movie") == (
        "https://proxy3.flikhub.net/movie?id=27205&mode=json&sources=zendaya&hevc=1"
    )
    assert cinezo_player.BerlinResolver().build_endpoint(1399, "tv", 1, 2) == (
        "https://proxy1.flikhub.net/tv?id=1399&season=1&episode=2&mode=json&sources=berlin&hevc=1"
    )
    assert cinezo_player.JenniferResolver().build_endpoint(27205, "movie") == (
        "https://media.vidcool.net/movie/27205.json"
    )
    assert cinezo_player.CinefreakResolver().build_endpoint(27205, "movie") == (
        "https://proxy1.flikhub.net/movie?id=27205&mode=json&sources=cinefreak&hevc=1"
    )


def test_standard_source_probe_detects_availability_without_returning_url(monkeypatch):
    response = _Response(data={
        "source": {
            "label": "Zendaya",
            "source": "zendaya",
            "type": "hls",
            "url": "https://example.test/private.m3u8",
        }
    })
    monkeypatch.setattr(
        cinezo_player,
        "create_client",
        lambda **kwargs: _Client(response),
    )

    result = cinezo_player.ZendayaResolver().probe(27205, "movie")

    assert result.available is True
    assert result.source_shape == "source"
    assert result.source_keys == ["label", "source", "type", "url"]
    assert not hasattr(result, "url")


def test_jennifer_probe_understands_stream_shape(monkeypatch):
    response = _Response(data={
        "stream": {
            "original": "https://example.test/private.m3u8",
            "hls": None,
        }
    })
    monkeypatch.setattr(
        cinezo_player,
        "create_client",
        lambda **kwargs: _Client(response),
    )

    result = cinezo_player.JenniferResolver().probe(27205, "movie")

    assert result.available is True
    assert result.source_shape == "stream"
    assert result.source_keys == ["hls", "original"]


def test_probe_records_http_failure(monkeypatch):
    monkeypatch.setattr(
        cinezo_player,
        "create_client",
        lambda **kwargs: _Client(_Response(status_code=530, content_type="text/html")),
    )

    result = cinezo_player.JenniferResolver().probe(27205, "movie")

    assert result.available is False
    assert result.status_code == 530
    assert result.error == "HTTP 530"


def test_chain_returns_all_source_results(monkeypatch):
    monkeypatch.setattr(
        cinezo_player,
        "create_client",
        lambda **kwargs: _Client(_Response(data={"source": None})),
    )

    results = cinezo_player.CinezoResolverChain(
        [
            cinezo_player.ZendayaResolver(),
            cinezo_player.BerlinResolver(),
        ]
    ).probe_sources(27205, "movie")

    assert [result.name for result in results] == ["zendaya", "berlin"]
    assert all(result.available is False for result in results)
