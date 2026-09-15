# 29.12.25

import base64
import json
import logging
import time

from rich.console import Console

from VibraVid.services._base.login_status import ACCOUNT, ANONYMOUS, print_login
from VibraVid.utils import config_manager, disk_cache
from VibraVid.utils.http_client import create_client, get_my_location, get_userAgent

console = Console()
logger = logging.getLogger(__name__)
PUBLIC_TOKEN = "bm9haWhkZXZtXzZpeWcwYThsMHE6"
BASE_URL = "https://www.crunchyroll.com"
API_BETA_BASE_URL = "https://beta-api.crunchyroll.com"
PLAY_SERVICE_URL = "https://cr-play-service.prd.crunchyrollsvc.com"
SKIP_EVENTS_URL = "https://static.crunchyroll.com/skip-events/production/{episode_id}.json"
_COUNTRY_TO_LOCALE = {
    "IN": "en-IN",
    "IT": "it-IT",
    "US": "en-US",
    "GB": "en-GB",
    "CA": "en-CA",
    "AU": "en-AU",
    "IE": "en-IE",
    "NZ": "en-NZ",
    "DE": "de-DE",
    "FR": "fr-FR",
    "ES": "es-ES",
    "MX": "es-419",
    "AR": "es-419",
    "BR": "pt-BR",
    "PT": "pt-PT",
    "JP": "ja-JP",
    "SA": "ar-SA",
    "AE": "ar-SA",
    "RU": "ru-RU",
    "TR": "tr-TR",
    "TH": "th-TH",
    "ID": "id-ID",
    "PH": "en-PH",
    "SG": "en-SG",
}
_DEFAULT_LOCALE = "en-US"
_resolved_locale_cache: str | None = None


def resolve_crunchyroll_locale(force: bool = False) -> str:
    """Detect the current IP region and return the matching Crunchyroll ``locale``."""
    global _resolved_locale_cache
    if _resolved_locale_cache is not None and not force:
        return _resolved_locale_cache

    locale = _DEFAULT_LOCALE
    try:
        location = get_my_location()
        country_code = location.get("country_code")
        if country_code and country_code in _COUNTRY_TO_LOCALE:
            locale = _COUNTRY_TO_LOCALE[country_code]
        city = location.get("city")
        logger.info(f"Crunchyroll locale resolved from region {country_code} ({city}): {locale}")
    except Exception as e:
        logger.warning(f"Region detection failed, falling back to {locale}: {e}")

    _resolved_locale_cache = locale
    return locale


class CrunchyrollError(Exception):
    """Crunchyroll refused playback (subscription, rate limit, endpoint error)."""


class CrunchyrollClient:
    def __init__(self, locale: str | None = None, **kwargs) -> None:
        self.locale = locale or resolve_crunchyroll_locale()
        self.device_id = config_manager.login.get("crunchyroll", "device_id")
        self.etp_rt = config_manager.login.get("crunchyroll", "etp_rt")

        self.web_base_url = BASE_URL
        self.api_base_url = self._resolve_api_base_url()
        self.play_service_url = PLAY_SERVICE_URL
        self.token_cache_path = self._resolve_token_cache_path()
        self.token_cache_enabled = True
        self.user_agent = None

        self.access_token: str | None = None
        self.refresh_token: str | None = None
        self.account_id: str | None = None
        self.expires_at: float = 0.0

        # Load cached tokens
        cache_data = self._load_token_cache()
        if not self.user_agent:
            cached_ua = cache_data.get("user_agent") if isinstance(cache_data, dict) else None
            self.user_agent = cached_ua if isinstance(cached_ua, str) and cached_ua.strip() else get_userAgent()

        self.session = create_client(headers=self._get_headers(), cookies=self._get_cookies())

    def close(self):
        """Close the HTTP session."""
        if self.session:
            self.session.close()

    @staticmethod
    def _resolve_api_base_url() -> str:
        """Determine the correct API base URL - defaults to beta API."""
        return API_BETA_BASE_URL

    @staticmethod
    def _resolve_token_cache_path() -> str:
        """Absolute path for the token cache file (informational — I/O goes through disk_cache)."""
        return disk_cache.cache_path("crunchyroll", "token")

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
        data = disk_cache.load("crunchyroll", "token")
        if not data:
            return {}

        try:
            cached_device_id = data.get("device_id")
            if self.device_id and isinstance(cached_device_id, str) and cached_device_id != self.device_id:
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
        disk_cache.save("crunchyroll", "token", payload)

    def _get_headers(self) -> dict:
        """Generate HTTP headers for API requests including authorization."""
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
        """Generate cookies for API requests"""
        cookies = dict(config_manager.login.get_section("crunchyroll"))
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

        print_login(ACCOUNT if self.etp_rt else ANONYMOUS, resolver=self._account_name)
        return True

    def _account_name(self) -> str:
        """Account name behind the etp_rt cookie, via Crunchyroll's own profile endpoint."""
        # Deliberately not routed through request(): that retries via start() on a 401, which would
        # re-enter the very call this runs from and print the banner twice.
        response = self.session.get(
            f"{self.api_base_url}/accounts/v1/me/profile",
            headers=self._get_headers(),
            cookies=self._get_cookies(),
        )
        response.raise_for_status()
        profile = response.json()
        return profile.get("email") or profile.get("username") or profile.get("profile_name") or ""

    def _refresh(self) -> None:
        """Refresh access token - single attempt."""
        if not self.refresh_token:
            raise RuntimeError("refresh_token missing")

        headers = self._get_headers()
        headers["authorization"] = f"Basic {PUBLIC_TOKEN}"
        headers["content-type"] = "application/x-www-form-urlencoded"

        data = {
            "grant_type": "refresh_token",
            "refresh_token": self.refresh_token,
            "device_type": "Chrome on Windows",
        }
        if self.device_id:
            data["device_id"] = self.device_id

        response = self.session.post(
            f"{self.api_base_url}/auth/v1/token", cookies=self._get_cookies(), headers=headers, data=data
        )

        if response.status_code != 200:
            logger.error(f"Token refresh failed: {response.status_code}")
            raise RuntimeError(f"Token refresh failed: {response.status_code}")

        result = response.json()
        self.access_token = result.get("access_token")
        self.refresh_token = result.get("refresh_token") or self.refresh_token

        expires_in = int(result.get("expires_in", 3600) or 3600)
        self._set_expires_at(expires_in=expires_in)
        self._save_token_cache()

    def _ensure_token(self) -> None:
        """Ensure valid access token - no retries."""
        if not self.access_token:
            if not self.start():
                raise RuntimeError("Authentication failed")
            return

        # Refresh if expiring soon
        if not disk_cache.is_fresh({"expiry": self.expires_at}, buffer_seconds=30):
            try:
                self._refresh()
            except Exception:
                if not self.start():
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
                self._refresh()
            except Exception:
                self.start()
            kwargs["headers"] = {**self._get_headers(), **headers}
            response = self.session.request(method, url, **kwargs)

        return response

    def refresh(self) -> None:
        """Public refresh method."""
        self._refresh()

    def get_streams(self, media_id: str, max_retries: int = 3) -> dict:
        """Get playback data, retrying with backoff on rate-limit/concurrent-stream errors."""
        for attempt in range(max_retries + 1):
            try:
                return self._get_streams_once(media_id)
            except CrunchyrollError as e:
                is_rate_limited = "429" in str(e) or "TOO_MANY_ACTIVE_STREAMS" in str(e)
                if not is_rate_limited or attempt >= max_retries:
                    raise

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

    def _get_streams_once(self, media_id: str) -> dict:
        """Single playback-data fetch attempt (no retry)."""
        pb_url = f"{self.play_service_url}/v3/{media_id}/web/chrome/play"

        response = self.request("GET", pb_url, params={"locale": self.locale})

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
            response = self.session.patch(
                f"{PLAY_SERVICE_URL}/v1/token/{media_id}/{token}/inactive",
                cookies=self._get_cookies(),
                headers=self._get_headers(),
            )
            return response.status_code in (200, 204)

        except Exception as e:
            logger.error(f"Failed to deauth stream token: {e}")
            return False

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
    mpd_url = playback_data.get("url")
    audio_locale = playback_data.get("audio_locale") or playback_data.get("audio", {}).get("locale")
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
    try:
        response = client.session.get(SKIP_EVENTS_URL.format(episode_id=episode_id))
        if response.status_code != 200:
            return []
        data = response.json()
    except Exception as e:
        logger.warning(f"Failed to fetch chapters for {episode_id}: {e}")
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
