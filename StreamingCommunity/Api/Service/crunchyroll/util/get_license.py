# 28.07.25

import time
import logging
from typing import Tuple, List, Dict, Optional


# Internal utilities
from StreamingCommunity.Util.config_json import config_manager
from StreamingCommunity.Util.http_client import create_client_curl, get_userAgent


# Variable
PUBLIC_TOKEN = "bm9haWhkZXZtXzZpeWcwYThsMHE6"
BASE_URL = "https://www.crunchyroll.com"
DEFAULT_QPS = 3.0               # Queries per second to avoid rate limiting
DEFAULT_MAX_RETRIES = 3         # Maximum retry attempts for failed requests
DEFAULT_BASE_BACKOFF_MS = 300   # Base backoff time in milliseconds
DEFAULT_SLOWDOWN_AFTER = 50     # Number of requests before introducing slowdown


class PlaybackError(Exception):
    """Custom exception for playback-related errors that shouldn't crash the program"""
    pass


class RateLimiter:
    """Simple token-bucket rate limiter to avoid server-side throttling."""
    def __init__(self, qps: float):
        self.qps = max(0.1, float(qps))
        self._last = 0.0

    def wait(self):
        if self.qps <= 0:
            return
        now = time.time()
        min_dt = 1.0 / self.qps
        dt = now - self._last
        if dt < min_dt:
            time.sleep(min_dt - dt)
        self._last = time.time()


class CrunchyrollClient:
    def __init__(self) -> None:
        config = config_manager.get_dict("SITE_LOGIN", "crunchyroll")
        self.device_id = str(config.get('device_id')).strip()
        self.etp_rt = str(config.get('etp_rt')).strip()
        self.locale = "it-IT"
        
        self.access_token: Optional[str] = None
        self.refresh_token: Optional[str] = None
        self.account_id: Optional[str] = None
        self.expires_at: float = 0.0                # epoch timestamp
        
        # Rate limiting configuration
        self.rate_limiter = RateLimiter(qps=DEFAULT_QPS)
        self._req_count = 0
        
        # Retry configuration
        self.max_retries = DEFAULT_MAX_RETRIES
        self.base_backoff_ms = DEFAULT_BASE_BACKOFF_MS
        self.slowdown_after = DEFAULT_SLOWDOWN_AFTER

    def _get_headers(self) -> Dict:
        headers = {
            'user-agent': get_userAgent(),
            'accept': 'application/json, text/plain, */*',
            'origin': BASE_URL,
            'referer': f'{BASE_URL}/',
        }
        if self.access_token:
            headers['authorization'] = f'Bearer {self.access_token}'
        return headers
    
    def _get_cookies(self) -> Dict:
        cookies = {'device_id': self.device_id}
        if self.etp_rt:
            cookies['etp_rt'] = self.etp_rt
        return cookies

    def start(self) -> bool:
        """Authorize the client with etp_rt_cookie grant."""
        headers = self._get_headers()
        headers['authorization'] = f'Basic {PUBLIC_TOKEN}'
        headers['content-type'] = 'application/x-www-form-urlencoded'
        
        data = {
            'device_id': self.device_id,
            'device_type': 'Chrome on Windows',
            'grant_type': 'etp_rt_cookie',
        }

        self.rate_limiter.wait()
        response = create_client_curl(headers=headers).post(
            f'{BASE_URL}/auth/v1/token',
            cookies=self._get_cookies(),
            data=data
        )
        self._req_count += 1
        
        if response.status_code == 400:
            logging.error("Error 400: Invalid 'etp_rt' in config.json")
            return False
            
        response.raise_for_status()
        result = response.json()
        
        self.access_token = result.get('access_token')
        self.refresh_token = result.get('refresh_token')
        self.account_id = result.get('account_id')
        
        # Set expiration with 60s margin to refresh proactively
        expires_in = int(result.get('expires_in', 3600) or 3600)
        self.expires_at = time.time() + max(0, expires_in - 60)
        
        return True

    def _refresh(self) -> None:
        """Refresh access token using refresh_token."""
        if not self.refresh_token:
            raise RuntimeError("refresh_token missing")
            
        headers = self._get_headers()
        headers['authorization'] = f'Basic {PUBLIC_TOKEN}'
        headers['content-type'] = 'application/x-www-form-urlencoded'
        
        data = {
            'grant_type': 'refresh_token',
            'refresh_token': self.refresh_token,
            'device_type': 'Chrome on Windows',
        }
        if self.device_id:
            data['device_id'] = self.device_id

        self.rate_limiter.wait()
        response = create_client_curl(headers=headers).post(
            f'{BASE_URL}/auth/v1/token',
            cookies=self._get_cookies(),
            data=data
        )
        self._req_count += 1
        response.raise_for_status()
        
        result = response.json()
        self.access_token = result.get('access_token')
        self.refresh_token = result.get('refresh_token') or self.refresh_token
        
        # Set expiration with 60s margin to refresh proactively
        expires_in = int(result.get('expires_in', 3600) or 3600)
        self.expires_at = time.time() + max(0, expires_in - 60)

    def _ensure_token(self) -> None:
        """Ensure access_token is valid and not expired."""
        if not self.access_token:
            if not self.start():
                raise RuntimeError("Authentication failed")
            return
            
        # Proactive refresh if token is expiring soon
        if time.time() >= (self.expires_at - 30):
            try:
                self._refresh()

            except Exception as e:
                logging.warning(f"Refresh failed, re-authenticating: {e}")
                if not self.start():
                    raise RuntimeError("Re-authentication failed")

    def _request_with_retry(self, method: str, url: str, **kwargs):
        """
        Make HTTP request with automatic retry on transient errors.
        """
        self._ensure_token()
        
        headers = kwargs.pop('headers', {}) or {}
        merged_headers = {**self._get_headers(), **headers}
        kwargs['headers'] = merged_headers
        kwargs.setdefault('cookies', self._get_cookies())
        
        attempt = 0
        while True:
            self.rate_limiter.wait()
            
            # Introduce slowdown after many requests
            if self._req_count >= self.slowdown_after:
                time.sleep((self.base_backoff_ms + 200) / 1000.0)
            
            response = create_client_curl(headers=kwargs['headers']).request(method, url, **kwargs)
            self._req_count += 1
            
            # Retry on 401 (token expired)
            if response.status_code == 401 and attempt < self.max_retries:
                attempt += 1
                logging.warning(f"401 error, refreshing token (attempt {attempt}/{self.max_retries})")

                try:
                    self._refresh()
                except Exception:
                    self.start()

                kwargs['headers'] = {**self._get_headers(), **headers}
                time.sleep((self.base_backoff_ms * attempt) / 1000.0)
                continue
            
            # Retry on transient server errors
            if response.status_code in (502, 503, 504) and attempt < self.max_retries:
                attempt += 1
                backoff = (self.base_backoff_ms * attempt + 100) / 1000.0
                logging.warning(f"{response.status_code} error, backing off {backoff}s (attempt {attempt}/{self.max_retries})")
                time.sleep(backoff)
                continue
            
            return response

    def get_streams(self, media_id: str) -> Optional[Dict]:
        """
        Get available streams for media_id.
        """
        response = self._request_with_retry(
            'GET',
            f'{BASE_URL}/playback/v3/{media_id}/web/chrome/play',
            params={'locale': self.locale}
        )

        if response.status_code == 403:
            logging.warning(f"Access denied for media {media_id}: Subscription required")
            return None
        
        if response.status_code == 420:
            raise PlaybackError("TOO_MANY_ACTIVE_STREAMS. Wait a few minutes and try again.")
        
        response.raise_for_status()
        
        data = response.json()
        
        if data.get('error') == 'Playback is Rejected':
            logging.warning(f"Playback rejected for media {media_id}: Premium required")
            return None
        
        return data

    def delete_active_stream(self, media_id: str, token: str) -> bool:
        """Delete an active stream session (cleanup to avoid TOO_MANY_ACTIVE_STREAMS)."""
        if not token:
            return False
        
        try:
            self.rate_limiter.wait()
            response = create_client_curl(headers=self._get_headers()).delete(
                f'{BASE_URL}/playback/v1/token/{media_id}/{token}',
                cookies=self._get_cookies()
            )
            self._req_count += 1
            return response.status_code in [200, 204]
        
        except Exception as e:
            logging.warning(f"Failed to delete stream: {e}")
            return False


def _find_token_anywhere(obj) -> Optional[str]:
    """Recursively search for 'token' field in playback response."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k.lower() == "token" and isinstance(v, str) and len(v) > 10:
                return v
            t = _find_token_anywhere(v)
            if t:
                return t
            
    elif isinstance(obj, list):
        for el in obj:
            t = _find_token_anywhere(el)
            if t:
                return t
            
    return None


def get_playback_session(client: CrunchyrollClient, url_id: str) -> Optional[Tuple[str, Dict, List[Dict], Optional[str], Optional[str]]]:
    """
    Return the playback session details.
    
    Returns:
        Tuple with (mpd_url, headers, subtitles, token, audio_locale) or None if access denied
    """
    data = client.get_streams(url_id)
    
    # If get_streams returns None, it means access was denied (403)
    if data is None:
        return None
    
    url = data.get('url')
    audio_locale_current = data.get('audio_locale') or data.get('audio', {}).get('locale')
    
    # Collect subtitles with metadata
    subtitles = []
    subs_obj = data.get('subtitles') or {}
    if isinstance(subs_obj, dict):
        for lang, info in subs_obj.items():
            if not info:
                continue
            sub_url = info.get('url')
            if not sub_url:
                continue
            
            subtitles.append({
                'language': lang,
                'url': sub_url,
                'format': info.get('format'),
                'type': info.get('type'),                           # "subtitles" | "captions"
                'closed_caption': bool(info.get('closed_caption')),
                'label': info.get('display') or info.get('title') or info.get('language')
            })
    
    token = _find_token_anywhere(data)
    headers = client._get_headers()
    
    return url, headers, subtitles, token, audio_locale_current