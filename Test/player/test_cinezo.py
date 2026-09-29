from VibraVid.player.cinezo import CinezoResolverChain, CinezoStream


class _Resolver:
    def __init__(self, name, result=None, error=None):
        self.name = name
        self.result = result
        self.error = error
        self.calls = 0

    def resolve(self, tmdb_id, media_type, season=None, episode=None):
        self.calls += 1
        if self.error is not None:
            raise self.error
        return self.result


def test_chain_uses_first_successful_resolver():
    first = _Resolver("first", result=None)
    second = _Resolver(
        "second",
        result=CinezoStream(
            url="https://example.test/master.m3u8",
            headers={"Referer": "https://example.test/"},
            subtitles=[{"type": "subtitle", "url": "https://example.test/it.vtt"}],
        ),
    )
    third = _Resolver("third", result=CinezoStream(url="https://unused.test/master.m3u8"))

    result = CinezoResolverChain([first, second, third]).resolve(27205, "movie")

    assert result is not None
    assert result.url == "https://example.test/master.m3u8"
    assert result.headers["Referer"] == "https://example.test/"
    assert first.calls == 1
    assert second.calls == 1
    assert third.calls == 0


def test_chain_continues_after_resolver_exception():
    failing = _Resolver("broken", error=RuntimeError("boom"))
    working = _Resolver("working", result=CinezoStream(url="https://example.test/video.mp4"))

    result = CinezoResolverChain([failing, working]).resolve(27205, "movie")

    assert result is not None
    assert result.url == "https://example.test/video.mp4"
    assert failing.calls == 1
    assert working.calls == 1


def test_default_pending_resolvers_return_no_stream():
    assert CinezoResolverChain().resolve(27205, "movie") is None
