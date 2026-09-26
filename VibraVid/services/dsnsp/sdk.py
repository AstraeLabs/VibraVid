# 25.09.26

import base64
import hashlib
import logging
import os
import time
import uuid
from typing import Any

from pyplayready.device import Device as PRDevice
from pywidevine.device import Device as WVDevice

from VibraVid.setup import get_prd_path, get_wvd_path
from VibraVid.utils import disk_cache
from VibraVid.utils.http_client import create_client

logger = logging.getLogger(__name__)

DEVICE_IDENTITY_SERVICE = "dsnsp"
DEVICE_IDENTITY_NAME = "device_identity"

API_BASE = "https://disney.api.edge.bamgrid.com"
# Device level calls (registerDevice, refreshToken) live on their own endpoint and
# authenticate with the api key, not a session token.
DEVICE_GRAPHQL = f"{API_BASE}/graph/v1/device/graphql"
CONFIG_URL = "https://client-sdk-configs.bamgrid.com/bam-sdk/v7.0/disney-svod-3d9324fc/android/v24.0.0/google/tv/prod.json"

SDK_VERSION = "24.0.2"
# Distinct from SDK_VERSION: it is the config document revision, and it is what the
# real client puts in the user agent (v7.0/v24.0.0, not v7.0/24.0.2).
SDK_CONFIG_VERSION = "v24.0.0"
APP_VERSION = "26.14.0+rc3-2026.08.10.0"
CLIENT_ID = "disney-svod-3d9324fc"
API_KEY = "ZGlzbmV5JmFuZHJvaWQmMS4wLjA.bkeb0m230uUhv8qrAXuNu39tbE_mD5EEhM_NAcohjyA"
EXPLORE_VERSION = "v1.18"
YP_SERVICE_ID = "624b805dafc5c73635b1a216"

# The playback service only hands out the UHD/HDR ladder to a session registered as an
# Android TV device, so the whole SDK impersonates one instead of the web client.
PLATFORM = "android/google/tv"
PLATFORM_ID = "android-tv"
DEVICE_FAMILY = "android"
DEVICE_PROFILE = "tv"
APPLICATION_RUNTIME = "android"
DEVICE_BRAND = "NVIDIA"
DEVICE_MANUFACTURER = "NVIDIA"
DEVICE_MODEL_NAME = "SHIELD Android TV"
DEVICE_MODEL = "NVIDIA SHIELD Android TV (RQ1A.210105.003.7825230_4387.0822; Linux; 11; API 30)"
APP_PACKAGE = "com.disney.disneyplus"

USER_AGENT = f"EDGESDK/v{SDK_VERSION} ({CLIENT_ID} {APP_VERSION}; v7.0/{SDK_CONFIG_VERSION}; android; tv) {DEVICE_MODEL}"

REGISTER_DEVICE = """mutation ($registerDevice: RegisterDeviceInput!) {
  registerDevice(registerDevice: $registerDevice) { __typename }
}"""

ENTITLEMENTS = """query EntitledGraphMeQuery {
  me {
    account { profiles { name } activeProfile { id } }
    activeSession { sessionId }
  }
}"""

REFRESH_TOKEN = """mutation refreshToken($input: RefreshTokenInput!) {
  refreshToken(refreshToken: $input) { activeSession { sessionId } }
}"""

UPDATE_DEVICE_OS = """mutation updateDeviceOperatingSystem($updateDeviceOperatingSystem: UpdateDeviceOperatingSystemInput!) {
  updateDeviceOperatingSystem(updateDeviceOperatingSystem: $updateDeviceOperatingSystem) { accepted }
}"""

ACTIVE_SESSION = """query activeSession {
  activeSession { device { id platform } entitlements inSupportedLocation isSubscriber
    location { type countryCode } sessionId account { id } profile { id } }
}"""


class Sdk:
    def __init__(self, cookies: dict[str, str] | None = None, on_token_refreshed=None):
        self.device_id = str(uuid.uuid1())
        # The device has to keep the same identity across runs, otherwise the
        # backend treats every start as a new unregistered device.
        stored_identity = disk_cache.load(DEVICE_IDENTITY_SERVICE, DEVICE_IDENTITY_NAME)
        if isinstance(stored_identity, dict) and stored_identity.get("device_id"):
            self.device_id = stored_identity["device_id"]
        self.access_token = None
        self.refresh_token = None
        self.prod_config: dict = {}
        self.device_token = ""
        self.on_token_refreshed = on_token_refreshed

        self.base_headers = {
            "User-Agent": USER_AGENT,
            "Accept-Encoding": "gzip",
            "Accept": "application/json",
            "Content-Type": "application/json",
            "X-Application-Version": APP_VERSION,
            "X-BAMSDK-Client-ID": CLIENT_ID,
            "X-BAMSDK-Platform": PLATFORM,
            "X-BAMSDK-Platform-Id": PLATFORM_ID,
            "X-BAMSDK-Version": SDK_VERSION,
            "X-DSS-Edge-Accept": "vnd.dss.edge+json; version=2",
            "X-Request-Yp-Id": YP_SERVICE_ID,
        }

        self.cookies = cookies or {}
        self.session = create_client(headers=self.base_headers, cookies=self.cookies, browser=None)

    def use_session_token(self, token: str, refresh_token: str | None = None):
        self.access_token = token
        self.refresh_token = refresh_token
        self.session.headers.update({"Authorization": f"Bearer {self.access_token}"})

    def fetch_prod_config(self):
        try:
            resp = self.session.get(CONFIG_URL)
            resp.raise_for_status()
            self.prod_config = resp.json()
            logger.info("Loaded prod config")
        except Exception as e:
            logger.error(f"Could not fetch prod config: {e}")
            self.prod_config = {}

    def graphql(self, operation_name: str = "", query: str = "", variables: dict = None, _allow_refresh: bool = True, endpoint: str = None) -> dict:
        endpoint = endpoint or f"{API_BASE}/v1/public/graphql"
        headers = self.session.headers.copy()
        headers.update({
            "X-BAMSDK-Transaction-ID": str(uuid.uuid4()),
            "X-Request-ID": str(uuid.uuid4()),
            "X-BAMSDK-Client-ID": CLIENT_ID,
            "X-BAMSDK-Platform": PLATFORM,
            "X-BAMSDK-Platform-Id": PLATFORM_ID,
            "X-BAMSDK-Version": SDK_VERSION,
            "X-Application-Version": APP_VERSION,
            "X-Api-Key": API_KEY,
        })

        # An anonymous operation must not carry an operationName, the service
        # rejects the pair with "Unknown operation name".
        payload = {
            "variables": variables or {},
            "query": query,
        }
        if operation_name:
            payload["operationName"] = operation_name

        resp = self.session.post(endpoint, headers=headers, json=payload)

        # A 401 here means the cached access token expired
        if resp.status_code == 401 and _allow_refresh and self.refresh_token:
            logger.info("Token expired. Refreshing...")
            if self.refresh():
                logger.info("Token refresh successful")
                return self.graphql(operation_name, query, variables, _allow_refresh=False)
            logger.warning("Token refresh failed; continuing with expired token")

        if resp.status_code >= 400:
            try:
                body = resp.json()
            except Exception:
                body = resp.text
            logger.error(f"GraphQL request failed: {resp.status_code} {endpoint}")
            logger.error(f"Response: {body}")
            raise RuntimeError(f"GraphQL request failed ({resp.status_code}): {body}")
        data = resp.json()

        if data.get("errors"):
            error_codes = [e.get("extensions", {}).get("code", "") for e in data["errors"]]
            if (
                _allow_refresh and self.refresh_token
                and any(("token" in c.lower() or "unauthenticated" in c.lower()) and "invalid.grant" not in c.lower() for c in error_codes)
            ):
                logger.info("Token expired. Refreshing...")
                if self.refresh():
                    logger.info("Token refresh successful")
                    return self.graphql(operation_name, query, variables, _allow_refresh=False)
                logger.warning("Token refresh failed; continuing with expired token")
            for code in error_codes:
                if "token.service.invalid.grant" in code:
                    raise ConnectionError(f"Refresh Token Expired: {code}")
                if "token.service.unauthorized.client" in code:
                    raise ConnectionError(f"Unauthorized Client/IP: {code}")
                if "idp.error.identity.bad-credentials" in code:
                    raise ConnectionError(f"Bad Credentials: {code}")
                if "account.profile.pin.invalid" in code:
                    raise ConnectionError(f"Invalid PIN: {code}")
            raise RuntimeError(f"GraphQL error: {data['errors']}")

        return data

    def _device_os_ids(self) -> list[dict]:
        """Identifiers the backend stores against the device.

        The real Android TV client generates these once and keeps them for the
        lifetime of the install, so `registerDevice`, `updateDeviceOperatingSystem`
        and every playback all present the same device. The backend ties the device
        DRM identity to the security level it will grant, and a device it has not
        seen before is served the safe 1080p SDR ladder, so a value that changes
        between calls costs the UHD ladder.
        """
        store = self._device_identity()
        ids = [
            {"identifier": store["vendor_id"], "type": "android.vendor.id"},
            {"identifier": store["drm_id"], "type": "android.drm.id"},
            {"identifier": store["advertising_id"], "type": "android.advertising.id"},
        ]
        return ids

    def _device_identity(self) -> dict[str, str]:
        """Read the persisted device identifiers, creating them on first use."""
        stored = disk_cache.load(DEVICE_IDENTITY_SERVICE, DEVICE_IDENTITY_NAME)
        if isinstance(stored, dict) and all(
            stored.get(k) for k in ("device_id", "vendor_id", "drm_id", "advertising_id")
        ):
            self.device_id = stored["device_id"]
            return stored

        drm_id = self._cdm_drm_id() or base64.b64encode(os.urandom(32)).decode()
        store = {
            "device_id": self.device_id,
            "vendor_id": hashlib.sha256(self.device_id.encode()).hexdigest()[:16],
            "drm_id": drm_id,
            "advertising_id": str(uuid.uuid4()),
        }
        disk_cache.save(DEVICE_IDENTITY_SERVICE, DEVICE_IDENTITY_NAME, store)
        return store

    @staticmethod
    def _cdm_drm_id() -> str | None:
        """Base64 device unique id derived from the configured PlayReady/Widevine CDM.

        Neither CDM exposes the id as an attribute, so it is derived from the
        certificate material instead. The value must stay stable across runs:
        the backend treats a changing `android.drm.id` as a different device and
        answers the downgrade prompt instead of the media payload.
        """
        try:
            prd_path = get_prd_path()
            if prd_path and os.path.isfile(prd_path):
                device = PRDevice.load(prd_path)
                cert = device.group_certificate
                material = b"".join(
                    bytes(cert.get(i).Header.subcons[j]) if hasattr(cert.get(i).Header.subcons[j], "__len__") else b""
                    for i in range(cert.count())
                    for j in range(len(cert.get(i).Header.subcons))
                )
                if not material:
                    material = repr([cert.get(i).get_name() for i in range(cert.count())]).encode()
                return base64.b64encode(hashlib.sha256(material).digest()).decode()
        except Exception as e:
            logger.warning(f"Could not derive PlayReady drm id: {e}")
        try:
            wvd_path = get_wvd_path()
            if wvd_path and os.path.isfile(wvd_path):
                with open(wvd_path, "rb") as fh:
                    return base64.b64encode(hashlib.sha256(fh.read()).digest()).decode()
        except Exception as e:
            logger.warning(f"Could not derive Widevine drm id: {e}")
        return None

    def register_device(self) -> str:
        attributes = {
            "osDeviceIds": self._device_os_ids(),
            "manufacturer": DEVICE_MANUFACTURER,
            "model": DEVICE_MODEL_NAME,
            "brand": DEVICE_BRAND,
            "operatingSystem": "Android",
            "operatingSystemVersion": "11",
        }
        # There is no session yet, so this call authenticates with the api key
        # against the device endpoint rather than a bearer session token.
        original_auth = self.session.headers.get("Authorization")
        self.session.headers.update({"Authorization": f"Bearer {API_KEY}"})
        try:
            data = self.graphql(
                operation_name="",
                query=REGISTER_DEVICE,
                variables={
                    "registerDevice": {
                        "applicationRuntime": APPLICATION_RUNTIME,
                        "attributes": attributes,
                        "deviceFamily": DEVICE_FAMILY,
                        "deviceLanguage": "en",
                        "deviceProfile": DEVICE_PROFILE,
                        "devicePlatformId": PLATFORM_ID,
                    }
                },
                _allow_refresh=False,
                endpoint=DEVICE_GRAPHQL,
            )
        finally:
            if original_auth:
                self.session.headers.update({"Authorization": original_auth})
            else:
                self.session.headers.pop("Authorization", None)
        return self._store_device_token(data)

    def _store_device_token(self, data: dict) -> str:
        token = data["extensions"]["sdk"]["token"]["accessToken"]
        self.device_token = token
        return token

    def update_device_operating_system(self) -> bool:
        """Re-declare the device DRM identity for the current session.

        The real Android TV client runs this before every playback. It is what binds
        the session to the CDM security level the backend will grant, so skipping it
        leaves the session pinned to the 1080p SDR ladder.
        """
        try:
            data = self.graphql(
                operation_name="updateDeviceOperatingSystem",
                query=UPDATE_DEVICE_OS,
                variables={
                    "updateDeviceOperatingSystem": {
                        "brand": DEVICE_BRAND,
                        "operatingSystem": "Android",
                        "operatingSystemVersion": "11",
                        "osDeviceIds": self._device_os_ids(),
                    }
                },
            )
            return bool(data.get("data", {}).get("updateDeviceOperatingSystem", {}).get("accepted"))
        except Exception as e:
            logger.warning(f"Could not update device operating system: {e}")
            return False

    def refresh(self) -> bool:
        """Refresh the session using the stored refresh token, if available."""
        if not self.refresh_token:
            return False
        try:
            data = self.graphql(
                operation_name="refreshToken",
                query=REFRESH_TOKEN,
                variables={"input": {"refreshToken": self.refresh_token}},
                _allow_refresh=False,
            )
            new_tokens = data.get("extensions", {}).get("sdk", {}).get("token", {})
            new_access = new_tokens.get("accessToken", "")
            if not new_access:
                logger.error("Token refresh response had no accessToken")
                return False
            self.access_token = new_access
            self.refresh_token = new_tokens.get("refreshToken", self.refresh_token)
            self.session.headers.update({"Authorization": f"Bearer {self.access_token}"})
            if self.on_token_refreshed:
                try:
                    self.on_token_refreshed(self.access_token, self.refresh_token)
                except Exception as e:
                    logger.warning(f"on_token_refreshed callback failed: {e}")
            return True
        except Exception as e:
            logger.error(f"Token refresh failed: {e}")
            return False

    def endpoint(self, service: str, endpoint_name: str, **kwargs) -> str:
        try:
            # the config document wraps everything in a top level "data" key
            cfg = self.prod_config.get("data", self.prod_config)
            href = cfg["services"][service]["client"]["endpoints"][endpoint_name]["href"]
            args = {"version": cfg.get("bamsdk", {}).get("explore_version", EXPLORE_VERSION)}
            args.update(kwargs)
            return href.format(**args)
        except Exception:
            return f"{API_BASE}/{service}/{endpoint_name}"

    def request(self, method: str, endpoint: str, params: dict = None, headers: dict = None, payload: dict = None, exclude_headers: list | None = None) -> Any:
        _headers = self.session.headers.copy()
        if headers:
            _headers.update(headers)
        # Session-level headers the real playback call does not send, while the
        # device registration call still needs them.
        for name in exclude_headers or ():
            _headers.pop(name, None)
        _headers.update({
            "X-BAMSDK-Transaction-ID": str(uuid.uuid4()),
            "X-Request-ID": str(uuid.uuid4()),
        })

        try:
            method = method.upper()
            if method == "GET":
                res = self.session.get(endpoint, params=params, headers=_headers)
            elif method == "POST":
                res = self.session.post(endpoint, headers=_headers, json=payload, params=params)
            else:
                raise RuntimeError(f"Unsupported method: {method}")
            res.raise_for_status()
            data = res.json()
            if data.get("errors"):
                raise RuntimeError(f"API error: {data['errors']}")
            return data
        except Exception as e:
            logger.error(f"API request failed: {e}")
            raise
