# 27.09.26

from urllib.parse import urljoin

from VibraVid.utils import config_manager
from VibraVid.utils.http_client import create_client, get_headers

DOMAIN_KEYS = ("Cineblog_1", "Cineblog_2", "Cineblog_3")


def get_base_url() -> str:
    """Return the preferred CB01 mirror from the standard VibraVid domain tracker."""
    candidates: list[tuple[str, int | None]] = []

    for domain_key in DOMAIN_KEYS:
        section = config_manager.domain.get_section(domain_key)
        full_url = str(section.get("full_url") or "").strip()
        if not full_url:
            continue

        candidates.append((full_url, section.get("last_status")))

    for full_url, last_status in candidates:
        if last_status == 200:
            return full_url.rstrip("/") + "/"

    if candidates:
        return candidates[0][0].rstrip("/") + "/"

    raise ValueError(
        "No Cineblog01 domain is available in the VibraVid domain tracker "
        f"({', '.join(DOMAIN_KEYS)})"
    )


def fetch_search_page(query: str) -> tuple[str, str]:
    """Fetch the public WordPress search page and return HTML plus final URL."""
    base_url = get_base_url()
    with create_client(headers=get_headers()) as client:
        response = client.get(
            urljoin(base_url, "index.php"),
            params={
                "story": query,
                "do": "search",
                "subaction": "search",
            },
            timeout=20,
        )
        response.raise_for_status()
    return response.text, str(response.url)


def fetch_detail_page(url: str) -> tuple[str, str]:
    """Fetch one CB01 detail page and return HTML plus final URL."""
    target = urljoin(get_base_url(), url)
    with create_client(headers=get_headers()) as client:
        response = client.get(target, timeout=20)
        response.raise_for_status()
    return response.text, str(response.url)
