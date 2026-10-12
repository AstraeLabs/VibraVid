# 16.12.25

import base64
import json
import time
import uuid

from VibraVid.services._base.login_status import Login
from VibraVid.utils import config_manager, disk_cache
from VibraVid.utils.http_client import create_client, get_headers, get_userAgent

tubi_email = config_manager.login.get("tubitv", "email")
tubi_password = config_manager.login.get("tubitv", "password")

_cached_token = None
_FALLBACK_TTL_SECONDS = 3600


def generate_device_id():
    """Generate a unique device ID"""
    return str(uuid.uuid4())


def _jwt_expiry(token: str) -> float | None:
    """Return a JWT's `exp` claim (epoch seconds), or None if undecodable."""
    try:
        part = token.split(".")[1]
        part += "=" * (4 - len(part) % 4)
        payload = json.loads(base64.urlsafe_b64decode(part))
        return float(payload["exp"])
    except (IndexError, ValueError, KeyError, TypeError, json.JSONDecodeError):
        return None


def get_bearer_token():
    """Get the Bearer token required for Tubi TV authentication"""
    global _cached_token

    # Try memory cache
    if _cached_token:
        return _cached_token

    # Try disk cache
    data = disk_cache.load_fresh("tubitv", "token", buffer_seconds=60)
    if data and data.get("access_token"):
        _cached_token = data["access_token"]
        Login.refresh_cached(disk_cache.cache_path("tubitv", "token"))
        return _cached_token

    if not tubi_email or not tubi_password:
        raise Exception("Email or Password not set in configuration.")

    json_data = {
        "type": "email",
        "platform": "web",
        "device_id": generate_device_id(),
        "credentials": {"email": str(tubi_email).strip(), "password": str(tubi_password).strip()},
    }

    with create_client(headers=get_headers()) as client:
        response = client.post("https://account.production-public.tubi.io/user/login", json=json_data)

    if response.status_code == 503:
        raise Exception("Service Unavailable: Set VPN to America.")

    login_data = response.json()
    Login.show(Login.ACCOUNT, user=login_data.get("name") or login_data.get("email") or str(tubi_email).strip())

    _cached_token = login_data["access_token"]
    expiry = _jwt_expiry(_cached_token) or (time.time() + _FALLBACK_TTL_SECONDS)
    login_data["expiry"] = expiry
    disk_cache.save("tubitv", "token", login_data)
    return _cached_token


def get_playback_url(content_id: str, bearer_token: str) -> tuple[str, str | None, dict | None]:
    """
    Get the playback URL (HLS) and license URL for a given content ID.

    Parameters:
        content_id (str): ID of the video content
        bearer_token (str): Bearer token for authentication

    Returns:
        - Tuple[str, Optional[str], Optional[dict]]: (master_playlist_url, license_url, headers)
    """
    headers = {
        "authorization": f"Bearer {bearer_token}",
        "user-agent": get_userAgent(),
    }

    params = {
        "content_id": content_id,
        "limit_resolutions[]": [
            "h264_1080p",
            "h265_1080p",
        ],
        "video_resources[]": ["dash", "dash_widevine"],
    }

    with create_client(headers=headers) as client:
        response = client.get("https://content-cdn.production-public.tubi.io/api/v2/content", params=params)

    json_data = response.json()
    master_playlist_url = json_data["video_resources"][0]["manifest"]["url"]

    license_url = None
    if "license_server" in json_data["video_resources"][0]:
        license_url = json_data["video_resources"][0]["license_server"]["url"]

    return master_playlist_url, license_url, headers
