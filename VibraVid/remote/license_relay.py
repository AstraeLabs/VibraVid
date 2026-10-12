# 12.10.26

import base64
import json
import logging

from .models import RemoteError, ResolvedMedia

logger = logging.getLogger(__name__)


def _post(media: ResolvedMedia, headers: dict, data: bytes):
    from VibraVid.utils.http_client import create_client

    with create_client(headers=headers) as client:
        return client.post(media.license_url, data=data)


def relay_widevine(media: ResolvedMedia, challenge: bytes) -> bytes:
    """POST a Widevine challenge to the service's license server exactly as the built-in flow does and return the raw license."""
    headers = dict(media.license_headers or {})
    if media.license_data:
        body = {**media.license_data, "licenseChallenge": base64.b64encode(challenge).decode("utf-8")}
        headers["Content-Type"] = "text/plain"
        data = json.dumps(body, separators=(",", ":")).encode("utf-8")
    else:
        headers.setdefault("Content-Type", "application/octet-stream")
        data = challenge

    response = _post(media, headers, data)
    if response.status_code != 200:
        raise RemoteError(f"license server answered HTTP {response.status_code}", 502)

    payload = response.content
    if "application/json" in response.headers.get("content-type", "").lower():
        try:
            body = response.json()
            if "widevineLicense" in body:
                payload = base64.b64decode(body["widevineLicense"]["license"])
            elif "license" in body:
                payload = base64.b64decode(body["license"])
            else:
                raise RemoteError(f"no license field in JSON answer: {list(body.keys())}", 502)
        except RemoteError:
            raise
        except Exception as exc:
            logger.warning(f"JSON license answer not parseable, relaying raw bytes: {exc}")
    if not payload:
        raise RemoteError("license server answered with an empty license", 502)
    return payload


def relay_playready(media: ResolvedMedia, challenge: bytes) -> str:
    """Same for PlayReady; returns the decoded license XML."""
    headers = dict(media.license_headers or {})
    if media.license_data:
        body = {**media.license_data, "licenseChallenge": base64.b64encode(challenge).decode("utf-8")}
        for name in ("Content-Type", "content-type"):
            headers.pop(name, None)
        headers["Content-Type"] = "text/plain"
        data = json.dumps(body, separators=(",", ":")).encode("utf-8")
    else:
        headers.setdefault("Content-Type", "text/xml; charset=utf-8")
        data = challenge

    response = _post(media, headers, data)
    if response.status_code != 200:
        raise RemoteError(f"license server answered HTTP {response.status_code}", 502)

    payload = response.text
    if media.license_data:
        try:
            payload = base64.b64decode(response.json()["playReadyLicense"]["license"]).decode("utf-8")
        except Exception as exc:
            raise RemoteError(f"could not parse PlayReady license answer: {exc}", 502) from exc
    if not payload:
        raise RemoteError("license server answered with an empty license", 502)
    return payload


def relay_license(media: ResolvedMedia, drm: str, challenge: bytes) -> bytes | str:
    """Relay a license request to the service's license server and return the raw license bytes (Widevine) or XML (PlayReady)."""
    if not media.license_url:
        raise RemoteError("this media has no license_url to relay to", 400)
    if drm == "PR":
        return relay_playready(media, challenge)
    return relay_widevine(media, challenge)
