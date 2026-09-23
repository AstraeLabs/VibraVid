# 09.08.25

import asyncio
import functools
import json
import logging
import os
import urllib.request
from contextlib import asynccontextmanager, contextmanager

import ua_generator
from curl_cffi import requests
from curl_cffi.const import CurlHttpVersion
from curl_cffi.requests.impersonate import REAL_TARGET_MAP

from VibraVid.utils import config_manager

logger = logging.getLogger(__name__)
ua = ua_generator.generate(device="desktop", browser=("chrome", "edge"))
_VALID_PROXY_SCOPES = ("scrap", "down", "scrap+down")


def _use_proxy() -> bool:
    try:
        return bool(config_manager.config.get_bool("REQUESTS", "use_proxy", default=False))
    except Exception:
        return False


def _get_proxy_scope() -> str:
    try:
        scope = config_manager.config.get("REQUESTS", "proxy_scope", str, default="scrap+down")
        scope = (scope or "").strip().lower()
        return scope if scope in _VALID_PROXY_SCOPES else "scrap+down"
    except Exception:
        return "scrap+down"


def _get_timeout() -> int:
    try:
        return int(config_manager.config.get_int("REQUESTS", "timeout"))
    except Exception:
        return 20


def _get_verify() -> bool:
    try:
        return bool(config_manager.config.get_bool("REQUESTS", "verify"))
    except Exception:
        return True


def _ca_bundle_path() -> str | None:
    """Resolve an explicit CA bundle for curl_cffi to verify against."""
    for env in ("CURL_CA_BUNDLE", "SSL_CERT_FILE"):
        p = os.environ.get(env)
        if p and os.path.isfile(p):
            return p
    try:
        import certifi

        path = certifi.where()
        if path and os.path.isfile(path):
            return path
    except Exception:
        pass

    for path in (
        "/etc/ssl/certs/ca-certificates.crt",  # Debian/Ubuntu (container)
        "/etc/pki/tls/certs/ca-bundle.crt",  # RHEL/CentOS/Fedora
        "/etc/ssl/ca-bundle.pem",  # OpenSUSE
        "/etc/ssl/cert.pem",  # Alpine/macOS
    ):
        if os.path.isfile(path):
            return path
    return None


def _resolve_verify(verify: bool | str | None) -> bool | str:
    """Normalize the requested verify setting into a value curl_cffi accepts."""
    if verify is None:
        verify = _get_verify()
    if verify is False:
        return False
    if isinstance(verify, str):
        return verify
    return _ca_bundle_path() or True


def _raw_proxies() -> dict[str, str] | None:
    if not _use_proxy():
        return None

    try:
        proxies = config_manager.config.get_dict("REQUESTS", "proxy", default={})
        if not isinstance(proxies, dict):
            return None

        # Normalize — drop empty strings
        cleaned: dict[str, str] = {
            scheme: url.strip() for scheme, url in proxies.items() if isinstance(url, str) and url.strip()
        }
        return cleaned or None
    except Exception:
        return None


def _get_proxies() -> dict[str, str] | None:
    if _get_proxy_scope() not in ("scrap", "scrap+down"):
        return None
    return _raw_proxies()


def get_proxy_url() -> str | None:
    if _get_proxy_scope() not in ("down", "scrap+down"):
        return None
    proxies = _raw_proxies()
    if not proxies:
        return None
    return proxies.get("https") or proxies.get("http") or next(iter(proxies.values()), None)


def _default_headers(extra: dict[str, str] | None = None) -> dict[str, str]:
    headers = {}

    if not extra or "user-agent" not in {k.lower() for k in extra.keys()}:
        headers["User-Agent"] = get_userAgent()

    if extra:
        headers.update(extra)

    return headers


def get_available_browsers() -> dict[str, str]:
    """Get the latest available browser impersonate versions."""
    return dict(REAL_TARGET_MAP)


def get_browser_impersonate(browser: str = "chrome") -> str | None:
    """Get the latest available browser impersonate version from curl_cffi."""
    available = get_available_browsers()
    result = available.get(browser.lower())
    if result is None:
        logger.warning(f"Browser '{browser}' not found in impersonate map, falling back to 'chrome'.")
        result = available.get("chrome")
    return result


def create_client(
    *,
    headers: dict[str, str] | None = None,
    cookies: dict[str, str] | None = None,
    timeout: int | float | None = None,
    verify: bool | None = None,
    proxies: dict[str, str] | None = None,
    http2: bool = False,
    follow_redirects: bool = True,
    browser: str | None = "chrome",
) -> requests.Session:
    """Factory for a configured curl_cffi session."""
    session = requests.Session()
    if browser:
        if headers:
            session.headers.update(headers)
    else:
        session.headers.update(_default_headers(headers))

    if cookies:
        session.cookies.update(cookies)

    session.timeout = timeout if timeout is not None else _get_timeout()
    session.verify = _resolve_verify(verify)

    proxy_value = proxies if proxies is not None else _get_proxies()
    if proxy_value:
        session.proxies = proxy_value

    if http2:
        session.http_version = CurlHttpVersion.V2TLS

    if browser:
        impersonate = get_browser_impersonate(browser)
        if impersonate:
            session.impersonate = impersonate

    session.allow_redirects = follow_redirects

    return session


def get_with_retry(session: requests.Session, url: str, retries: int | None = None, **kwargs) -> requests.Response:
    """GET *url* retrying with backoff on network errors and 5xx responses; 4xx are raised immediately.
    Meant for small manifest/playlist requests: some hosts (e.g. vixcloud) answer most of them with a transient 500."""
    import time

    if retries is None:
        retries = max(config_manager.config.get_int("REQUESTS", "max_retry"), 15)

    for attempt in range(retries + 1):
        try:
            resp = session.get(url, **kwargs)
            if resp.status_code < 500:
                resp.raise_for_status()
                return resp
            exc: Exception = requests.exceptions.HTTPError(f"HTTP Error {resp.status_code}", response=resp)
        except requests.exceptions.HTTPError:
            raise
        except Exception as e:
            exc = e

        if attempt >= retries:
            raise exc
        logger.debug(f"GET {url} failed ({exc}), retry {attempt + 1}/{retries}")
        time.sleep(min(0.5 * (attempt + 1), 2.0))


@contextmanager
def open_client(
    *,
    headers: dict[str, str] | None = None,
    cookies: dict[str, str] | None = None,
    timeout: int | float | None = None,
    verify: bool | None = None,
    proxies: dict[str, str] | None = None,
    http2: bool = False,
    follow_redirects: bool = True,
    browser: str | None = "chrome",
):
    """Context-manager wrapper around :func:`create_client`"""
    session = create_client(
        headers=headers,
        cookies=cookies,
        timeout=timeout,
        verify=verify,
        proxies=proxies,
        http2=http2,
        follow_redirects=follow_redirects,
        browser=browser,
    )
    try:
        yield session
    finally:
        session.close()


class AsyncStreamResponse:
    """Wrapper for streaming responses in async context."""

    def __init__(self, response):
        self.response = response
        self.headers = response.headers
        self.status_code = response.status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    async def aiter_bytes(self, chunk_size: int = 8192):
        """Iterate over response content in chunks asynchronously."""
        for chunk in self.response.iter_content(chunk_size=chunk_size):
            yield chunk
            await asyncio.sleep(0)


class AsyncClient:
    """Async wrapper for curl_cffi client."""

    def __init__(self, session):
        self.session = session

    @asynccontextmanager
    async def stream(self, method: str, url: str, **kwargs):
        """Stream request wrapper for async context."""
        loop = asyncio.get_running_loop()
        response = await loop.run_in_executor(
            None,
            functools.partial(self.session.request, method, url, stream=True, **kwargs),
        )
        try:
            yield AsyncStreamResponse(response)
        finally:
            response.close()

    async def get(self, url: str, **kwargs):
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, functools.partial(self.session.get, url, **kwargs))

    async def post(self, url: str, **kwargs):
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, functools.partial(self.session.post, url, **kwargs))

    async def put(self, url: str, **kwargs):
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, functools.partial(self.session.put, url, **kwargs))

    async def delete(self, url: str, **kwargs):
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, functools.partial(self.session.delete, url, **kwargs))

    async def patch(self, url: str, **kwargs):
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, functools.partial(self.session.patch, url, **kwargs))

    async def head(self, url: str, **kwargs):
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, functools.partial(self.session.head, url, **kwargs))

    async def request(self, method: str, url: str, **kwargs):
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, functools.partial(self.session.request, method, url, **kwargs))

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        self.session.close()


@asynccontextmanager
async def create_async_client(
    *,
    headers: dict[str, str] | None = None,
    cookies: dict[str, str] | None = None,
    timeout: int | float | None = None,
    verify: bool | None = None,
    proxies: dict[str, str] | None = None,
    http2: bool = False,
    follow_redirects: bool = True,
    browser: str = "chrome",
):
    """Context-manager factory for an async-compatible curl_cffi session wrapper."""
    session = requests.Session()
    if browser:
        if headers:
            session.headers.update(headers)
    else:
        session.headers.update(_default_headers(headers))

    if cookies:
        session.cookies.update(cookies)

    session.timeout = timeout if timeout is not None else _get_timeout()
    session.verify = _resolve_verify(verify)

    proxy_value = proxies if proxies is not None else _get_proxies()
    if proxy_value:
        session.proxies = proxy_value

    if http2:
        session.http_version = CurlHttpVersion.V2TLS

    if browser:
        impersonate = get_browser_impersonate(browser)
        if impersonate:
            session.impersonate = impersonate

    session.allow_redirects = follow_redirects

    try:
        yield AsyncClient(session)
    finally:
        session.close()


def fetch_image_bytes(url: str, timeout: int = 10) -> bytes | None:
    """Download raw image bytes from a URL (cover art, posters, stills, ...)."""
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return resp.read()
    except Exception as e:
        logger.warning(f"Could not fetch image: {e}")
        return None


def get_userAgent() -> str:
    return ua_generator.generate().text


def get_headers() -> dict:
    return ua.headers.get()


def get_my_location() -> dict:
    cache_dir = os.path.join(config_manager.base_path, ".cache")
    cache_file = os.path.join(cache_dir, "ip.json")

    try:
        url = "http://ip-api.com/json/?fields=status,country,countryCode,city,query"

        with open_client(headers=get_headers()) as c:
            response = c.get(url, timeout=4)

        data = response.json()

        if data.get("status") == "success":
            location = {
                "country": data["country"],
                "country_code": data["countryCode"],
                "city": data["city"],
                "ip": data["query"],
            }

            try:
                os.makedirs(cache_dir, exist_ok=True)
                with open(cache_file, "w", encoding="utf-8") as f:
                    json.dump(location, f, indent=4)
            except Exception as e:
                logger.warning(f"Could not cache location data: {e}")

            return location

        return {"status": "fail", "country_code": "XX", "ip": "0.0.0.0"}

    except Exception as e:
        return {"status": "fail", "country_code": "XX", "ip": "0.0.0.0", "error": str(e)}


def check_region_availability(allowed_regions: list, site_name: str) -> bool:
    try:
        logger.info(f"Checking region availability for {site_name}...")
        location = get_my_location()
        if location.get("status") == "fail" or "error" in location:
            logger.warning(f"Region check skipped or failed for {site_name}: {location.get('error', 'Unknown error')}")
            return True

        current_country = location.get("country_code")
        logger.info(f"Current detected region: {current_country}")

        if current_country and current_country not in allowed_regions:
            print(f"Site: {site_name} is not available in your region ({current_country}).")
            logger.error(f"Site: {site_name}, unavailable outside {', '.join(allowed_regions)}.")
            return False
    except Exception as e:
        logger.error("Region check failed: %s", e)

    return True
