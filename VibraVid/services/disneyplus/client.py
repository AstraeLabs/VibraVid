# 21.09.26

import logging
import uuid
from typing import Any

from VibraVid.services._base.login_status import ACCOUNT, ANONYMOUS, print_login
from VibraVid.utils import config_manager
from VibraVid.utils.http_client import create_client

logger = logging.getLogger(__name__)

_disney_client = None
_cookie_st = config_manager.login.get("disneyplus", "token")
_email = ""
_password = ""

API_BASE = "https://disney.api.edge.bamgrid.com"
CONFIG_URL = "https://client-sdk-configs.bamgrid.com/bam-sdk/v7.0/disney-svod-3d9324fc/android/v13.0.0/google/tv/prod.json"
SEARCH_URL = "https://disney.api.edge.bamgrid.com/explore/v1.20/search"

SDK_VERSION = "13.3.0"
APP_VERSION = "4.18.1+rc6-2025.11.05.0"
CLIENT_ID = "disney-svod-3d9324fc"
API_KEY = "ZGlzbmV5JmFuZHJvaWQmMS4wLjA.bkeb0m230uUhv8qrAXuNu39tbE_mD5EEhM_NAcohjyA"
YP_SERVICE_ID = "624b805dafc5c73635b1a216"
EXPLORE_VERSION = "v1.11"

USER_AGENT = f"BAMSDK/{SDK_VERSION} ({CLIENT_ID} {APP_VERSION}; v7.0/{SDK_VERSION}; android; tv)"

CHECK_EMAIL = """query Check($email: String!) {
  check(email: $email) { operations nextOperation }
}"""
REGISTER_DEVICE = """mutation ($registerDevice: RegisterDeviceInput!) {
  registerDevice(registerDevice: $registerDevice) { __typename }
}"""
LOGIN = """mutation loginTv($input: LoginInput!) {
  login(login: $input) {
    __typename actionGrant
    account { __typename id activeProfile { __typename id } profiles { __typename id name } }
    activeSession { __typename sessionId }
  }
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


class DisneyPlusClient:
    def __init__(self, cookies: dict[str, str] | None = None):
        self.device_id = str(uuid.uuid1())
        self.access_token = None
        self.refresh_token = None
        self.region = "US"
        self.prod_config = {}
        self.account_tokens = {}
        self.active_session = {}
        self.playback_data = {}

        self.base_headers = {
            "User-Agent": USER_AGENT,
            "Accept-Encoding": "gzip",
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

        self.cookies = cookies or {}
        self.session = create_client(headers=self.base_headers, cookies=self.cookies, browser=None)

        self._authenticate()

    def _authenticate(self):
        if _cookie_st:
            self._use_session_token()
        else:
            print_login(ANONYMOUS, resolver=lambda: "Disney+")
            return
        print_login(ACCOUNT, resolver=self._account_name)
        self._fetch_prod_config()

    def _account_name(self) -> str:
        try:
            info = self._get_account_info()
            profiles = info.get("account", {}).get("profiles", [])
            active = info.get("account", {}).get("activeProfile", {})
            return active.get("name") or profiles[0].get("name", "Disney+") if profiles else "Disney+"
        except Exception:
            return "Disney+"

    def _use_session_token(self):
        self.access_token = _cookie_st
        self.session.headers.update({"Authorization": f"Bearer {self.access_token}"})
        logger.info("Using session token")

    def _fetch_prod_config(self):
        try:
            resp = self.session.get(CONFIG_URL)
            resp.raise_for_status()
            self.prod_config = resp.json()
            logger.info("Loaded prod config from BAMSDK")
        except Exception as e:
            logger.error(f"Could not fetch prod config: {e}")
            self.prod_config = {}

    def _login_with_credentials(self):
        logger.info("Logging into Disney+ with credentials...")
        try:
            self.access_token = self._perform_full_login()
            if not self.access_token:
                raise RuntimeError("Disney+ login returned no access token")
            self.session.headers.update({"Authorization": f"Bearer {self.access_token}"})
        except Exception as e:
            logger.error(f"Disney+ login failed: {e}")
            raise
        _save_session_token(self.access_token)

    def _perform_full_login(self) -> str:
        device_token = self._register_device()
        email_status = self._check_email(_email, device_token)
        if email_status != "login":
            raise RuntimeError(f"Email status: {email_status}")
        login_tokens = self._login_with_password(_email, _password, device_token)
        return login_tokens.get("accessToken", login_tokens.get("token", {}).get("accessToken", ""))

    def _graphql_request(self, operation_name: str, query: str, variables: dict = None) -> dict:
        endpoint = f"{API_BASE}/v1/public/graphql"
        headers = self.session.headers.copy()
        headers.update({
            "X-BAMSDK-Transaction-ID": str(uuid.uuid4()),
            "X-Request-ID": str(uuid.uuid4()),
            "X-BAMSDK-Client-ID": CLIENT_ID,
            "X-BAMSDK-Platform": "android/google/tv",
            "X-BAMSDK-Version": SDK_VERSION,
            "X-Application-Version": APP_VERSION,
            "X-Api-Key": API_KEY,
        })

        payload = {
            "operationName": operation_name,
            "variables": variables or {},
            "query": query,
        }

        resp = self.session.post(endpoint, headers=headers, json=payload)
        if resp.status_code >= 400:
            try:
                body = resp.json()
            except Exception:
                body = resp.text
            logger.error(f"GraphQL request failed: {resp.status_code} {endpoint}")
            logger.error(f"Response: {body}")
            raise RuntimeError(f"Disney+ GraphQL request failed ({resp.status_code}): {body}")
        data = resp.json()

        if data.get("errors"):
            error_codes = [e.get("extensions", {}).get("code", "") for e in data["errors"]]
            for code in error_codes:
                if "token.service.invalid.grant" in code:
                    raise ConnectionError(f"Refresh Token Expired: {code}")
                if "token.service.unauthorized.client" in code:
                    raise ConnectionError(f"Unauthorized Client/IP: {code}")
                if "idp.error.identity.bad-credentials" in code:
                    raise ConnectionError(f"Bad Credentials: {code}")
                if "account.profile.pin.invalid" in code:
                    raise ConnectionError(f"Invalid PIN: {code}")
            raise RuntimeError(f"Disney+ GraphQL error: {data['errors']}")

        return data

    def _register_device(self) -> str:
        data = self._graphql_request(
            operation_name="registerDevice",
            query=REGISTER_DEVICE,
            variables={
"registerDevice": {
                    "applicationRuntime": "android",
                    "attributes": {"operatingSystem": "Android", "operatingSystemVersion": "16"},
                    "deviceFamily": "android",
                    "deviceLanguage": "en-US",
                    "deviceProfile": "tv",
                    "devicePlatformId": "android-tv",
                }
            },
        )
        return data["extensions"]["sdk"]["token"]["accessToken"]

    def _check_email(self, email: str, token: str) -> str:
        original_auth = self.session.headers.get("Authorization")
        self.session.headers.update({"Authorization": f"Bearer {token}"})
        try:
            data = self._graphql_request(
                operation_name="Check",
                query=CHECK_EMAIL,
                variables={"email": email},
            )
        finally:
            if original_auth:
                self.session.headers.update({"Authorization": original_auth})
        operations = data.get("data", {}).get("check", {}).get("operations", [])
        return operations[0] if operations else "unknown"

    def _login_with_password(self, email: str, password: str, token: str) -> dict:
        self.session.headers.update({"Authorization": f"Bearer {token}"})
        data = self._graphql_request(
            operation_name="loginTv",
            query=LOGIN,
            variables={"input": {"email": email, "password": password}},
        )
        token_data = data.get("extensions", {}).get("sdk", {}).get("token", {})
        if not token_data:
            token_data = data.get("data", {}).get("login", {}).get("accessToken", {})
        return token_data if isinstance(token_data, dict) else {"accessToken": token_data}
    def _get_account_info(self) -> dict:
        data = self._graphql_request(
            operation_name="EntitledGraphMeQuery",
            query=ENTITLEMENTS,
            variables={},
        )
        return data.get("data", {}).get("me", {})

    def _get_drm_license_url_from_config(self, drm_type: str) -> str:
        try:
            endpoint_name = None
            endpoints = self.prod_config.get("services", {}).get("drm", {}).get("client", {}).get("endpoints", {})
            if drm_type.upper() == "WIDEVINE" and "widevineLicense" in endpoints:
                endpoint_name = "widevineLicense"
            elif drm_type.upper() == "PLAYREADY" and "playReadyLicense" in endpoints:
                endpoint_name = "playReadyLicense"
            if endpoint_name:
                url = self._endpoint("drm", endpoint_name)
                logger.info(f"BAMSDK DRM endpoint: {url}")
                return url
        except Exception as e:
            logger.error(f"Could not get DRM license URL from config: {e}")
        return ""

    def _get_widevine_fallback_license_url(self) -> str:
        """Try alternative Widevine license URLs used by Disney+ community projects."""
        fallbacks = [
            "https://global.edge.bamgrid.com/widevine/v1/obtain-license",
        ]
        for url in fallbacks:
            logger.info(f"Trying fallback Widevine URL: {url}")
            return url
        return ""

    def _get_license_headers_from_config(self, drm_type: str) -> dict:
        try:
            endpoints = self.prod_config.get("services", {}).get("drm", {}).get("client", {}).get("endpoints", {})
            endpoint_name = "widevineLicense" if drm_type.upper() == "WIDEVINE" else "playReadyLicense"
            endpoint = endpoints.get(endpoint_name, {})
            headers = endpoint.get("headers", {})
            if self.access_token:
                for k, v in headers.items():
                    if isinstance(v, str) and "{accessToken}" in v:
                        headers[k] = v.replace("{accessToken}", self.access_token)
            return headers
        except Exception as e:
            logger.error(f"Could not get DRM license headers from config: {e}")
        return {}

    def get_playback_info(self, media_id: str) -> dict[str, Any]:
        endpoint = self._endpoint("media", "mediaPayload", scenario="ctr-high")
        headers = {
            "Accept": "application/vnd.media-service+json",
            "X-Application-Version": APP_VERSION,
            "X-BAMSDK-Client-ID": CLIENT_ID,
            "X-BAMSDK-Platform": "android/google/tv",
            "X-BAMSDK-Version": SDK_VERSION,
            "X-DSS-Edge-Accept": "vnd.dss.edge+json; version=2",
            "X-DSS-Feature-Filtering": "true",
            "Origin": "https://www.disneyplus.com",
        }
        payload = {
            "playbackId": media_id,
            "playback": {
                "attributes": {
                    "codecs": {
                        "supportsMultiCodecMaster": False,
                        "video": ["h.264", "h.265"],
                    },
                    "protocol": "HTTPS",
                    "frameRates": [60],
                    "assetInsertionStrategy": "SGAI",
                    "playbackInitiationContext": "ONLINE",
                    "resolution": {"max": ["1280x720"]},
                    "audioTypes": ["ATMOS", "DTS_X"],
                }
            },
        }
        data = self._request("POST", endpoint, headers=headers, payload=payload)
        stream = data.get("stream", {})
        sources = stream.get("sources", [{}])
        source = sources[0] if sources else {}
        manifest_url = source.get("complete", {}).get("url", "")
        logger.info(f"Manifest URL from API: {manifest_url}")
        widevine_drm = source.get("drm", {}).get("widevine", {})
        playready_drm = source.get("drm", {}).get("playready", {})
        logger.debug(f"Source DRM widevine: {widevine_drm}")
        logger.debug(f"Source DRM playready: {playready_drm}")
        # Search for any license URL in source dict
        import json as _json
        _source_str = _json.dumps(source)
        import re as _re
        _license_urls = _re.findall(r'https?://[^\s,"\']*(?:license|widevine|drm)[^\s,"\']*', _source_str, _re.IGNORECASE)
        if _license_urls:
            for _u in set(_license_urls):
                logger.info(f"License URL found in source: {_u}")
        widevine_license = widevine_drm.get("licenseUrl", "") if isinstance(widevine_drm, dict) else ""
        playready_license = playready_drm.get("licenseUrl", "") if isinstance(playready_drm, dict) else ""
        license_url = widevine_license or playready_license
        drm_type = "widevine" if widevine_license else ("playready" if playready_license else "")
        logger.info(f"DRM from API response: license_url={'yes' if license_url else 'none'}, drm_type={drm_type or 'none'}")
        if not license_url:
            widevine_license = self._get_drm_license_url_from_config("widevine")
            playready_license = self._get_drm_license_url_from_config("playready")
            license_url = widevine_license or playready_license
            drm_type = "widevine" if widevine_license else ("playready" if playready_license else "")
            if license_url:
                logger.info(f"Using DRM license URL from prod_config: {license_url} (type={drm_type})")
                if drm_type.upper() == "WIDEVINE" and license_url and "disney.playback.edge" in license_url:
                    fallback_url = self._get_widevine_fallback_license_url()
                    if fallback_url:
                        logger.info(f"Widevine fallback URL: {fallback_url}")
                        license_url = fallback_url
                        drm_type = "widevine"
            else:
                logger.warning("No DRM license URL found in API response or prod_config, trying Widevine fallbacks")
                fallback_url = self._get_widevine_fallback_license_url()
                if fallback_url:
                    logger.info(f"Using Widevine fallback URL: {fallback_url}")
                    license_url = fallback_url
                    drm_type = "widevine"
                    cfg_headers = self._get_license_headers_from_config("widevine")
                    if cfg_headers:
                        for k, v in cfg_headers.items():
                            if k not in license_headers and not (k == "Authorization" and "global.edge.bamgrid.com" in license_url):
                                license_headers[k] = v
        license_headers = {}
        if self.access_token and license_url:
            license_headers["Authorization"] = f"Bearer {self.access_token}"
        if license_url and drm_type:
            cfg_headers = self._get_license_headers_from_config(drm_type)
            if cfg_headers:
                for k, v in cfg_headers.items():
                    if k not in license_headers:
                        license_headers[k] = v
        logger.info(f"Final license_url: {license_url}, drm_type: {drm_type}, headers: {list(license_headers.keys())}")
        return {"manifest": manifest_url, "license": license_url, "type": "dash", "license_headers": license_headers, "drm_type": drm_type}

    def search(self, query: str) -> list[dict]:
        headers = {
            "Accept": "application/json",
            "X-BAMSDK-Client-ID": CLIENT_ID,
            "X-BAMSDK-Version": SDK_VERSION,
            "X-BAMSDK-Platform": "android/google/tv",
        }
        if self.access_token:
            headers["Authorization"] = f"Bearer {self.access_token}"
        params = {"query": query}
        try:
            resp = self.session.get(SEARCH_URL, params=params, headers=headers)
            if resp.status_code == 401:
                try:
                    error = resp.json().get("errors", [{}])[0]
                    code = error.get("code", "unauthorized")
                    description = error.get("description", "authentication failed")
                except ValueError:
                    code = "unauthorized"
                    description = resp.reason
                raise ConnectionError(
                    f"Disney+ token non valido o scaduto ({code}: {description})."
                )
            resp.raise_for_status()
            data = resp.json()
            results = self._parse_search_results(data)
            logger.info(f"Disney+ search: {len(results)} results for '{query}'")
            return results
        except Exception as e:
            logger.error(f"Disney+ search failed: {e}")
            if isinstance(e, ConnectionError):
                raise
            return []

    def _parse_search_results(self, data: dict) -> list[dict]:
        results = []
        page = data.get("data", {}).get("page", {})
        for container in page.get("containers", []):
            for item in container.get("items", []):
                results.append(item)
        if results:
            return results
        items = data.get("data", {}).get("items", [])
        if items:
            return list(items)
        items = data.get("data", {}).get("search", {}).get("items", [])
        if items:
            return list(items)
        included = data.get("data", {}).get("included", [])
        if included:
            return [e for e in included if e.get("type") in ("movie", "tv", "show", "season", "episode")]
        return []

    def refresh_session(self) -> None:
        if self.refresh_token:
            try:
                data = self._graphql_request(
                    operation_name="refreshToken",
                    query=REFRESH_TOKEN,
                    variables={"input": {"refreshToken": self.refresh_token}},
                )
                refreshed = data.get("data", {}).get("refreshToken", {})
                self.access_token = refreshed.get("accessToken", "")
                self.refresh_token = refreshed.get("refreshToken", "")
                if self.access_token:
                    self.session.headers.update({"Authorization": f"Bearer {self.access_token}"})
            except Exception as e:
                logger.error(f"Token refresh failed: {e}")
    def _endpoint(self, service: str, endpoint_name: str, **kwargs) -> str:
        try:
            href = self.prod_config["services"][service]["client"]["endpoints"][endpoint_name]["href"]
            args = {"version": self.prod_config.get("bamsdk", {}).get("explore_version", EXPLORE_VERSION)}
            args.update(kwargs)
            return href.format(**args)
        except Exception:
            return f"{API_BASE}/{service}/{endpoint_name}"

    def _request(self, method: str, endpoint: str, params: dict = None, headers: dict = None, payload: dict = None) -> Any:
        _headers = self.session.headers.copy()
        if headers:
            _headers.update(headers)
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
                raise RuntimeError(f"Disney+ API error: {data['errors']}")
            return data
        except Exception as e:
            logger.error(f"Disney+ API request failed: {e}")
            raise


def get_client():
    global _disney_client
    if _disney_client is None:
        cookies = {"st": _cookie_st} if _cookie_st else None
        _disney_client = DisneyPlusClient(cookies)
    return _disney_client


def _save_session_token(token: str) -> None:
    """Persist the credentials-based session token for the next startup."""
    login_data = config_manager._login_data.setdefault("disneyplus", {})
    if login_data.get("token") == token:
        return
    login_data["token"] = token
    config_manager.save_login()


def refresh_login_token() -> bool:
    """Use the stored Disney+ bearer token and persist it after authentication."""
    if not _cookie_st:
        return False
    client = get_client()
    if not client.access_token:
        return False
    _save_session_token(client.access_token)
    return True
