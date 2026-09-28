# 27.09.26

from urllib.parse import urljoin, urlsplit

from VibraVid.utils import config_manager
from VibraVid.utils.http_client import create_client, get_headers

DOMAIN_KEYS = ("Cineblog_1", "Cineblog_2", "Cineblog_3")


def get_base_urls() -> list[str]:
    """Return configured CB01 mirrors in tracker order."""
    urls: list[str] = []

    for domain_key in DOMAIN_KEYS:
        section = config_manager.domain.get_section(domain_key)
        full_url = str(section.get("full_url") or "").strip()
        if not full_url:
            continue

        normalized = full_url.rstrip("/") + "/"
        if normalized not in urls:
            urls.append(normalized)

    if not urls:
        raise ValueError(
            "No Cineblog01 domain is available in the VibraVid domain tracker "
            f"({', '.join(DOMAIN_KEYS)})"
        )

    return urls


def get_base_url() -> str:
    """Return the first configured CB01 mirror."""
    return get_base_urls()[0]


def _relative_target(url: str) -> str:
    """Convert an absolute CB01 URL to a mirror-relative target."""
    parsed = urlsplit(url)
    if not parsed.scheme and not parsed.netloc:
        return url.lstrip("/")

    target = parsed.path.lstrip("/")
    if parsed.query:
        target = f"{target}?{parsed.query}"
    return target


def _fetch_with_fallback(target: str, *, params: dict | None = None) -> tuple[str, str]:
    """Fetch a public CB01 page, trying configured mirrors in tracker order."""
    last_error: Exception | None = None

    with create_client(headers=get_headers()) as client:
        for base_url in get_base_urls():
            try:
                response = client.get(
                    urljoin(base_url, target),
                    params=params,
                    timeout=20,
                )
                response.raise_for_status()
                return response.text, str(response.url)
            except Exception as exc:
                last_error = exc

    if last_error is not None:
        raise last_error

    raise RuntimeError("No Cineblog01 mirror could be attempted")


def fetch_search_page(query: str) -> tuple[str, str]:
    """Fetch the public search page and return HTML plus final URL."""
    return _fetch_with_fallback(
        "index.php",
        params={
            "story": query,
            "do": "search",
            "subaction": "search",
        },
    )


def fetch_detail_page(url: str) -> tuple[str, str]:
    """Fetch one public CB01 detail page and return HTML plus final URL."""
    return _fetch_with_fallback(_relative_target(url))
