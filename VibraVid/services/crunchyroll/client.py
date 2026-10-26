# 29.12.25

import base64
import json
import logging
import math
import sys
import threading
import time
import uuid
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import parse_qs, urlparse

from rich.console import Console

from VibraVid.core.utils.language import resolve_locale
from VibraVid.services._base.login_status import Login
from VibraVid.utils import config_manager, disk_cache
from VibraVid.utils.http_client import create_client, get_my_location, get_userAgent

console = Console()
logger = logging.getLogger(__name__)
PUBLIC_TOKEN = "bm9haWhkZXZtXzZpeWcwYThsMHE6"
BASE_URL = "https://www.crunchyroll.com"
API_BETA_BASE_URL = "https://beta-api.crunchyroll.com"
PLAY_SERVICE_URL = "https://cr-play-service.prd.crunchyrollsvc.com"
SKIP_EVENTS_URL = "https://static.crunchyroll.com/skip-events/production/{episode_id}.json"
INTRO_DATALAB_URL = "https://static.crunchyroll.com/datalab-intro-v2/{episode_id}.json"
WEB_LICENSE_URL = "https://www.crunchyroll.com/license/v1/license/widevine"
TV_CLIENT_ID = "er9ngg2ko5ohirla51i2"
TV_CLIENT_SECRET = "Vl1T0Pn8QZJ6wz46xXDlHzDFP5tZX1oq"
TV_ACTIVATE_URL = "https://www.crunchyroll.com/activate"
TV_POLL_SECONDS = 3.0
TV_USER_AGENT = "Crunchyroll/ANDROIDTV/3.50.0_22282 (Android 16; en-US; sdk_gphone64_x86_64)"
TV_DEVICE_TYPE = "sdk_gphone64_x86_64"
TV_DEVICE_NAME = "Google sdk_gphone64_x86_64"
TV_LICENSE_URL = "https://cr-license-proxy.prd.crunchyrollsvc.com/v1/license/widevine"
TV_PLAY_PATH = "/v1/{media_id}/tv/android_tv/play"
TV_DOWNLOAD_PATH = "/v1/{media_id}/android/phone/download"
TV_REFRESH_TTL = 30 * 86400
_tv_code_failed = False

_COUNTRIES_RESOLVED_BY_LANGUAGE = frozenset({"IT", "US", "GB", "AU", "DE", "FR", "BR", "JP", "RU", "TR", "TH", "ID"})
_COUNTRY_LOCALE_OVERRIDES = {
    "IN": "en-IN",
    "CA": "en-CA",
    "IE": "en-IE",
    "NZ": "en-NZ",
    "ES": "es-ES",
    "MX": "es-419",
    "AR": "es-419",
    "PT": "pt-PT",
    "SA": "ar-SA",
    "AE": "ar-SA",
    "PH": "en-PH",
    "SG": "en-SG",
}
_DEFAULT_LOCALE = "en-US"
_resolved_locale_cache: str | None = None
DEFAULT_RETRY_AFTER = 30.0
MAX_COOLDOWN_WAIT = 120.0
_cooldown_lock = threading.Lock()
_cooldown_until = 0.0
LICENSE_LOCK = threading.Lock()


def resolve_crunchyroll_locale(force: bool = False) -> str:
    """Detect the current IP region and return the matching Crunchyroll ``locale``."""
    global _resolved_locale_cache
    if _resolved_locale_cache is not None and not force:
        return _resolved_locale_cache

    locale = _DEFAULT_LOCALE
    try:
        location = get_my_location()
        country_code = location.get("country_code")
        if country_code in _COUNTRY_LOCALE_OVERRIDES:
            locale = _COUNTRY_LOCALE_OVERRIDES[country_code]
        elif country_code in _COUNTRIES_RESOLVED_BY_LANGUAGE:
            locale = resolve_locale(country_code.lower()) or locale
        city = location.get("city")
        logger.info(f"Crunchyroll locale resolved from region {country_code} ({city}): {locale}")
    except Exception as e:
        logger.warning(f"Region detection failed, falling back to {locale}: {e}")

    _resolved_locale_cache = locale
    return locale


class CrunchyrollError(Exception):
    """Crunchyroll refused playback (subscription, rate limit, endpoint error)."""


class CrunchyrollRateLimitError(CrunchyrollError):
    """HTTP 429: the server asked us to back off for ``retry_after`` seconds."""
    def __init__(self, message: str, retry_after: float = 0.0) -> None:
        super().__init__(message)
        self.retry_after = retry_after


def parse_retry_after(value, default: float = DEFAULT_RETRY_AFTER) -> float:
    """Seconds from a Retry-After header: either a number of seconds or an HTTP date."""
    raw = str(value or "").strip()
    if not raw:
        return default
    try:
        seconds = float(raw)
        return max(1.0, seconds) if math.isfinite(seconds) else default
    except ValueError:
        pass
    try:
        deadline = parsedate_to_datetime(raw)
        if deadline.tzinfo is None:
            deadline = deadline.replace(tzinfo=timezone.utc)
        return max(1.0, (deadline - datetime.now(timezone.utc)).total_seconds())
    except (TypeError, ValueError, OverflowError):
        return default


def note_rate_limit(retry_after: float) -> None:
    """Start (or extend) the process-wide cooldown after a 429."""
    global _cooldown_until
    with _cooldown_lock:
        _cooldown_until = max(_cooldown_until, time.monotonic() + retry_after)


def cooldown_remaining() -> float:
    with _cooldown_lock:
        return max(0.0, _cooldown_until - time.monotonic())


def wait_for_cooldown() -> None:
    """Sleep until the current cooldown is over, so no new request goes out while the server asked for a pause."""
    remaining = cooldown_remaining()
    if remaining <= 0:
        return
    if remaining > MAX_COOLDOWN_WAIT:
        raise CrunchyrollRateLimitError(f"Crunchyroll rate limited (HTTP 429); retry in {math.ceil(remaining)}s", remaining)
    console.print(f"[yellow]Crunchyroll rate limit: waiting {math.ceil(remaining)}s before the next request")
    logger.warning(f"Crunchyroll rate limit: waiting {math.ceil(remaining)}s before the next request")
    time.sleep(remaining)


def license_bytes(response) -> bytes:
    """License payload from a response: raw bytes, or JSON with a base64 ``license`` / ``widevineLicense.license`` field."""
    content = response.content or b""
    if "application/json" in response.headers.get("content-type", "").lower() or content.lstrip().startswith(b"{"):
        body = response.json()
        value = body.get("license") or (body.get("widevineLicense") or {}).get("license")
        if not isinstance(value, str) or not value:
            raise CrunchyrollError("Crunchyroll returned no license in its JSON response")
        return base64.b64decode(value)
    if not content:
        raise CrunchyrollError("Crunchyroll returned an empty license response")
    return content


def manifest_url(payload: dict) -> str:
    """DASH manifest url of a TV playback payload: the clean (soft-subtitle) one, else a hard-subbed variant, else the root url."""
    root = payload.get("url")
    if root and "/clean/" in root:
        return root
    hard_subs = payload.get("hardSubs") or {}
    for locale in ("none", "fr-FR", *hard_subs):
        if isinstance(hard_subs.get(locale), dict) and hard_subs[locale].get("url"):
            return hard_subs[locale]["url"]
    return root or ""


class CrunchyrollClient:
    def __init__(self, locale: str | None = None, **kwargs) -> None:
        self.locale = locale or resolve_crunchyroll_locale()
        self._manifests: dict[str, str] = {}
        self.email = str(config_manager.login.get("crunchyroll", "email", default="") or "").strip()
        self.password = str(config_manager.login.get("crunchyroll", "password", default="") or "")
        self.etp_rt = config_manager.login.get("crunchyroll", "etp_rt", default="")
        tv_id = str(config_manager.login.get("crunchyroll", "tv_client_id", default="") or "").strip() or TV_CLIENT_ID
        tv_secret = str(config_manager.login.get("crunchyroll", "tv_client_secret", default="") or "").strip() or TV_CLIENT_SECRET
        self.tv_basic = base64.b64encode(f"{tv_id}:{tv_secret}".encode()).decode()

        self.web_base_url = BASE_URL
        self.api_base_url = self._resolve_api_base_url()
        self.play_service_url = PLAY_SERVICE_URL
        self.token_cache_enabled = True
        self.session = None

        self._configure(use_tv=self.prefers_tv())

    def has_tv_credentials(self) -> bool:
        """True when email and password are set."""
        return bool(self.email and self.password)

    def prefers_tv(self) -> bool:
        """Android TV API (pair code / email+password) unless the account is set up for the web API only."""
        if self.has_tv_credentials() or config_manager.login.get("crunchyroll", "tv_login", data_type=bool, default=False):
            return True
        return not (self.etp_rt or config_manager.login.get("crunchyroll", "device_id", default=""))

    def _configure(self, use_tv: bool) -> None:
        """Build the per-API state: device id, license url, token cache and HTTP session."""
        self.use_tv = use_tv
        self.device_id = "" if use_tv else config_manager.login.get("crunchyroll", "device_id", default="")
        self.license_url = TV_LICENSE_URL if use_tv else WEB_LICENSE_URL
        self.token_cache_name = "token_tv" if use_tv else "token"
        self.token_cache_path = self._resolve_token_cache_path(self.token_cache_name)
        self.user_agent = TV_USER_AGENT if use_tv else None

        self.access_token: str | None = None
        self.refresh_token: str | None = None
        self.account_id: str | None = None
        self.expires_at: float = 0.0
        self.refresh_expires_at: float = 0.0

        # Load cached tokens
        cache_data = self._load_token_cache()
        if use_tv and not self.device_id:
            self.device_id = str(uuid.uuid4())
        if self.access_token and disk_cache.is_fresh({"expiry": self.expires_at}, buffer_seconds=30):
            Login.refresh_cached(self.token_cache_path)
        if not self.user_agent:
            cached_ua = cache_data.get("user_agent") if isinstance(cache_data, dict) else None
            self.user_agent = cached_ua if isinstance(cached_ua, str) and cached_ua.strip() else get_userAgent()

        if self.session:
            self.session.close()

        # The TV API is called with an Android user agent, so no Chrome TLS impersonation there
        self.session = create_client(headers=self._get_headers(), cookies=self._get_cookies(), browser=None if use_tv else "chrome")

    def _fallback_to_web(self, reason: str) -> None:
        """Drop the TV API for this client and continue with the web endpoints (cookies / anonymous)."""
        logger.warning(f"Crunchyroll TV API unavailable ({reason}), falling back to the web API")
        console.print(f"[yellow]Crunchyroll TV API unavailable ({reason}), falling back to the web API")
        self._configure(use_tv=False)

    def close(self):
        """Close the HTTP session."""
        if self.session:
            self.session.close()

    @staticmethod
    def _resolve_api_base_url() -> str:
        """Determine the correct API base URL - defaults to beta API."""
        return API_BETA_BASE_URL

    @staticmethod
    def _resolve_token_cache_path(name: str = "token") -> str:
        """Absolute path for the token cache file (informational — I/O goes through disk_cache)."""
        return disk_cache.cache_path("crunchyroll", name)

    @staticmethod
    def _jwt_exp(token: str | None) -> int | None:
        """Extract expiration timestamp from JWT token payload."""
        if not isinstance(token, str) or token.count(".") < 2:
            return None

        try:
            payload_b64 = token.split(".", 2)[1]
            padding = "=" * (-len(payload_b64) % 4)
            payload = base64.urlsafe_b64decode(payload_b64 + padding).decode("utf-8", errors="replace")
            obj = json.loads(payload)
            exp = obj.get("exp")

            if isinstance(exp, int):
                return exp
            if isinstance(exp, str) and exp.isdigit():
                return int(exp)

        except Exception:
            pass
        return None

    def _set_expires_at(self, *, expires_in: int | None = None) -> None:
        """Set token expiration time from JWT or expires_in value."""
        exp = self._jwt_exp(self.access_token)
        if isinstance(exp, int) and exp > 0:
            self.expires_at = float(exp - 60)
            return

        if expires_in is None:
            self.expires_at = 0.0
            return

        self.expires_at = time.time() + max(0, int(expires_in) - 60)

    def _load_token_cache(self) -> dict:
        """Load cached authentication tokens from file if available."""
        data = disk_cache.load("crunchyroll", self.token_cache_name)
        if not data:
            return {}

        try:
            cached_device_id = data.get("device_id")
            if self.use_tv:
                if str(data.get("email") or "").lower() != self.email.lower():
                    return {}
                if isinstance(cached_device_id, str) and cached_device_id:
                    self.device_id = cached_device_id
            elif self.device_id and isinstance(cached_device_id, str) and cached_device_id != self.device_id:
                return {}

            access = data.get("access_token")
            refresh = data.get("refresh_token")
            if isinstance(access, str) and access:
                self.access_token = access
            if isinstance(refresh, str) and refresh:
                self.refresh_token = refresh

            account_id = data.get("account_id")
            if isinstance(account_id, str) and account_id:
                self.account_id = account_id

            try:
                self.expires_at = float(data.get("expires_at") or 0.0)
            except Exception:
                self.expires_at = 0.0
            try:
                self.refresh_expires_at = float(data.get("refresh_expires_at") or 0.0)
            except Exception:
                self.refresh_expires_at = 0.0

            return data
        except Exception as e:
            logger.error(f"Token cache load failed: {e}")
            return {}

    def _save_token_cache(self) -> None:
        """Save current authentication tokens to cache file."""
        payload = {
            "device_id": self.device_id,
            "account_id": self.account_id,
            "access_token": self.access_token,
            "refresh_token": self.refresh_token,
            "expires_at": self.expires_at,
            "user_agent": self.user_agent,
            "api_base_url": self.api_base_url,
            "saved_at": time.time(),
        }
        if self.use_tv:
            payload["email"] = self.email
            payload["refresh_expires_at"] = self.refresh_expires_at
        disk_cache.save("crunchyroll", self.token_cache_name, payload)

    def _get_headers(self) -> dict:
        """Generate HTTP headers for API requests including authorization."""
        if self.use_tv:
            headers = {
                "user-agent": TV_USER_AGENT,
                "accept": "application/json",
                "accept-charset": "UTF-8",
                "accept-encoding": "gzip",
                "connection": "Keep-Alive",
                "origin": self.web_base_url,
                "referer": f"{self.web_base_url}/",
            }
            if self.access_token:
                headers["authorization"] = f"Bearer {self.access_token}"
            return headers

        headers = {
            "user-agent": self.user_agent or get_userAgent(),
            "accept": "application/json, text/plain, */*",
            "origin": self.web_base_url,
            "referer": f"{self.web_base_url}/",
            "accept-language": f"{self.locale.replace('_', '-')},en-US;q=0.8,en;q=0.7",
        }
        if self.access_token:
            headers["authorization"] = f"Bearer {self.access_token}"

        return headers

    def _get_cookies(self) -> dict:
        """Generate cookies for API requests (none for the TV API)"""
        if self.use_tv:
            return {}
        cookies = config_manager.login.get_cookies("crunchyroll")
        cookies["device_id"] = self.device_id
        if self.etp_rt:
            cookies["etp_rt"] = self.etp_rt
        return cookies

    def start(self) -> bool:
        """Authenticate using etp_rt cookie - single attempt."""
        headers = self._get_headers()
        headers["authorization"] = f"Basic {PUBLIC_TOKEN}"
        headers["content-type"] = "application/x-www-form-urlencoded"

        data = {
            "device_id": self.device_id,
            "device_type": "Chrome on Windows",
            "grant_type": "etp_rt_cookie",
        }

        response = self.session.post(
            f"{self.api_base_url}/auth/v1/token", cookies=self._get_cookies(), headers=headers, data=data
        )

        if response.status_code != 200:
            logger.error(f"Authentication failed: {response.status_code}")
            return False

        result = response.json()

        self.access_token = result.get("access_token")
        self.refresh_token = result.get("refresh_token")
        self.account_id = result.get("account_id")

        expires_in = int(result.get("expires_in", 3600) or 3600)
        self._set_expires_at(expires_in=expires_in)
        self._save_token_cache()

        Login.show(Login.ACCOUNT if self.etp_rt else Login.ANONYMOUS)
        return True

    def start_anonymous(self) -> bool:
        """Authenticate without a user account (client_credentials) for public endpoints like search."""
        headers = self._get_headers()
        headers["authorization"] = f"Basic {PUBLIC_TOKEN}"
        headers["content-type"] = "application/x-www-form-urlencoded"

        data = {
            "grant_type": "client_credentials",
            "scope": "offline_access",
        }

        try:
            response = self.session.post(
                f"{self.api_base_url}/auth/v1/token", cookies=self._get_cookies(), headers=headers, data=data
            )
        except Exception as e:
            logger.error(f"Anonymous authentication failed: {e}")
            return False

        if response.status_code != 200:
            logger.error(f"Anonymous authentication failed: {response.status_code}")
            return False

        try:
            result = response.json()
        except Exception:
            return False

        access = result.get("access_token")
        if not access:
            return False

        self.access_token = access
        if result.get("refresh_token"):
            self.refresh_token = result.get("refresh_token")
        if result.get("account_id"):
            self.account_id = result.get("account_id")

        try:
            expires_in = int(result.get("expires_in", 3600) or 3600)
        except Exception:
            expires_in = 3600
        self._set_expires_at(expires_in=expires_in)

        Login.show(Login.ANONYMOUS)
        return True

    def _refresh(self) -> None:
        """Refresh access token - single attempt."""
        if not self.refresh_token:
            raise RuntimeError("refresh_token missing")

        Login.refresh_try()
        try:
            self._refresh_request()
        except Exception as e:
            Login.refresh_failed(str(e) or e.__class__.__name__)
            raise
        Login.refresh_ok()

    def _tv_token_request(self, grant: str, **fields) -> dict:
        """POST ``/auth/v1/token`` as the Android TV client; returns the token payload."""
        data = {
            "grant_type": grant,
            "scope": "offline_access",
            "device_id": self.device_id,
            "device_type": TV_DEVICE_TYPE,
            "device_name": TV_DEVICE_NAME,
            **fields,
        }
        headers = {
            "authorization": f"Basic {self.tv_basic}",
            "content-type": "application/x-www-form-urlencoded",
            "accept-encoding": "gzip",
            "user-agent": TV_USER_AGENT,
        }
        if grant == "password":
            headers["request-type"] = "SignIn"

        response = self.session.post(f"{self.api_base_url}/auth/v1/token", headers=headers, data=data)
        if response.status_code == 429:
            retry_after = parse_retry_after(response.headers.get("Retry-After"))
            note_rate_limit(retry_after)
            raise CrunchyrollRateLimitError(f"Authentication rate limited (HTTP 429), Retry-After {math.ceil(retry_after)}s", retry_after)
        
        if response.status_code != 200:
            try:
                code = response.json().get("error")
            except Exception:
                code = None
            detail = f"; {code}" if isinstance(code, str) and code.replace("_", "").isalpha() else ""
            stage = {"password": "account sign-in", "refresh_token": "session refresh"}.get(grant, "authentication")
            hint = "; the TV client was retired, set tv_client_id / tv_client_secret in login.toml" if code == "invalid_client" else ""
            raise CrunchyrollError(f"TV {stage} rejected (HTTP {response.status_code}{detail}){hint}")
        return response.json()

    def _tv_authenticate(self) -> None:
        """TV API session: refresh the saved session, else email and password, else the pair code."""
        if self.refresh_token:
            try:
                self._refresh()
                return
            except CrunchyrollRateLimitError:
                raise
            except Exception:
                pass
        if self.has_tv_credentials():
            self._apply_token_result(self._tv_token_request("password", username=self.email, password=self.password))
            Login.show(Login.ACCOUNT)
            return
        self.login_with_tv_code()

    def _activate_with_web_session(self, user_code: str) -> bool:
        """Activate a pair code with the web session (etp_rt cookie or saved refresh token)."""
        cached = disk_cache.load("crunchyroll", "token")
        cached = cached if isinstance(cached, dict) else {}
        saved_refresh = cached.get("refresh_token") if isinstance(cached.get("refresh_token"), str) else ""

        # (what the grant sends, the etp_rt cookie sent with it): the cookie of login.toml first, then the saved web session
        attempts = []
        if self.etp_rt:
            attempts.append(({"grant_type": "etp_rt_cookie"}, self.etp_rt))
        if saved_refresh:
            attempts.append(({"grant_type": "refresh_token", "refresh_token": saved_refresh, "scope": "offline_access"}, saved_refresh))
            attempts.append(({"grant_type": "etp_rt_cookie"}, saved_refresh))
        if not attempts:
            return False

        web_device_id = config_manager.login.get("crunchyroll", "device_id", default="") or cached.get("device_id") or ""
        headers = {
            "user-agent": get_userAgent(),
            "accept": "application/json, text/plain, */*",
            "origin": self.web_base_url,
            "referer": f"{self.web_base_url}/",
        }
        try:
            access, status = None, None
            for grant_fields, etp_rt in attempts:
                cookies = dict(config_manager.login.get_cookies("crunchyroll"))
                cookies["etp_rt"] = etp_rt
                if web_device_id:
                    cookies["device_id"] = web_device_id
                grant = {**grant_fields, "device_type": "Chrome on Windows", **({"device_id": web_device_id} if web_device_id else {})}
                with create_client(headers=headers, cookies=cookies) as web:
                    token = web.post(
                        f"{API_BETA_BASE_URL}/auth/v1/token",
                        headers={**headers, "authorization": f"Basic {PUBLIC_TOKEN}", "content-type": "application/x-www-form-urlencoded"},
                        data=grant,
                    )
                    status = token.status_code
                    result = token.json() if status == 200 else {}
                    access = result.get("access_token")
                    if not access:
                        continue

                    if saved_refresh and result.get("refresh_token") and result["refresh_token"] != saved_refresh and etp_rt == saved_refresh:
                        disk_cache.save("crunchyroll", "token", {**cached, "access_token": access, "refresh_token": result["refresh_token"], "expires_at": time.time() + max(0, int(result.get("expires_in") or 300) - 60), "saved_at": time.time()})
                    break

            if not access:
                logger.warning(f"Crunchyroll web session refused the sign-in (HTTP {status}): the pair code is entered by hand")
                return False
            
            with create_client(headers=headers, cookies=cookies) as web:
                for base in (API_BETA_BASE_URL, self.web_base_url):
                    response = web.post(
                        f"{base}/auth/v1/device",
                        headers={**headers, "authorization": f"Bearer {access}", "content-type": "application/json"},
                        data=json.dumps({"user_code": user_code}),
                    )
                    if response.status_code in (200, 204):
                        return True
                    logger.warning(f"Crunchyroll {base} refused the pair code activation (HTTP {response.status_code})")
        except Exception as e:
            logger.warning(f"Crunchyroll pair code activation with the web cookies failed: {e}")
        return False

    def login_with_tv_code(self) -> None:
        """Sign in like the Android TV app: show a 6 character code to enter at crunchyroll.com/activate, then poll for the session.

        Needs a console to show the code. The session is cached and renewed with its refresh token, so the code is
        only asked once."""
        global _tv_code_failed
        if _tv_code_failed:
            raise CrunchyrollError("TV pair code was not entered")
        if not sys.stdin.isatty():
            raise CrunchyrollError("TV pair code needs a console to show the code (or set email/password, or the web cookies, in login.toml)")

        headers = {"authorization": f"Basic {self.tv_basic}", "user-agent": TV_USER_AGENT, "accept-encoding": "gzip"}
        Login.refresh_try()
        try:
            response = self.session.post(
                f"{self.api_base_url}/auth/v1/device/code", headers=headers,
                data={"device_id": self.device_id, "device_type": TV_DEVICE_TYPE, "device_name": TV_DEVICE_NAME},
            )
            if response.status_code == 429:
                retry_after = parse_retry_after(response.headers.get("Retry-After"))
                note_rate_limit(retry_after)
                raise CrunchyrollRateLimitError(f"Pair code rate limited (HTTP 429), Retry-After {math.ceil(retry_after)}s", retry_after)
            if response.status_code != 200:
                try:
                    error = str(response.json().get("error") or "")
                except Exception:
                    error = ""
                hint = "; the TV client was retired, set tv_client_id / tv_client_secret in login.toml" if error == "invalid_client" else ""
                raise CrunchyrollError(f"TV pair code request rejected (HTTP {response.status_code}{hint})")

            code = response.json()
            user_code, device_code = code.get("user_code"), code.get("device_code")
            if not user_code or not device_code:
                raise CrunchyrollError("TV pair code response was missing the code")
            expires_at = time.time() + float(code.get("expires_in") or 300)
            valid_until = time.strftime("%H:%M", time.localtime(expires_at))
            if self._activate_with_web_session(user_code):
                console.print("[cyan][+][/cyan] [white]TV code activated with the web session[/white]")
            else:
                console.print(f"[cyan][+][/cyan] [white]Enter the code[/white] [bold green]{user_code}[/bold green] [white]on[/white] [cyan]{TV_ACTIVATE_URL}[/cyan] [white](valid until {valid_until})[/white]")

            while time.time() < expires_at:
                time.sleep(TV_POLL_SECONDS)
                poll = self.session.post(
                    f"{self.api_base_url}/auth/v1/device/token",
                    headers={**headers, "content-type": "application/json"},
                    data=json.dumps({"device_id": self.device_id, "device_code": device_code}),
                )
                if poll.status_code == 429:
                    time.sleep(parse_retry_after(poll.headers.get("Retry-After")))
                    continue
                body = poll.json() if poll.status_code == 200 and (poll.content or b"").strip() else None
                if isinstance(body, dict) and body.get("access_token"):
                    self._apply_token_result(body)
                    Login.show(Login.ACCOUNT)
                    return
                if poll.status_code not in (200, 204, 400, 428):  # 204: not entered yet
                    raise CrunchyrollError(f"TV pair code rejected (HTTP {poll.status_code})")
            raise CrunchyrollError("TV pair code expired before it was entered")
        except Exception as e:
            _tv_code_failed = not isinstance(e, CrunchyrollRateLimitError)
            Login.refresh_failed(str(e)[:120] or e.__class__.__name__)
            raise

    def _refresh_request(self) -> None:
        if self.use_tv:
            self._apply_token_result(self._tv_token_request("refresh_token", refresh_token=self.refresh_token))
            return

        headers = self._get_headers()
        headers["authorization"] = f"Basic {PUBLIC_TOKEN}"
        headers["content-type"] = "application/x-www-form-urlencoded"

        data = {
            "grant_type": "refresh_token",
            "refresh_token": self.refresh_token,
            "scope": "offline_access",
            "device_type": "Chrome on Windows",
        }
        if self.device_id:
            data["device_id"] = self.device_id

        try:
            response = self.session.post(
                f"{self.api_base_url}/auth/v1/token", cookies=self._get_cookies(), headers=headers, data=data
            )
        except Exception as e:
            logger.warning(f"refresh_token grant request failed ({e}), trying etp_rt_cookie fallback")
            response = None

        if response is not None and response.status_code == 200:
            self._apply_token_result(response.json())
            return

        etp_rt_value = self.etp_rt or self.refresh_token
        if etp_rt_value:
            cookies = dict(self._get_cookies())
            cookies["etp_rt"] = etp_rt_value
            etp_data = {
                "grant_type": "etp_rt_cookie",
                "scope": "offline_access",
                "device_type": "Chrome on Windows",
            }
            if self.device_id:
                etp_data["device_id"] = self.device_id
            try:
                etp_response = self.session.post(
                    f"{self.api_base_url}/auth/v1/token", cookies=cookies, headers=headers, data=etp_data
                )
            except Exception as e:
                logger.error(f"Token refresh requests failed: {e}")
                raise RuntimeError(f"Token refresh failed: {e}") from e
            if etp_response.status_code == 200:
                self._apply_token_result(etp_response.json())
                return
            first_status = response.status_code if response is not None else "ERR"
            logger.error(
                f"Token refresh failed: {first_status} / etp_rt_cookie {etp_response.status_code}"
            )
        else:
            logger.error(f"Token refresh failed: {response.status_code if response is not None else 'ERR'}")
        raise RuntimeError(f"Token refresh failed: {response.status_code if response is not None else 'request failed'}")

    def _apply_token_result(self, result: dict) -> None:
        """Store a successful /auth/v1/token response and persist the cache."""
        access = result.get("access_token")
        if not access:
            raise RuntimeError("Token refresh returned no access_token")
        self.access_token = access
        self.refresh_token = result.get("refresh_token") or self.refresh_token
        if result.get("account_id"):
            self.account_id = result.get("account_id")
        if self.use_tv and result.get("refresh_token"):
            self.refresh_expires_at = time.time() + TV_REFRESH_TTL

        try:
            expires_in = int(result.get("expires_in", 3600) or 3600)
        except Exception:
            expires_in = 3600
        self._set_expires_at(expires_in=expires_in)
        self._save_token_cache()

    def _ensure_token(self) -> None:
        """Ensure valid access token - no retries."""
        if self.use_tv:
            if self.access_token and disk_cache.is_fresh({"expiry": self.expires_at}, buffer_seconds=30):
                return
            try:
                self._tv_authenticate()
                return
            except CrunchyrollRateLimitError:
                raise
            except Exception as e:
                self._fallback_to_web(str(e) or e.__class__.__name__)

        if not self.access_token:
            if self.etp_rt and self.start():
                return
            if self.start_anonymous():
                return
            raise RuntimeError("Authentication failed")

        # Refresh if expiring soon
        if not disk_cache.is_fresh({"expiry": self.expires_at}, buffer_seconds=30):
            try:
                self._refresh()
                return
            except Exception:
                pass
            if self.etp_rt and self.start():
                return
            if self.start_anonymous():
                return
            raise RuntimeError("Re-authentication failed") from None

    def request(self, method: str, url: str, **kwargs):
        """Single request attempt - no retries."""
        self._ensure_token()

        headers = kwargs.pop("headers", {}) or {}
        merged_headers = {**self._get_headers(), **headers}
        kwargs["headers"] = merged_headers
        kwargs.setdefault("cookies", self._get_cookies())
        kwargs.setdefault("timeout", config_manager.config.get_int("REQUESTS", "timeout"))

        response = self.session.request(method, url, **kwargs)

        # Only handle 401 once
        if response.status_code == 401:
            try:
                if self.use_tv:
                    self._tv_authenticate()
                else:
                    self._refresh()
            except Exception:
                if self.use_tv:
                    pass
                elif self.etp_rt:
                    self.start()
                else:
                    self.start_anonymous()
            kwargs["headers"] = {**self._get_headers(), **headers}
            response = self.session.request(method, url, **kwargs)

        return response

    def refresh(self) -> None:
        """Public refresh method."""
        self._refresh()

    def get_streams(self, media_id: str, max_retries: int = 3, audio_locale: str | None = None) -> dict:
        """Get playback data, retrying with backoff on rate-limit/concurrent-stream errors."""
        for attempt in range(max_retries + 1):
            try:
                return self._get_streams_once(media_id, audio_locale)
            except CrunchyrollError as e:
                is_rate_limited = "429" in str(e) or "TOO_MANY_ACTIVE_STREAMS" in str(e)
                if not is_rate_limited or attempt >= max_retries:
                    raise

                if isinstance(e, CrunchyrollRateLimitError):
                    continue  # the process-wide cooldown (Retry-After) is honoured by the next attempt

                cleared = self.clear_all_sessions()
                wait_time = min(5 * (2**attempt), 30)
                if not cleared:
                    wait_time = max(wait_time, 15)
                console.print(f"[yellow]Crunchyroll rate limit ({e}); cleared {cleared} session(s), waiting {wait_time}s before retry {attempt + 1}/{max_retries}")
                logger.warning(f"Crunchyroll rate limit ({e}); cleared {cleared} session(s), waiting {wait_time}s before retry {attempt + 1}/{max_retries}")
                time.sleep(wait_time)

        raise CrunchyrollError(f"Playback failed for {media_id} after {max_retries} retries")

    def get_available_versions(self, url_id: str) -> list[dict]:
        """
        Return the audio versions the account is actually entitled to, taken from the playback API ``versions`` array (each entry carries its own GUID).
        
        Returns:
            List of dicts: {"guid", "audio_locale", "roles"}.
        """
        try:
            playback_data = self.get_streams(url_id)
            versions_list = playback_data.get("versions") or []

            # Free the streaming slot opened by get_streams
            token = playback_data.get("token") or _find_token_recursive(playback_data)
            if token:
                self.deauth_video(url_id, token)

            result = []
            seen = set()
            for v in versions_list:
                guid = v.get("guid") or v.get("id")
                locale = v.get("audio_locale")
                if guid and locale and locale not in seen:
                    seen.add(locale)
                    result.append({"guid": guid, "audio_locale": locale, "roles": v.get("roles") or []})

            return result

        except Exception as e:
            logger.error(f"get_available_versions failed for {url_id}: {e}")
            return []

    def _get_streams_once(self, media_id: str, audio_locale: str | None = None) -> dict:
        """Single playback-data fetch attempt (no retry)."""
        if self.use_tv:
            pb_url = self.play_service_url + TV_PLAY_PATH.format(media_id=media_id)
            params = {"queue": "false", **({"audio": audio_locale} if audio_locale else {})}
        else:
            pb_url = f"{self.play_service_url}/v3/{media_id}/web/chrome/play"
            params = {"locale": self.locale}

        wait_for_cooldown()
        response = self.request("GET", pb_url, params=params)

        if self.use_tv and response.status_code in (403, 404):
            # Same content, the phone download profile of the TV API
            pb_url = self.play_service_url + TV_DOWNLOAD_PATH.format(media_id=media_id)
            wait_for_cooldown()
            response = self.request("GET", pb_url, params=params)

        if response.status_code == 429:
            retry_after = parse_retry_after(response.headers.get("Retry-After"))
            note_rate_limit(retry_after)
            raise CrunchyrollRateLimitError(f"Playback rate limited (HTTP 429), Retry-After {math.ceil(retry_after)}s", retry_after)

        if response.status_code == 403:
            raise CrunchyrollError("Playback Rejected: Subscription required")

        if response.status_code == 404:
            raise CrunchyrollError(f"Playback endpoint not found: {pb_url}")

        if response.status_code == 420:
            try:
                payload = response.json()
                error_code = payload.get("error")
                active_streams = payload.get("activeStreams", [])

                if error_code in ("TOO_MANY_ACTIVE_STREAMS", "TOO_MANY_CONCURRENT_STREAMS") and active_streams:
                    logger.error(f"TOO_MANY_ACTIVE_STREAMS: cleaning up {len(active_streams)} streams")

                    for s in active_streams:
                        if isinstance(s, dict):
                            content_id = s.get("contentId")
                            token = s.get("token")
                            if content_id and token:
                                self.deauth_video(content_id, token)
            except Exception:
                pass

            raise CrunchyrollError("TOO_MANY_ACTIVE_STREAMS. Wait and try again.")

        if response.status_code != 200:
            raise CrunchyrollError(f"Playback failed: {response.status_code}")

        data = response.json()

        if data.get("error") == "Playback is Rejected":
            raise CrunchyrollError("Playback Rejected: Premium required")

        return data

    def get_active_sessions(self) -> list[dict]:
        """List every currently-open streaming session for this account."""
        try:
            response = self.request("GET", f"{self.web_base_url}/playback/v1/sessions/streaming")
            if response.status_code != 200:
                logger.warning(f"Failed to list active sessions (status {response.status_code})")
                return []
            return response.json().get("items") or []
        except Exception as e:
            logger.warning(f"Error listing active sessions: {e}")
            return []

    def clear_all_sessions(self) -> int:
        """Close every open streaming session on the account (e.g. leaked by a crashed/killed run)."""
        sessions = self.get_active_sessions()
        cleared = 0
        for s in sessions:
            content_id = s.get("contentId")
            token = s.get("token")
            if content_id and token and self.deauth_video(content_id, token):
                cleared += 1

        if cleared:
            console.print(f"[dim]Cleared {cleared} leftover Crunchyroll streaming session(s).")
            logger.info(f"Cleared {cleared} leftover Crunchyroll streaming session(s)")
        return cleared

    def deauth_video(self, media_id: str, token: str) -> bool:
        """Mark playback token as inactive to free stream slot."""
        if not media_id or not token:
            return False

        try:
            if self.use_tv:
                response = self.session.delete(
                    f"{PLAY_SERVICE_URL}/v1/token/{media_id}/{token}",
                    headers=self._get_headers(),
                )
            else:
                response = self.session.patch(
                    f"{PLAY_SERVICE_URL}/v1/token/{media_id}/{token}/inactive",
                    cookies=self._get_cookies(),
                    headers=self._get_headers(),
                )
            return response.status_code in (200, 204)

        except Exception as e:
            logger.error(f"Failed to deauth stream token: {e}")
            return False

    def prefetch_manifest(self, mpd_url: str) -> str:
        """TV only: read the MPD while its playback session is still open (the session is released right after), keep the text and return the final manifest url."""
        try:
            response = self.session.get(mpd_url, headers=self._get_headers())
            text = response.text
            if response.status_code != 200 or "<MPD" not in text and ":MPD" not in text:
                logger.warning(f"Crunchyroll manifest prefetch returned HTTP {response.status_code}, the downloader will fetch it itself")
                return mpd_url
        except Exception as e:
            logger.warning(f"Crunchyroll manifest prefetch failed ({e}), the downloader will fetch it itself")
            return mpd_url

        final_url = str(getattr(response, "url", "") or mpd_url)
        self._manifests[final_url] = text
        return final_url

    def cached_manifest(self, mpd_url: str) -> str | None:
        """MPD text read by :meth:`prefetch_manifest`, or None."""
        return self._manifests.get(mpd_url)

    def license_video_token(self, mpd_url: str | None, token: str | None) -> str | None:
        """Value of the ``x-cr-video-token`` license header: the playback token on TV, the ``playbackGuid`` of the manifest url on web."""
        if self.use_tv:
            return token
        return (parse_qs(urlparse(mpd_url or "").query).get("playbackGuid") or [token])[0]

    def request_license(self, content_id: str, challenge: bytes, license_url: str | None = None) -> bytes:
        """Request a license for the given content_id and challenge bytes."""
        license_url = license_url or self.license_url
        with LICENSE_LOCK:
            data = self.get_streams(content_id)
            token = data.get("token") or _find_token_recursive(data)
            try:
                playback_guid = self.license_video_token(data.get("url"), token)
                if not playback_guid:
                    raise CrunchyrollError("Playback session carries no token for the license request")

                headers = {
                    **self._get_headers(),
                    "x-cr-content-id": content_id,
                    "x-cr-video-token": playback_guid,
                    "Content-Type": "application/octet-stream",
                }
                wait_for_cooldown()
                response = self.session.post(license_url, data=challenge, headers=headers, cookies=self._get_cookies())
                if response.status_code == 429:
                    retry_after = parse_retry_after(response.headers.get("Retry-After"))
                    note_rate_limit(retry_after)
                    raise CrunchyrollRateLimitError(f"License rate limited (HTTP 429), Retry-After {math.ceil(retry_after)}s", retry_after)
                if response.status_code != 200:
                    raise CrunchyrollError(f"License request failed: HTTP {response.status_code}")
                return license_bytes(response)
            finally:
                if token:
                    self.deauth_video(content_id, token)


def _find_token_recursive(obj) -> str | None:
    """Recursively search for 'token' field in playback response."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            if str(k).lower() == "token" and isinstance(v, str) and len(v) > 10:
                return v
            token = _find_token_recursive(v)
            if token:
                return token
    elif isinstance(obj, list):
        for el in obj:
            token = _find_token_recursive(el)
            if token:
                return token
    return None


def _extract_subtitles(data: dict) -> list[dict]:
    """Extract all subtitles from playback data."""
    subtitles = []

    # Process regular subtitles
    subs_obj = data.get("subtitles") or {}
    for lang, info in subs_obj.items():
        if not info or not info.get("url"):
            continue

        subtitles.append(
            {
                "language": lang,
                "url": info["url"],
                "format": info.get("format") or "ass",
                "type": info.get("type"),
                "closed_caption": bool(info.get("closed_caption")),
                "label": info.get("display") or info.get("title") or info.get("language"),
            }
        )

    # Process captions/closed captions
    captions_obj = data.get("captions") or data.get("closed_captions") or {}
    for lang, info in captions_obj.items():
        if not info or not info.get("url"):
            continue

        subtitles.append(
            {
                "language": lang,
                "url": info["url"],
                "format": info.get("format") or "vtt",
                "type": info.get("type") or "captions",
                "closed_caption": True if info.get("closed_caption") is None else bool(info.get("closed_caption")),
                "label": info.get("display") or info.get("title") or info.get("language"),
            }
        )

    return subtitles


def get_playback_session(
    client: CrunchyrollClient, url_id: str, main_guid: str | None = None
) -> tuple[str, dict, list[dict], str | None, str | None]:
    """
    Get playback session with SINGLE API call.
    If main_guid is provided, fetch subtitles from main track for complete subs.

    Returns:
        - mpd_url: str
        - headers: Dict
        - subtitles: List[Dict]
        - token: Optional[str]
        - audio_locale: Optional[str]
    """
    playback_data = client.get_streams(url_id)

    # Extract relevant data
    mpd_url = manifest_url(playback_data) if client.use_tv else playback_data.get("url")
    audio_locale = playback_data.get("audioLocale") or playback_data.get("audio_locale") or (playback_data.get("audio") or {}).get("locale")
    token = playback_data.get("token") or _find_token_recursive(playback_data)

    # Get subtitles: prefer main_guid for complete subtitles if available
    if main_guid and main_guid != url_id:
        try:
            # Fetch subtitles from main track
            main_playback_data = client.get_streams(main_guid)
            subtitles = _extract_subtitles(main_playback_data)

            # Deauth main track token
            main_token = main_playback_data.get("token") or _find_token_recursive(main_playback_data)
            if main_token:
                client.deauth_video(main_guid, main_token)

        except Exception as e:
            logger.error(f"Failed to fetch subtitles from main track: {e}")
            subtitles = _extract_subtitles(playback_data)

    else:
        subtitles = _extract_subtitles(playback_data)

    if client.use_tv and mpd_url:
        mpd_url = client.prefetch_manifest(mpd_url)

    # Immediately deauth to free stream slot (non-blocking)
    if token:
        try:
            client.deauth_video(url_id, token)
        except Exception as e:
            logger.error(f"Deauth during playback failed: {e}")

    headers = client._get_headers()
    return mpd_url, headers, subtitles, token, audio_locale

def get_episode_chapters(client: CrunchyrollClient, episode_id: str) -> list[dict]:
    """Fetch intro/recap/credits/preview skip-events for an episode and turn them into chapter markers."""
    data = None
    for url in (SKIP_EVENTS_URL, INTRO_DATALAB_URL):
        try:
            response = client.session.get(url.format(episode_id=episode_id))
            if response.status_code == 200:
                data = response.json()
                break
        except Exception as e:
            logger.warning(f"Failed to fetch chapters for {episode_id} from {url}: {e}")

    if not isinstance(data, dict):
        return []

    special_chapters = []
    for chapter_type in ("intro", "recap", "credits", "preview"):
        info = data.get(chapter_type)
        if not info:
            continue
        try:
            start = float(info["start"])
            end = float(info.get("end", start))
            special_chapters.append({"start": start, "end": end, "name": chapter_type.capitalize()})
        except Exception:
            continue

    # datalab-intro-v2 only knows the opening: {"startTime": s, "endTime": e}
    if not special_chapters and "startTime" in data and "endTime" in data:
        try:
            special_chapters.append({"start": float(data["startTime"]), "end": float(data["endTime"]), "name": "Intro"})
        except Exception:
            pass

    if not special_chapters:
        return []

    special_chapters.sort(key=lambda c: c["start"])

    chapters = [{"name": "Chapter 1", "seconds": 0}]
    counter = 2
    for idx, special in enumerate(special_chapters):
        chapters.append({"name": special["name"], "seconds": special["start"]})

        should_add_after = False
        if special["end"] > special["start"]:
            if idx + 1 < len(special_chapters):
                should_add_after = special_chapters[idx + 1]["start"] - special["end"] > 2.0
            else:
                should_add_after = True

        if should_add_after:
            chapters.append({"name": f"Chapter {counter}", "seconds": special["end"]})
            counter += 1

    return chapters
