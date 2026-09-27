# 27.09.26

from urllib.parse import urljoin

from VibraVid.utils import config_manager
from VibraVid.utils.http_client import create_client, get_headers

DOMAIN_KEY = "cb01new"


def get_base_url() -> str:
    """Return the current CB01 base URL resolved by ConfigManager."""
    return config_manager.domain.get(DOMAIN_KEY, "full_url").rstrip("/") + "/"


def fetch_search_page(query: str) -> tuple[str, str]:
    """Fetch the public WordPress search page and return HTML plus final URL."""
    base_url = get_base_url()
    with create_client(headers=get_headers()) as client:
        response = client.get(base_url, params={"s": query}, timeout=20)
        response.raise_for_status()
    return response.text, str(response.url)


def fetch_detail_page(url: str) -> tuple[str, str]:
    """Fetch one CB01 detail page and return HTML plus final URL."""
    target = urljoin(get_base_url(), url)
    with create_client(headers=get_headers()) as client:
        response = client.get(target, timeout=20)
        response.raise_for_status()
    return response.text, str(response.url)
