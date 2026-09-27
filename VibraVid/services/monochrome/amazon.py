# 16.07.26
# by @danpro00

import logging
import os
import threading
import time

from VibraVid.utils import config_manager, disk_cache
from VibraVid.utils.http_client import create_client

logger = logging.getLogger(__name__)

MONOCHROME_ORIGIN = "https://monochrome.tf"
AMAZON_API_URL = "https://amz.geeked.wtf/api/track/"
AMAZON_TURNSTILE_EXCHANGE_URL = "https://amz.geeked.wtf/api/auth/turnstile"
TURNSTILE_SITE_KEY = "0x4AAAAAADgxqF6QVMm0GLHH"
TURNSTILE_ACTION = "auth"

_JWT_TTL_SECONDS = 3600
_TURNSTILE_TIMEOUT_SECONDS = 40
_CACHE_SERVICE = "monochrome"
_CACHE_NAME = "turnstile_jwt"


class AmazonError(Exception):
    """Raised when the Amazon Music resolve path cannot produce a usable stream."""


class _JWTCache:
    """Process-wide cache for the exchanged Turnstile JWT, persisted to disk_cache."""
    _lock = threading.Lock()
    _token: str | None = None
    _expiry: float = 0.0
    _loaded = False

    @classmethod
    def _ensure_loaded(cls) -> None:
        if cls._loaded:
            return
        cls._loaded = True
        data = disk_cache.load(_CACHE_SERVICE, _CACHE_NAME)
        if data:
            cls._token = data.get("token")
            cls._expiry = float(data.get("expiry") or 0)

    @classmethod
    def get(cls) -> str | None:
        with cls._lock:
            cls._ensure_loaded()
            if cls._token and disk_cache.is_fresh({"expiry": cls._expiry}, buffer_seconds=60):
                return cls._token
        return None

    @classmethod
    def set(cls, token: str, ttl: int = _JWT_TTL_SECONDS) -> None:
        with cls._lock:
            cls._token = token
            cls._expiry = time.time() + ttl
            cls._loaded = True
            disk_cache.save(_CACHE_SERVICE, _CACHE_NAME, {"token": cls._token, "expiry": cls._expiry})


def _bypasser_url() -> str | None:
    """Resolve the bypasser sidecar endpoint from env (compose) or config."""
    url = os.environ.get("BYPASSER_URL")
    if not url:
        try:
            url = config_manager.config.get("REQUESTS", "bypasser_url", str, default="")
        except Exception:
            url = ""
    url = (url or "").strip().rstrip("/")
    return url or None


def _solve_via_bypasser(url: str, timeout: int) -> str:
    """Solve the Turnstile widget via the docker/bypasser sidecar over HTTP."""
    endpoint = _bypasser_url()
    logger.info(f"[monochrome/amazon] solving Cloudflare Turnstile via bypasser sidecar ({endpoint})")
    client = create_client(browser=None, timeout=timeout + 15, proxies={})
    try:
        try:
            resp = client.post(
                f"{endpoint}/solve",
                json={"url": url, "sitekey": TURNSTILE_SITE_KEY, "action": TURNSTILE_ACTION, "timeout": timeout},
            )
        except Exception as e:
            raise AmazonError(
                f"Could not reach the bypasser sidecar at {endpoint} ({e}). "
                "It is required to solve monochrome.tf's Turnstile and is not running. "
                "Start it with 'docker compose --profile bypasser up -d bypasser' "
                "(see docker/bypasser/ and the bypasser_url setting in README.md)."
            ) from e
        resp.raise_for_status()
        data = resp.json()
    finally:
        client.close()

    if str(data.get("status")) != "ok" or not data.get("token"):
        raise AmazonError(f"bypasser did not solve: {data.get('message') or data}")

    logger.info(f"[monochrome/amazon] Turnstile token obtained via bypasser in {data.get('elapsed')}s.")
    return data["token"]


def _acquire_turnstile_response(timeout: int = _TURNSTILE_TIMEOUT_SECONDS) -> str:
    """Solve monochrome.tf's Turnstile widget via the docker bypasser sidecar."""
    endpoint = _bypasser_url()
    if not endpoint:
        raise AmazonError(
            "The monochrome Amazon Music download requires the bypasser sidecar, "
            "but no endpoint is configured. Set BYPASSER_URL (e.g. "
            "http://bypasser:8192 in docker-compose, or http://localhost:8192 "
            "locally) or REQUESTS.bypasser_url in config.json."
        )
    return _solve_via_bypasser(MONOCHROME_ORIGIN, timeout)


def _exchange_turnstile_response(cf_turnstile_response: str) -> str:
    """POST the solved Turnstile token, get back the X-Turnstile-JWT."""
    client = create_client(headers={"Origin": MONOCHROME_ORIGIN})
    try:
        resp = client.post(
            AMAZON_TURNSTILE_EXCHANGE_URL,
            json={"cf_turnstile_response": cf_turnstile_response},
            timeout=20,
        )
        resp.raise_for_status()
        data = resp.json()
    finally:
        client.close()

    token = data.get("access_token")
    if not token:
        raise AmazonError(f"Turnstile exchange returned no access_token: {data}")
    _JWTCache.set(token)
    logger.info("[monochrome/amazon] Turnstile exchanged for JWT (cached ~1h).")
    return token


def get_jwt(force_refresh: bool = False) -> str:
    """Return a usable X-Turnstile-JWT, solving Turnstile + exchanging it if needed."""
    if not force_refresh:
        cached = _JWTCache.get()
        if cached:
            return cached
    cf_response = _acquire_turnstile_response()
    return _exchange_turnstile_response(cf_response)


def get_track_link(title: str, duration: int, album: str, artist: str, quality: str = "UHD") -> dict:
    """Resolve an Amazon Music stream for a track matched by title/artist/album/duration."""
    params = {"track": title, "duration": duration, "album": album, "artist": artist, "quality": quality}
    jwt = get_jwt()
    client = create_client(headers={"Origin": MONOCHROME_ORIGIN})
    try:
        resp = client.get(AMAZON_API_URL, headers={"X-Turnstile-JWT": jwt}, params=params, timeout=30)

        # Retry once if the JWT was rejected (401) — it may have expired or been revoked.
        if resp.status_code == 401:
            logger.info("[monochrome/amazon] JWT rejected, refreshing and retrying once")
            jwt = get_jwt(force_refresh=True)
            resp = client.get(AMAZON_API_URL, headers={"X-Turnstile-JWT": jwt}, params=params, timeout=30)

        # Raise an error for any non-200 response, including 404 (track not found) or 403 (quality not available).
        if resp.status_code >= 400:
            detail = ""
            try:
                body = resp.json()
                if isinstance(body, dict):
                    detail = str(body.get("message") or body.get("error") or "").strip()
            except Exception:
                detail = (resp.text or "")[:200].strip()
            raise AmazonError(f"amz.geeked.wtf HTTP {resp.status_code}" + (f": {detail}" if detail else ""))

        return resp.json()
    finally:
        client.close()


def extract_stream_url(amazon_response: dict) -> str | None:
    """Pull the CDN stream url out of an amz.geeked.wtf track response."""
    for key in ("stream_url", "url", "download_url", "link"):
        val = amazon_response.get(key)
        if val:
            return val
    for key in ("data", "result"):
        nested = amazon_response.get(key)
        if isinstance(nested, dict) and nested.get("url"):
            return nested["url"]
    return None


def extract_decryption_key(amazon_response: dict) -> str | None:
    """Hex AES-128 key (CENC) if the track is encrypted; None if it's in the clear."""
    return amazon_response.get("decryption_key") or None
