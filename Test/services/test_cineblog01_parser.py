import importlib.util
from pathlib import Path


def _load_scrapper():
    path = Path(__file__).resolve().parents[2] / "VibraVid" / "services" / "cineblog01" / "scrapper.py"
    spec = importlib.util.spec_from_file_location("cineblog01_scrapper_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


scrapper = _load_scrapper()


def test_search_returns_all_cards_and_cleans_titles():
    html = """
    <div class="card mp-post horizontal">
      <img src="/wp-content/uploads/cime-200x300.jpg">
      <div class="card-content">
        <h3 class="card-title">
          <a href="https://cineblog001.download/cime-tempestose-hd-2026/">
            Cime tempestose [HD] (2026)
          </a>
        </h3>
      </div>
    </div>
    <div class="card mp-post horizontal">
      <div class="card-content">
        <h3 class="card-title">
          <a href="/oppenheimer-documentary-hd-2023/">
            To End All War: Oppenheimer &amp; the Atomic Bomb [HD] (2023)
          </a>
        </h3>
      </div>
    </div>
    """

    results = scrapper.parse_search_results(html, "https://cineblog001.download/")

    assert len(results) == 2
    assert results[0].title == "Cime tempestose"
    assert results[0].year == "2026"
    assert results[0].image == "https://cineblog001.download/wp-content/uploads/cime-200x300.jpg"
    assert results[1].title == "To End All War: Oppenheimer & the Atomic Bomb"
    assert results[1].year == "2023"


def test_detail_parser_extracts_table_and_player_sources():
    html = """
    <p><strong>Streaming:</strong></p>
    <table class="tableinside">
      <tr><td><a href="https://uprot.net/msf/abc123">Maxstream</a></td></tr>
      <tr><td><a href="https://stayonline.pro/l/XYZ12/">Mixdrop</a></td></tr>
    </table>

    <p><strong>Streaming HD:</strong></p>
    <table class="tableinside">
      <tr><td><a href="https://uprot.net/msf/hd123">Maxstream HD 1080p</a></td></tr>
    </table>

    <div id="iframen1" class="tabs-catch-all" data-src="https://uprot.net/mse/hd123"></div>
    <div id="iframen2" class="tabs-catch-all" data-src="https://stayonline.pro/e/XYZ12/"></div>
    """

    sources = scrapper.parse_detail_sources(html, "https://cineblog001.download/movie/")

    urls = {source.url: source for source in sources}

    assert "https://uprot.net/msf/abc123" in urls
    assert urls["https://uprot.net/msf/abc123"].section == "streaming"

    assert "https://uprot.net/msf/hd123" in urls
    assert urls["https://uprot.net/msf/hd123"].section == "streaming_hd"

    assert "https://uprot.net/mse/hd123" in urls
    assert urls["https://uprot.net/mse/hd123"].section == "player"
    assert scrapper.source_kind(urls["https://uprot.net/mse/hd123"]) == "maxstream"

    assert "https://stayonline.pro/e/XYZ12/" in urls
    assert urls["https://stayonline.pro/e/XYZ12/"].verification_required is True
    assert scrapper.source_kind(urls["https://stayonline.pro/e/XYZ12/"]) == "stayonline"


def test_vidxgo_player_uses_existing_resolver_kind():
    source = scrapper.CineblogSource(
        section="player",
        label="VidXgo",
        host="v.vidxgo.co",
        url="https://v.vidxgo.co/tt1234567",
    )

    assert scrapper.source_kind(source) == "vidxgo"


def test_direct_hls_is_classified_first():
    source = scrapper.CineblogSource(
        section="player",
        label="HLS",
        host="cdn.example.test",
        url="https://cdn.example.test/video/master.m3u8?token=abc",
    )

    assert scrapper.source_kind(source) == "hls"


def test_iframe_vidxgo_source_is_extracted():
    html = """
    <article>
      <iframe src="https://v.vidxgo.co/tt1234567" title="Player HD"></iframe>
      <iframe src="about:blank"></iframe>
    </article>
    """

    sources = scrapper.parse_detail_sources(html, "https://cineblog001.download/movie/")

    assert len(sources) == 1
    assert sources[0].url == "https://v.vidxgo.co/tt1234567"
    assert sources[0].section == "player"
    assert scrapper.source_kind(sources[0]) == "vidxgo"


def test_search_falls_back_to_standard_wordpress_entry_title():
    html = """
    <article class="post type-post">
      <h2 class="entry-title">
        <a href="/film-esempio-hd-2025/">Film esempio [HD] (2025)</a>
      </h2>
      <img data-lazy-src="/poster.jpg">
    </article>
    """

    results = scrapper.parse_search_results(html, "https://cineblog001.download/")

    assert len(results) == 1
    assert results[0].title == "Film esempio"
    assert results[0].year == "2025"
    assert results[0].image == "https://cineblog001.download/poster.jpg"


def test_direct_video_source_is_extracted_and_classified():
    html = """
    <video src="https://cdn.example.test/movie/master.m3u8?token=abc"></video>
    """

    sources = scrapper.parse_detail_sources(html, "https://cineblog001.download/movie/")

    assert len(sources) == 1
    assert sources[0].section == "player"
    assert scrapper.source_kind(sources[0]) == "hls"
