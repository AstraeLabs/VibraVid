from VibraVid.utils import _startup_prefetch


def test_extract_cb01_domain_from_updated_section():
    page = """
    <p>Old reference: https://cb01.example/</p>
    <h2><span id="cb01-nuovo-indirizzo-aggiornato">CB01 nuovo indirizzo aggiornato</span></h2>
    <p>Per raggiungere il sito:</p>
    <p><strong>https://cineblog001.download/</strong></p>
    """

    assert _startup_prefetch._extract_cb01_domain(page) == "https://cineblog001.download/"


def test_extract_cb01_domain_normalizes_to_origin():
    page = """
    <span id="cb01-nuovo-indirizzo-aggiornato"></span>
    <p>https://cineblog001.download/some/path?from=article</p>
    """

    assert _startup_prefetch._extract_cb01_domain(page) == "https://cineblog001.download/"


def test_extract_cb01_domain_accepts_cb01_hostname():
    page = """
    <span id="cb01-nuovo-indirizzo-aggiornato"></span>
    <p>https://cb01uno.top/</p>
    """

    assert _startup_prefetch._extract_cb01_domain(page) == "https://cb01uno.top/"


def test_extract_cb01_domain_returns_none_when_missing():
    page = """
    <span id="cb01-nuovo-indirizzo-aggiornato"></span>
    <p>https://example.com/</p>
    """

    assert _startup_prefetch._extract_cb01_domain(page) is None
