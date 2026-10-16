# 29.01.24

import json
import logging
import os
import sys
import threading
import time
from typing import Any

try:
    import tomllib
except ImportError:
    import tomli as tomllib  # type: ignore

import tomlkit
from curl_cffi import requests
from rich.console import Console

from . import _startup_prefetch

console = Console()
logger = logging.getLogger(__name__)

CONFIG_FILENAME = "config.toml"
LOGIN_FILENAME = "login.toml"
DOMAINS_FILENAME = "domains.json"
GITHUB_DOMAINS_PATH = ".github/script/domains.json"

CONFIG_DOWNLOAD_URL = "https://raw.githubusercontent.com/AstraeLabs/VibraVid/refs/heads/main/Conf/config.toml"
CONFIG_LOGIN_DOWNLOAD_URL = "https://raw.githubusercontent.com/AstraeLabs/VibraVid/refs/heads/main/Conf/login.toml"
DOMAIN_REFRESH_INTERVAL_SECONDS = 1800


DEFAULT_CONFIG = {
    "DEFAULT": {
        "debug_track_json": False,
        "log_level": "INFO",
        "close_console": True,
        "show_message": True,
        "fetch_domain_online": True,
        "disable_scraper_cache": False,
        "imp_service": ["default"],
        "installation": "",
        "metadata_provider": "tmdb,imdb",
    },
    "OUTPUT": {
        "root_path": "Video",
        "movie_folder_name": "Movie",
        "serie_folder_name": "Serie",
        "anime_folder_name": "Anime",
        "music_folder_name": "Music",
        "live_folder_name": "Live",
        "movie_format": "%(title_name) (%(title_year))/%(title_name) (%(title_year)) [%(quality)]",
        "episode_format": "%(series_name)/S%(season:02d)/%(episode_name) S%(season:02d)E%(episode:02d) [%(quality)]",
        "song_format": "%(album)/%(track_number:02d). %(title)",
        "tag_format": "[VibraVid]",
    },
    "DOWNLOAD": {
        "skip_download": False,
        "auto_select": True,
        "use_curl_cffi_segments": False,
        "delay_after_download": 0,
        "thread_count": 10,
        "max_speed_mbps": 0,
        "segment_delay_seconds": 0,
        "segment_delay_jitter_seconds": 0,
        "subtitle_resolve_workers": 4,
        "select_video": "best",
        "select_audio": "ita|eng",
        "select_subtitle": "ita|eng",
        "extract_embedded_cc": False,
        "live_max_empty_polls": 8,
        "max_token_refresh_rounds": 10,
        "token_refresh_backoff_seconds": 4.0,
        "token_refresh_stall_rounds": 3,
        "embed_poster": False,
        "write_sidecars": False,
        "cleanup_tmp_folder": True,
    },
    "CODEC": {
        "dv_auto": False,
    },
    "PROCESS": {
        "engine": "ffmpeg",
        "use_gpu": False,
        "param_video": ["-c:v", "libx265", "-crf", "28", "-preset", "medium"],
        "param_audio": ["-c:a", "libopus", "-b:a", "128k"],
        "param_song": [],
        "param_final": ["-c", "copy"],
        "audio_order": [],
        "subtitle_order": [],
        "subtitle_disposition_language": "it-it_forced",
        "merge_audio": True,
        "merge_subtitle": True,
        "force_subtitle": "auto",
        "mux_dtsx": False,
        "extension": "mkv",
    },
    "REQUESTS": {
        "timeout": 15,
        "max_retry": 4,
        "verify": True,
        "use_proxy": False,
        "proxy_scope": "scrap+down",
        "proxy": {
            "http": "http://localhost:8888",
            "https": "http://localhost:8888",
        },
        "bypasser_url": "http://localhost:8192",
    },
    "DRM": {
        "use_cdm": True,
        "prefer_remote_cdm": False,
        "bypass_vault_cache": False,
        "log_engine_output": False,
        "vault": {
            "default": {
                "url": "https://drm-db.server66.workers.dev",
                "token": "",
            }
        },
    },
    "HOOKS": {
        "pre_run": [],
        "pre_download": [],
        "post_download": [],
        "post_run": [],
    },
    "ARR": {
        "enabled": False,
        "download_italian_anime_default": True,
        "anime_id_crosswalk_enabled": True,
        "provider_fallback": [
            "streamingcommunity", "animeunity", "discoveryplus", "discovery",
            "dmax", "nove", "realtime", "mediasetinfinity", "raiplay",
            "homegardentv", "foodnetwork", "animeworld", "crunchyroll",
            "primevideo", "tubitv",
        ],
        "path_mapping": {},
        "enable_polling": False,
        "enable_seerr_webhook": False,
        "enable_sonarr_webhook": False,
        "enable_radarr_webhook": False,
        "polling_interval": 300,
        "full_resync_interval": 21600,
        "max_concurrent_downloads": 2,
        "webhook_priority_enabled": True,
        "native_webhook_priority_window_seconds": 120,
        "seerr_fallback_delay_seconds": 20,
        "sonarr": {"url": "", "api_key": ""},
        "radarr": {"url": "", "api_key": ""},
        "seerr": {"webhook_secret": ""},
        "sonarr_webhook": {"webhook_secret": ""},
        "radarr_webhook": {"webhook_secret": ""},
    },
    "TELEGRAM": {
        "enabled": False,
        "gui_url": "http://vibravid:8000",
        "max_results": 8,
    },
}

DEFAULT_LOGIN = {
    "crunchyroll": {"cookies": "", "device_id": "", "cdm": ""},
    "tubi": {"email": "", "password": "", "cdm": ""},
    "mediasetinfinity": {"rtilogin_acd": "", "cdm": ""},
    "discoveryplus": {"st": "", "cdm": ""},
    "hbomax": {"st": "", "cdm": ""},
    "primevideo": {"cookies": "", "cdm": ""},
    "amazon_music": {"cookies": "", "cdm": ""},
    "appletv": {"cookies": "", "cdm": ""},
    "paramountplus": {"st": "", "cdm": ""},
    "disney": {"token": "", "refresh_token": "", "cdm": ""},
    "nowtvit": {"skyCEsidmesso01": "", "cdm": ""},
    "rakutentv": {"email": "", "password": "", "country": "", "cdm": ""},
    "chili": {"email": "", "password": "", "country": "", "cdm": ""},
    "cs_chrome": {"device_type": "chrome", "netflixid": "", "securenetflixid": "", "cdm": ""},
    "timvision": {"cookies": "", "cdm": ""},
}

DEFAULT_CONFIG_TOML = """# =============================================================================
# VibraVid - Main Configuration (config.toml)
# =============================================================================
# Each section is enclosed in square brackets [SECTION_NAME].
# Lines starting with # are comments and are ignored.
# Edit only the values, not the key names.
# =============================================================================

# ---------------------------------------------------------------
# [DEFAULT] - General application settings
# ---------------------------------------------------------------
# Basic options that affect the whole program.
[DEFAULT]
# If true, saves detailed trace of downloaded JSON files (developer debug)
debug_track_json = false
# Log level: DEBUG, INFO, WARNING, ERROR (lower = more details)
log_level = "INFO"
# If true, closes the console automatically at the end
close_console = true
# If true, shows messages on screen during execution
show_message = true
# If true, downloads the updated list of supported site domains online
fetch_domain_online = true
# If true, disables the scraper cache (forces re-run every time)
disable_scraper_cache = false
# List of service sources to import (e.g. ["default"] or ["default", 'C:\\more_service']).
# Use the full word "default"; Windows paths can use literal single quotes.
imp_service = ["default"]
# Installation path (empty = automatic)
installation = ""
# Metadata providers in order of preference (tmdb, imdb, tvdb) separated by comma
metadata_provider = "tmdb,imdb"

# ---------------------------------------------------------------
# [OUTPUT] - Where and how to save downloaded files
# ---------------------------------------------------------------
# Here you define destination folders and file names.
[OUTPUT]
# Main folder where videos end up
root_path = "Video"
# Folder name for movies
movie_folder_name = "Movie"
# Folder name for TV series
serie_folder_name = "Serie"
# Folder name for anime
anime_folder_name = "Anime"
# Folder name for music
music_folder_name = "Music"
# Folder name for live channels
live_folder_name = "Live"
# Format for movie folder/file name (tokens: title_name, title_year, quality)
movie_format = "%(title_name) (%(title_year))/%(title_name) (%(title_year)) [%(quality)]"
# Format for TV series episodes
episode_format = "%(series_name)/S%(season:02d)/%(episode_name) S%(season:02d)E%(episode:02d) [%(quality)]"
# Format for music tracks
song_format = "%(album)/%(track_number:02d). %(title)"
# Tag to write into files (e.g. [VibraVid])
tag_format = "[VibraVid]"

# ---------------------------------------------------------------
# [DOWNLOAD] - Download behavior
# ---------------------------------------------------------------
# Options that control how content is downloaded.
[DOWNLOAD]
# If true, skips download (preview/metadata only)
skip_download = false
# If true, automatically selects the best available quality
auto_select = true
# If true, uses curl_cffi to download video segments (faster on some sites)
use_curl_cffi_segments = false
# Seconds to wait after each download before the next one
delay_after_download = 0
# Number of simultaneous downloads (recommended: 4-10)
thread_count = 10
# Speed limit in MB/s (0 = unlimited; 10 Mbps is about 1.25 MB/s)
max_speed_mbps = 0
# Seconds to wait between segments of a file
segment_delay_seconds = 0
# Random variation on segment delay (anti-block)
segment_delay_jitter_seconds = 0
# Number of parallel workers to resolve subtitles
subtitle_resolve_workers = 4
# Preferred video quality: "best", "worst" or a number
select_video = "best"
# Preferred audio languages separated by | (e.g. "ita|eng")
select_audio = "ita|eng"
# Preferred subtitle languages separated by | (e.g. "ita|eng")
select_subtitle = "ita|eng"
# If true, extracts closed captions embedded in the video
extract_embedded_cc = false
# Maximum empty polls for live streams before giving up
live_max_empty_polls = 8
# Maximum token refresh rounds before giving up
max_token_refresh_rounds = 10
# Seconds to wait between token refresh attempts
token_refresh_backoff_seconds = 4.0
# Stalled rounds before considering refresh failed
token_refresh_stall_rounds = 3
# If true, embeds the poster inside the video file
embed_poster = false
# If true, writes sidecar files (nfo, poster) next to the video for Jellyfin/Emby/Kodi
write_sidecars = false
# If true, deletes the temporary folder after download
cleanup_tmp_folder = true

# ---------------------------------------------------------------
# [CODEC] - Video codec settings
# ---------------------------------------------------------------
[CODEC]
# If true, automatically detects Dolby Vision and handles the compatible layer
dv_auto = false

# ---------------------------------------------------------------
# [PROCESS] - Video/audio processing with ffmpeg
# ---------------------------------------------------------------
# Here you configure how ffmpeg processes files after download.
[PROCESS]
# Processing engine: "ffmpeg" (recommended)
engine = "ffmpeg"
# If true, uses GPU for processing (requires ffmpeg with hardware support)
use_gpu = false
# ffmpeg parameters for video (list of arguments)
param_video = [
    "-c:v",
    "libx265",
    "-crf",
    "28",
    "-preset",
    "medium",
]
# ffmpeg parameters for audio
param_audio = [
    "-c:a",
    "libopus",
    "-b:a",
    "128k",
]
# ffmpeg parameters for music tracks (empty = default)
param_song = []
# ffmpeg final parameters applied at the end
param_final = [
    "-c",
    "copy",
]
# Preferred audio track order (empty = automatic)
audio_order = []
# Preferred subtitle track order (empty = automatic)
subtitle_order = []
# Forced subtitle language/disposition
subtitle_disposition_language = "it-it_forced"
# If true, merges all audio tracks into a single file
merge_audio = true
# If true, merges all subtitles into a single file
merge_subtitle = true
# Force subtitles: "auto", "on", "off"
force_subtitle = "auto"
# If true, muxes DTS:X tracks
mux_dtsx = false
# Output file extension (e.g. mkv, mp4)
extension = "mkv"

# ---------------------------------------------------------------
# [REQUESTS] - Network and HTTP requests
# ---------------------------------------------------------------
# Timeouts, retries and proxy configuration for internet connections.
[REQUESTS]
# Timeout in seconds for each HTTP request
timeout = 15
# Maximum number of retries on error
max_retry = 4
# If true, verifies SSL certificates (recommended: true)
verify = true
# If true, uses a proxy for requests
use_proxy = false
# Proxy scope: "scrap+down" (scraping + download) or only part of it
proxy_scope = "scrap+down"
# Local bypasser URL (e.g. for anti-bot protection sites)
bypasser_url = "http://localhost:8192"

# Sub-section: direct proxy configuration
[REQUESTS.proxy]
http = "http://localhost:8888"
https = "http://localhost:8888"

# ---------------------------------------------------------------
# [DRM] - DRM management (Widevine, etc.)
# ---------------------------------------------------------------
# Options for downloading DRM-protected content.
[DRM]
# If true, uses the CDM module to decrypt content
use_cdm = true
# If true, prefers remote CDM over local CDM
prefer_remote_cdm = false
# If true, skips vault key lookup and forces a fresh CDM request.
# prefer_remote_cdm determines whether that request uses the remote or local CDM.
bypass_vault_cache = false
# If true, shows the DRM engine output in logs
log_engine_output = false

# Sub-section: DRM vault (key database)
[DRM.vault.default]
url = "https://drm-db.server66.workers.dev"
token = ""

# ---------------------------------------------------------------
# [HOOKS] - Custom scripts
# ---------------------------------------------------------------
# Lists of commands to execute at specific moments during execution.
# Leave lists empty [] if you do not want to use hooks.
[HOOKS]
# Executed before program startup
pre_run = []
# Executed before each download
pre_download = []
# Executed after each download
post_download = []
# Executed after program ends
post_run = []

# ---------------------------------------------------------------
# [ARR] - Integration with Sonarr / Radarr / Seerr
# ---------------------------------------------------------------
# Automation services to manage downloads and media cataloging.
# If you do not use Sonarr/Radarr/Seerr, leave enabled = false.
[ARR]
# If true, enables integration with ARR services
enabled = false
# If true, downloads Italian anime by default
download_italian_anime_default = true
# If true, enables anime ID crosswalk between providers
anime_id_crosswalk_enabled = true
# List of fallback providers for anime (in priority order)
provider_fallback = [
    "streamingcommunity",
    "animeunity",
    "discoveryplus",
    "discovery",
    "dmax",
    "nove",
    "realtime",
    "mediasetinfinity",
    "raiplay",
    "homegardentv",
    "foodnetwork",
    "animeworld",
    "crunchyroll",
    "primevideo",
    "tubitv",
]
# If true, enables periodic polling to check for new episodes
enable_polling = false
# If true, enables Seerr webhooks
enable_seerr_webhook = false
# If true, enables Sonarr webhooks
enable_sonarr_webhook = false
# If true, enables Radarr webhooks
enable_radarr_webhook = false
# Interval between polling checks in seconds (300 = 5 minutes)
polling_interval = 300
# Full resync interval in seconds (21600 = 6 hours)
full_resync_interval = 21600
# Maximum simultaneous downloads via ARR
max_concurrent_downloads = 2
# If true, enables webhook priority
webhook_priority_enabled = true
# Native webhook priority window in seconds
native_webhook_priority_window_seconds = 120
# Seerr fallback delay in seconds
seerr_fallback_delay_seconds = 20

# ARR path mapping (empty = no mapping)
[ARR.path_mapping]

# Sonarr configuration
[ARR.sonarr]
url = ""
api_key = ""

# Radarr configuration
[ARR.radarr]
url = ""
api_key = ""

# Seerr configuration
[ARR.seerr]
webhook_secret = ""

# Sonarr webhook configuration
[ARR.sonarr_webhook]
webhook_secret = ""

# Radarr webhook configuration
[ARR.radarr_webhook]
webhook_secret = ""

# ---------------------------------------------------------------
# [TELEGRAM] - Telegram notifications
# ---------------------------------------------------------------
[TELEGRAM]
# If true, enables Telegram notifications
enabled = false
# GUI URL for links in notifications
gui_url = "http://vibravid:8000"
# Maximum number of results shown in Telegram searches
max_results = 8

# =============================================================================
# END OF CONFIGURATION
# =============================================================================
"""

DEFAULT_LOGIN_TOML = """# =============================================================================
# QUICK LOGIN GUIDE
#
# CookieInspector: https://github.com/AstraeLabs/Extension/
# For email/password services, fill in only those fields.
# For individual cookies, follow the names listed in each service section.
# If a service requires the complete JSON, replace [paste here] with the
# unmodified CookieInspector export, keeping the triple single quotes:
# cookies = '''{"cookie_name":"cookie_value"}'''
# Optionally set cdm to your PRD/WVD key name; if empty, the default is used.
# Leave unused services empty. Do not share this file; it contains credentials.
# =============================================================================

# ---------------------------------------------------------------
# AMAZON_MUSIC
# ---------------------------------------------------------------
[amazon_music]
cookies = '''[paste here]'''
cdm = ""

# ---------------------------------------------------------------
# APPLETV
# ---------------------------------------------------------------
[appletv]
cookies = '''[paste here]'''
cdm = ""

# ---------------------------------------------------------------
# CHILI
# ---------------------------------------------------------------
[chili]
email = ""
password = ""
country = ""
cdm = ""

# ---------------------------------------------------------------
# CRUNCHYROLL
# ---------------------------------------------------------------
[crunchyroll]
cookies = '''[paste here]'''
device_id = ""
cdm = ""

# ---------------------------------------------------------------
# CS_CHROME
# ---------------------------------------------------------------
[cs_chrome]
device_type = "chrome"
netflixid = ""
securenetflixid = ""
cdm = ""

# ---------------------------------------------------------------
# DISCOVERYPLUS
# ---------------------------------------------------------------
[discoveryplus]
st = ""
cdm = ""

# ---------------------------------------------------------------
# DISNEY
# ---------------------------------------------------------------
[disney]
token = ""
refresh_token = ""
cdm = ""

# ---------------------------------------------------------------
# HBOMAX
# ---------------------------------------------------------------
[hbomax]
st = ""
cdm = ""

# ---------------------------------------------------------------
# MEDIASETINFINITY
# ---------------------------------------------------------------
[mediasetinfinity]
rtilogin_acd = ""
cdm = ""

# ---------------------------------------------------------------
# NOWTVIT
# ---------------------------------------------------------------
[nowtvit]
skyCEsidmesso01 = ""
cdm = ""

# ---------------------------------------------------------------
# PARAMOUNTPLUS
# ---------------------------------------------------------------
[paramountplus]
st = ""
cdm = ""

# ---------------------------------------------------------------
# PRIMEVIDEO
# ---------------------------------------------------------------
[primevideo]
cookies = '''[paste here]'''
cdm = ""

# ---------------------------------------------------------------
# RAKUTENTV
# ---------------------------------------------------------------
[rakutentv]
email = ""
password = ""
country = ""
cdm = ""

# ---------------------------------------------------------------
# TIMVISION
# ---------------------------------------------------------------
[timvision]
cookies = '''[paste here]'''
cdm = ""

# ---------------------------------------------------------------
# TUBI
# ---------------------------------------------------------------
[tubi]
email = ""
password = ""
cdm = ""

# =============================================================================
# END OF CREDENTIALS
# =============================================================================
"""

_MISSING = object()


class ConfigAccessor:
    def __init__(
        self, config_dict: dict, cache: dict, cache_prefix: str, cache_enabled: bool = True, repair_callback=None
    ):
        self._config_dict = config_dict
        self._cache = cache
        self._cache_prefix = cache_prefix
        self._cache_enabled = cache_enabled
        self._repair_callback = repair_callback
        self._repair_attempted = False
        self.honors_scope = False

    def _scoped_section(self, section: str):
        """The forwarded section for this thread's remote request, or ``None`` when the stored config applies."""
        if not self.honors_scope:
            return None
        from VibraVid.utils.scope import PASSTHROUGH_SECTIONS, current_overlay, note_wanted

        overlay = current_overlay()
        if overlay is None or section in PASSTHROUGH_SECTIONS:
            return None
        if section not in overlay:
            note_wanted(section)  # never fall back to the server's own account: act as if the section did not exist
            return {}
        return overlay[section]

    def _normalize_login_section(self, section_data: dict | None) -> dict:
        """Decode JSON cookie/token fields into the mapping consumed by service clients."""
        if self._cache_prefix != "login" or not isinstance(section_data, dict):
            return section_data or {}

        normalized = dict(section_data)
        cookie_data = normalized.pop("cookies", _MISSING)
        if isinstance(cookie_data, str):
            if not cookie_data.strip() or cookie_data.strip() == "[paste here]":
                cookie_data = _MISSING
            else:
                try:
                    cookie_data = json.loads(cookie_data)
                except json.JSONDecodeError as exc:
                    raise ValueError("login cookies must contain valid JSON") from exc

        if cookie_data is not _MISSING:
            if isinstance(cookie_data, dict):
                if isinstance(cookie_data.get("cookies"), list):
                    cookie_data = cookie_data["cookies"]
                else:
                    for name, value in cookie_data.items():
                        name = str(name)
                        if normalized.get(name) in (None, ""):
                            normalized[name] = value
                    cookie_data = None

            if isinstance(cookie_data, list):
                for cookie in cookie_data:
                    if not isinstance(cookie, dict):
                        raise ValueError("login cookie arrays must contain objects with name and value")
                    if "name" in cookie or "value" in cookie:
                        if "name" not in cookie or "value" not in cookie:
                            raise ValueError("login cookie arrays must contain objects with name and value")
                        cookie_items = ((cookie["name"], cookie["value"]),)
                    else:
                        cookie_items = cookie.items()

                    for name, value in cookie_items:
                        name = str(name)
                        if normalized.get(name) in (None, ""):
                            normalized[name] = value
            elif cookie_data is not None:
                raise ValueError("login cookies must be a JSON object or an array of cookie objects")

        token_data = normalized.get("rtilogin_acd")
        if isinstance(token_data, str) and token_data.lstrip().startswith(("{", "[")):
            try:
                token_data = json.loads(token_data)
            except json.JSONDecodeError as exc:
                raise ValueError("login rtilogin_acd must contain valid JSON") from exc
            if isinstance(token_data, list):
                token_data = next(
                    (
                        entry["rtilogin_acd"]
                        for entry in token_data
                        if isinstance(entry, dict) and "rtilogin_acd" in entry
                    ),
                    None,
                )
                if isinstance(token_data, str):
                    try:
                        token_data = json.loads(token_data)
                    except json.JSONDecodeError as exc:
                        raise ValueError("login rtilogin_acd must contain valid JSON") from exc
            elif isinstance(token_data, dict) and "rtilogin_acd" in token_data and "caToken" not in token_data:
                token_data = token_data["rtilogin_acd"]
                if isinstance(token_data, str):
                    try:
                        token_data = json.loads(token_data)
                    except json.JSONDecodeError as exc:
                        raise ValueError("login rtilogin_acd must contain valid JSON") from exc

            if not isinstance(token_data, dict):
                raise ValueError("login rtilogin_acd JSON must contain an object")
            normalized["rtilogin_acd"] = token_data
            admin_token = token_data.get("adminBeToken")
            if admin_token and normalized.get("adminBeToken") in (None, ""):
                normalized["adminBeToken"] = admin_token

        token_data = normalized.get("token")
        if isinstance(token_data, str) and token_data.lstrip().startswith("{"):
            try:
                token_data = json.loads(token_data)
            except json.JSONDecodeError as exc:
                raise ValueError("login token must contain valid JSON") from exc
            if not isinstance(token_data, dict):
                raise ValueError("login token JSON must be an object")
            access_token = token_data.get("access_token") or token_data.get("token")
            if access_token:
                normalized["token"] = access_token
            if access_token and normalized.get("access_token") in (None, ""):
                normalized["access_token"] = access_token
            refresh_token = token_data.get("refresh_token")
            if refresh_token and normalized.get("refresh_token") in (None, ""):
                normalized["refresh_token"] = refresh_token

        return normalized

    def get(self, section: str, key: str, data_type: type = str, default: Any = _MISSING) -> Any:
        """
        Read a value from the configuration with caching.

        Args:
            section (str): Section in the configuration
            key (str): Key to read
            data_type (type, optional): Expected data type. Default: str
            default (Any, optional): Value returned when the key is missing.
                If omitted, a ValueError is raised. Explicit ``default=None``
                returns ``None`` (a sentinel is used internally to tell
                "not supplied" from "supplied as None").

        Returns:
            Any: The key value converted to the specified data type, or default if not found
        """
        cache_key = f"{self._cache_prefix}.{section}.{key}"

        scoped = self._scoped_section(section)
        if scoped is not None:  # never through the shared cache: it would hand one client's value to the next
            scoped = self._normalize_login_section(scoped)
            if key in scoped:
                return self._convert_to_data_type(scoped[key], data_type)
            if default is not _MISSING:
                return default
            raise ValueError(f"Key '{key}' not found in section '{section}' of {self._cache_prefix} configuration")

        # Check if the value is in the cache
        if self._cache_enabled and cache_key in self._cache:
            return self._cache[cache_key]

        section_data = self._normalize_login_section(self._config_dict.get(section))

        # Check if the section and key exist
        if section not in self._config_dict:
            if default is not _MISSING:
                logger.info(f"Section '{section}' not found in {self._cache_prefix} configuration, returning default.")
                return default

            # Attempt repair only once per session to avoid repeated network calls
            if self._repair_callback and not self._repair_attempted:
                self._repair_attempted = True
                logger.info(f"Section '{section}' missing — attempting one-time config repair.")
                if self._repair_callback():
                    # Reset the flag: repair succeeded and saved a new config,
                    # so future misses may be legitimate and warrant another attempt.
                    self._repair_attempted = False
                    section_data = self._normalize_login_section(self._config_dict.get(section))
                    if section in self._config_dict and key in section_data:
                        value = section_data[key]
                        converted_value = self._convert_to_data_type(value, data_type)
                        if self._cache_enabled:
                            self._cache[cache_key] = converted_value
                        return converted_value

            raise ValueError(f"Section '{section}' not found in {self._cache_prefix} configuration")

        if key not in section_data:
            if default is not _MISSING:
                logger.debug(f"Key '{key}' not found in section '{section}' of {self._cache_prefix} configuration, returning default.")
                return default

            # Same one-time repair guard as above
            if self._repair_callback and not self._repair_attempted:
                self._repair_attempted = True
                logger.info(f"Key '{key}' in section '{section}' missing — attempting one-time config repair.")
                if self._repair_callback():
                    self._repair_attempted = False
                    section_data = self._normalize_login_section(self._config_dict.get(section))
                    if key in section_data:
                        value = section_data[key]
                        converted_value = self._convert_to_data_type(value, data_type)
                        if self._cache_enabled:
                            self._cache[cache_key] = converted_value
                        return converted_value

            raise ValueError(f"Key '{key}' not found in section '{section}' of {self._cache_prefix} configuration")

        # Get and convert the value
        value = section_data[key]
        converted_value = self._convert_to_data_type(value, data_type)

        # Save in cache
        if self._cache_enabled:
            self._cache[cache_key] = converted_value

        return converted_value

    def _convert_to_data_type(self, value: Any, data_type: type) -> Any:
        """
        Convert the value to the specified data type.

        Args:
            value (Any): Value to convert
            data_type (type): Target data type

        Returns:
            Any: Converted value
        """
        try:
            if data_type is int:
                return int(value)

            elif data_type is float:
                return float(value)

            elif data_type is bool:
                if isinstance(value, str):
                    return value.lower() in ("yes", "true", "t", "1")
                return bool(value)

            elif data_type is list:
                if isinstance(value, list):
                    return value
                if isinstance(value, str):
                    return [item.strip() for item in value.split(",")]
                return [value]

            elif data_type is dict:
                if isinstance(value, dict):
                    return value

                raise ValueError(f"Cannot convert {type(value).__name__} to dict")
            else:
                return value

        except Exception as e:
            error_msg = f"Error converting: {data_type.__name__} to value '{value}' with error: {e}"
            console.print(f"[red]{error_msg}")
            raise ValueError(f"Error converting: {data_type.__name__} to value '{value}' with error: {e}") from e

    def get_int(self, section: str, key: str, default: Any = _MISSING) -> int:
        """Read an integer from the configuration."""
        return self.get(section, key, int, default=default)

    def get_float(self, section: str, key: str, default: Any = _MISSING) -> float:
        """Read a float from the configuration."""
        return self.get(section, key, float, default=default)

    def get_bool(self, section: str, key: str, default: Any = _MISSING) -> bool:
        """Read a boolean from the configuration."""
        return self.get(section, key, bool, default=default)

    def get_list(self, section: str, key: str, default: Any = _MISSING) -> list[str]:
        """Read a list from the configuration."""
        return self.get(section, key, list, default=default)

    def get_dict(self, section: str, key: str, default: Any = _MISSING) -> dict:
        """Read a dictionary from the configuration."""
        return self.get(section, key, dict, default=default)

    def get_section(self, section: str, default: dict | None = None) -> dict:
        """Return the entire section as a dict (e.g. all cookies/keys for a service's login block), uncached and un-typed."""
        scoped = self._scoped_section(section)
        if scoped is not None:
            return dict(self._normalize_login_section(scoped))
        return dict(self._normalize_login_section(self._config_dict.get(section) or (default or {})))

    def has_section(self, section: str) -> bool:
        """True if the section exists, even when empty (get_section cannot tell an empty section from a missing one)."""
        scoped = self._scoped_section(section)
        if scoped is not None:
            return bool(scoped)
        return isinstance(self._config_dict.get(section), dict)

    def set_key(self, section: str, key: str, value: Any) -> None:
        """
        Set a key in the configuration and update cache.

        Args:
            section (str): Section in the configuration
            key (str): Key to set
            value (Any): Value to associate with the key
        """
        scoped = self._scoped_section(section)
        if scoped is not None:  # a remote request: the new value belongs to the client, never to the server's login.toml
            from VibraVid.utils.scope import current_overlay, mark_changed

            current_overlay().setdefault(section, scoped)[key] = value
            mark_changed(section)
            return

        logger.info(f"Setting config: section='{section}', key='{key}', value='<hidden>'")
        try:
            if section not in self._config_dict:
                self._config_dict[section] = {}

            self._config_dict[section][key] = value

            # Update the cache
            cache_key = f"{self._cache_prefix}.{section}.{key}"
            self._cache[cache_key] = value

        except Exception as e:
            error_msg = f"Error setting key '{key}' in section '{section}' of {self._cache_prefix} configuration: {e}"
            console.print(f"[red]{error_msg}")

    def reset_repair_flag(self) -> None:
        """Reset the repair-attempted flag (called after a successful reload)."""
        self._repair_attempted = False


class ConfigManager:
    def __init__(self) -> None:
        """Initialize the ConfigManager with caching."""
        self.base_path = None

        # Strategy 0: Environment variable override
        env_base_path = os.environ.get("VIBRAVID_BASE_PATH")
        if env_base_path:
            self.base_path = env_base_path
            logger.info("Base path set from environment variable VIBRAVID_BASE_PATH: " + self.base_path)
        # Strategy 1: PyInstaller binary
        elif getattr(sys, "frozen", False):
            self.base_path = os.path.dirname(sys.executable)
            logger.info("Running in PyInstaller binary mode, base path set: " + self.base_path)
        else:
            # Strategy 2: Try to find Conf in source directory (development mode)
            package_base = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
            package_conf = os.path.join(package_base, "Conf")
            logger.info(f"Checking for Conf directory in package base: {package_conf}")

            if os.path.exists(package_conf):
                self.base_path = package_base
            else:
                self.base_path = os.getcwd()
                logger.info(f"Conf directory not found in package, using current working directory as base path: {self.base_path}")

        # Initialize conf directory path
        self.conf_path = os.path.join(self.base_path, "Conf")

        # Create conf directory if it doesn't exist
        if not os.path.exists(self.conf_path):
            logger.info(f"Conf directory not found at {self.conf_path}, creating it.")
            os.makedirs(self.conf_path, exist_ok=True)

        # Initialize file paths using conf directory
        self.config_file_path = os.path.join(self.conf_path, CONFIG_FILENAME)
        self.login_file_path = os.path.join(self.conf_path, LOGIN_FILENAME)
        self.domains_path = os.path.join(self.conf_path, DOMAINS_FILENAME)
        self.github_domains_path = os.path.join(self.base_path, GITHUB_DOMAINS_PATH)
        logger.debug(f"Config file path: {self.config_file_path}")
        logger.debug(f"Login file path: {self.login_file_path}")
        logger.debug(f"Domains file path: {self.domains_path}")

        # Initialize data structures
        self._config_data = {}
        self._login_data = {}
        self._domains_data = {}

        # Enhanced caching system
        self.cache: dict[str, Any] = {}
        self._cache_enabled = True

        # Create accessors — repair_callback only on config, not on login/domain
        self.config = ConfigAccessor(
            self._config_data,
            self.cache,
            "config",
            self._cache_enabled,
            repair_callback=self._repair_missing_config_keys,
        )
        self.login = ConfigAccessor(self._login_data, self.cache, "login", self._cache_enabled)
        self.login.honors_scope = True
        self.domain = ConfigAccessor(self._domains_data, self.cache, "domain", self._cache_enabled)

        # Load the configuration
        self.fetch_domain_online = True
        self.load_all_configs()

    def load_all_configs(self) -> None:
        """Load all configuration files."""
        self._load_config()
        self._load_login()
        self._update_settings_from_config()
        self._load_site_data()
        self._start_domain_refresh_loop()

    def _load_config(self) -> None:
        """Load the main configuration file."""
        if not os.path.exists(self.config_file_path):
            logger.info("Configuration file not found, creating default config.toml")
            self._write_default_config()
        else:
            # Try to download reference if local file exists but we want to check remote too
            pass

        try:
            with open(self.config_file_path, "rb") as f:
                config_data = tomllib.load(f)

        except tomllib.TOMLDecodeError as e:
            message = f"Error parsing config TOML; original file was not changed: {e}"
            console.print(f"[red]{message}")
            logger.error(message)
            raise ValueError(message) from e

        except Exception as e:
            message = f"Error loading configuration; original file was not changed: {e}"
            console.print(f"[red]{message}")
            logger.exception(message)
            raise

        self._config_data.clear()
        self._config_data.update(config_data)

        env_root = os.environ.get("VIBRAVID_OUTPUT_ROOT")
        if env_root:
            self._config_data.setdefault("OUTPUT", {})["root_path"] = env_root
            logger.info(f"OUTPUT.root_path overridden via VIBRAVID_OUTPUT_ROOT={env_root}")

        self._apply_termux_defaults()
        self._precache_config_values()

    def _apply_termux_defaults(self) -> None:
        """Apply Termux/Android-specific configuration defaults when running on Termux."""
        is_termux = "TERMUX_VERSION" in os.environ or os.path.exists("/data/data/com.termux/files/usr/bin")
        if not is_termux:
            return

        root_path = self._config_data.get("OUTPUT", {}).get("root_path", "Video")
        if root_path == "Video" or not root_path.startswith("/"):
            self._config_data.setdefault("OUTPUT", {})["root_path"] = "/sdcard/Movies/VibraVid"
            logger.info("OUTPUT.root_path defaulted to /sdcard/Movies/VibraVid for Termux compatibility")

    def _load_login(self) -> None:
        """Load the login configuration file."""
        if not os.path.exists(self.login_file_path):
            logger.info("Login file not found, creating default login.toml")
            self._write_default_login()

        try:
            with open(self.login_file_path, "rb") as f:
                login_data = tomllib.load(f)
            self._login_data.clear()
            self._login_data.update(login_data)

        except tomllib.TOMLDecodeError as e:
            message = f"Error parsing login TOML; original file was not changed: {e}"
            console.print(f"[red]{message}")
            logger.error(message)
            raise ValueError(message) from e

        except Exception as e:
            message = f"Error loading login configuration; original file was not changed: {e}"
            console.print(f"[red]{message}")
            logger.exception(message)
            raise

    def _write_default_config(self) -> None:
        """Write the default configuration to config.toml (with fixed comments)."""
        try:
            with open(self.config_file_path, "w", encoding="utf-8") as f:
                f.write(DEFAULT_CONFIG_TOML)
            logger.info(f"Default configuration written to {self.config_file_path}")
        except Exception as e:
            console.print(f"[red]Error writing default configuration: {e}")

    def _write_default_login(self) -> None:
        """Write the default login configuration to login.toml (with fixed comments)."""
        try:
            with open(self.login_file_path, "w", encoding="utf-8") as f:
                f.write(DEFAULT_LOGIN_TOML)
            logger.info(f"Default login configuration written to {self.login_file_path}")
        except Exception as e:
            console.print(f"[red]Error writing default login configuration: {e}")

    def _precache_config_values(self) -> None:
        """Pre-cache commonly used configuration values."""
        common_keys = [
            ("DOWNLOAD", "thread_count", int),
            ("DOWNLOAD", "cleanup_tmp_folder", bool),
            ("PROCESS", "use_gpu", bool),
            ("PROCESS", "param_video", str),
            ("PROCESS", "param_audio", str),
            ("PROCESS", "param_final", str),
            ("REQUESTS", "timeout", int),
            ("REQUESTS", "max_retry", int),
            ("REQUESTS", "use_proxy", bool),
            ("REQUESTS", "proxy", dict),
        ]

        for section, key, data_type in common_keys:
            try:
                cache_key = f"config.{section}.{key}"
                if section in self._config_data and key in self._config_data[section]:
                    value = self._config_data[section][key]
                    converted_value = self.config._convert_to_data_type(value, data_type)
                    self.cache[cache_key] = converted_value

            except Exception as e:
                logger.error(f"Failed to precache {section}.{key}: {e}")

    def _handle_config_error(self) -> None:
        """Handle configuration errors by downloading the reference version."""
        console.print("[yellow]Attempting to retrieve reference configuration...")
        self._download_file(CONFIG_DOWNLOAD_URL, self.config_file_path)
        logger.info("Reference configuration downloaded successfully, attempting to load again")

        try:
            with open(self.config_file_path, "rb") as f:
                self._config_data.clear()
                self._config_data.update(tomllib.load(f))

            self._apply_termux_defaults()
            self._precache_config_values()
            self._update_settings_from_config()
            console.print("[green]Reference configuration loaded successfully")

        except Exception as e:
            console.print(f"[red]Critical configuration error: {str(e)}")
            console.print("[red]Unable to proceed. The application will terminate.")
            sys.exit(1)

    def _repair_missing_config_keys(self) -> bool:
        """Download the remote config.toml and merge only missing sections/keys into the current config, then persist to disk."""
        console.print("[yellow]Missing config key detected, downloading reference config to fill gaps...")
        logger.info("Attempting to repair missing config keys from remote reference")
        try:
            response = requests.get(CONFIG_DOWNLOAD_URL, headers={"User-Agent": "Mozilla/5.0"})
            if response.status_code != 200:
                console.print(f"[red]Could not download reference config: HTTP {response.status_code}")
                return False

            try:
                remote_config = tomllib.loads(response.text)
            except Exception:
                remote_config = response.json()
            changed = False

            for section, section_data in remote_config.items():
                if section not in self._config_data:
                    self._config_data[section] = section_data
                    changed = True
                    console.print(f"[yellow]Added missing section: {section}")
                    logger.info(f"Added missing section: {section}")

                elif isinstance(section_data, dict):
                    for key, value in section_data.items():
                        if key not in self._config_data[section]:
                            self._config_data[section][key] = value
                            changed = True
                            console.print(f"[yellow]Added missing key: [{section}] {key} = {value}")
                            logger.info(f"Added missing key: {section}.{key} = {value}")

            if changed:
                self.save_config()

                # Invalidate only config-prefixed entries so login/domain cache is untouched
                stale = [k for k in self.cache if k.startswith("config.")]
                for k in stale:
                    del self.cache[k]
                console.print("[green]Missing keys added and config.toml saved.")
            else:
                console.print("[yellow]No new keys found in reference config.")

            return changed

        except Exception as e:
            console.print(f"[red]Failed to repair config: {e}")
            logger.error(f"Config repair failed: {e}")
            return False

    def _update_settings_from_config(self) -> None:
        """Update internal settings from loaded configurations."""
        default_section = self._config_data.get("DEFAULT", {})
        self.fetch_domain_online = default_section.get("fetch_domain_online", True)

    def _download_file(self, url: str, file_path: str) -> None:
        """Download a file from a URL."""
        try:
            response = requests.get(url, headers={"User-Agent": "Mozilla/5.0"})

            if response.status_code == 200:
                with open(file_path, "wb") as f:
                    f.write(response.content)
            else:
                error_msg = f"HTTP Error: {response.status_code}, Response: {response.text[:100]}"
                console.print(f"[red]Download failed: {error_msg}")
                raise Exception(error_msg)

        except Exception as e:
            console.print(f"[red]Download error: {str(e)} for url: {url}")
            raise

    def _load_site_data(self) -> None:
        """Load site data based on fetch_domain_online setting."""
        if self.fetch_domain_online:
            self._load_site_data_online()
        else:
            self._load_site_data_from_file()

    def _start_domain_refresh_loop(self) -> None:
        """Start a background thread that re-fetches domains.json every DOMAIN_REFRESH_INTERVAL_SECONDS."""
        if not self.fetch_domain_online:
            return

        thread = threading.Thread(
            target=self._domain_refresh_worker,
            name="domain-refresh",
            daemon=True,
        )
        thread.start()

    def _domain_refresh_worker(self) -> None:
        while True:
            time.sleep(DOMAIN_REFRESH_INTERVAL_SECONDS)
            try:
                data = _startup_prefetch._fetch_domains()
                self._domains_data.clear()
                self._domains_data.update(data)
                self._save_domains_to_appropriate_location()

                stale = [key for key in self.cache if key.startswith("domain.")]
                for key in stale:
                    del self.cache[key]
                logger.info("Domains refreshed in background (%d entries).", len(self._domains_data))
            except Exception as e:
                logger.warning(f"Background domain refresh failed: {e}")

    def _load_site_data_online(self) -> None:
        """Load site data from GitHub and update local domains.json file."""
        try:
            _startup_prefetch.start()
            data = _startup_prefetch.collect("domains", timeout=5)

            if data is not None:
                self._domains_data.clear()
                self._domains_data.update(data)
                self._save_domains_to_appropriate_location()

            else:
                console.print("[yellow]Cloudflare request failed, using local domains.json fallback")
                self._handle_site_data_fallback()

        except Exception as e:
            console.print(f"[yellow]Cloudflare request failed ({str(e)}), using local domains.json fallback")
            self._handle_site_data_fallback()

    def _save_domains_to_appropriate_location(self) -> None:
        """Save domains to the conf directory."""
        try:
            with open(self.domains_path, "w", encoding="utf-8") as f:
                json.dump(self._domains_data, f, indent=4, ensure_ascii=False)
        except Exception as save_error:
            console.print(f"[red]Could not save domains to file: {str(save_error)}")

    def _load_site_data_from_file(self) -> None:
        """Load site data from local domains.json file."""
        try:
            if os.path.exists(self.domains_path):
                with open(self.domains_path, encoding="utf-8") as f:
                    self._domains_data.clear()
                    self._domains_data.update(json.load(f))

                site_count = len(self._domains_data) if isinstance(self._domains_data, dict) else 0

            elif os.path.exists(self.github_domains_path):
                console.print(f"[dim]Fallback domain path: {self.github_domains_path}[/dim]")
                logger.info(f"Loading domains from GitHub structure: {self.github_domains_path}")

                with open(self.github_domains_path, encoding="utf-8") as f:
                    self._domains_data.clear()
                    self._domains_data.update(json.load(f))

                site_count = len(self._domains_data) if isinstance(self._domains_data, dict) else 0
                console.print(f"[green]Domains loaded from GitHub structure: {site_count} streaming services")

            else:
                console.print("[dim]Domain file not found locally, trying online fallback...")
                self._load_site_data_online()
                if self._domains_data:
                    return

                console.print("[dim]Domain path: Disabled[/dim]")
                self._domains_data.clear()

        except Exception as e:
            console.print(f"[red]Local domain file error: {str(e)}")
            self._domains_data.clear()

    def _handle_site_data_fallback(self) -> None:
        """Handle site data fallback in case of error."""
        if os.path.exists(self.domains_path):
            logger.info(f"Attempting fallback to local domains file: {self.domains_path}")

            try:
                with open(self.domains_path, encoding="utf-8") as f:
                    self._domains_data.clear()
                    self._domains_data.update(json.load(f))
                logger.info("Fallback to conf domains.json successful")
                return
            except Exception as fallback_error:
                console.print(f"[red]Conf domains fallback failed: {str(fallback_error)}")

        if os.path.exists(self.github_domains_path):
            logger.info(f"Attempting fallback to GitHub structure domains file: {self.github_domains_path}")
            try:
                with open(self.github_domains_path, encoding="utf-8") as f:
                    self._domains_data.clear()
                    self._domains_data.update(json.load(f))
                logger.info("Fallback to GitHub structure domains.json successful")
                return
            except Exception as fallback_error:
                console.print(f"[red]GitHub structure fallback failed: {str(fallback_error)}")

        console.print("[red]No local domains.json file available for fallback")
        self._domains_data.clear()

    def reload(self) -> None:
        """Reload all configuration files and clear cached values."""
        self.cache.clear()
        self.config.reset_repair_flag()
        self.load_all_configs()

    def reload_config_only(self) -> None:
        """Reload only config.toml and refresh related settings."""
        self.cache.clear()
        self.config.reset_repair_flag()
        self._load_config()
        self._update_settings_from_config()

    def reload_login_only(self) -> None:
        """Reload only login.toml."""
        self.cache.clear()
        self._load_login()

    def _apply_dict_to_toml(self, toml_doc, data):
        """Synchronize values from a Python dict into a tomlkit document, preserving comments."""
        for section, section_data in data.items():
            if section not in toml_doc:
                toml_doc[section] = tomlkit.table()
            section_toml = toml_doc[section]
            if isinstance(section_data, dict):
                for key, value in section_data.items():
                    if isinstance(value, dict):
                        if key not in section_toml:
                            section_toml[key] = tomlkit.table()
                        self._apply_dict_to_toml(section_toml[key], value)
                    else:
                        section_toml[key] = value

    def save_config(self) -> None:
        """Save the main configuration to file."""
        try:
            if os.path.exists(self.config_file_path):
                with open(self.config_file_path, encoding="utf-8") as f:
                    doc = tomlkit.parse(f.read())
                self._apply_dict_to_toml(doc, self._config_data)
                with open(self.config_file_path, "w", encoding="utf-8") as f:
                    tomlkit.dump(doc, f)
            else:
                with open(self.config_file_path, "w", encoding="utf-8") as f:
                    f.write(DEFAULT_CONFIG_TOML)
        except Exception as e:
            console.print(f"[red]Error saving configuration: {e}")

    def save_login(self) -> None:
        """Save the login configuration to file."""
        from VibraVid.utils.scope import current_overlay

        if current_overlay() is not None:
            return

        logger.info("Saving login configuration to file")
        try:
            if os.path.exists(self.login_file_path):
                with open(self.login_file_path, encoding="utf-8") as f:
                    doc = tomlkit.parse(f.read())
                self._apply_dict_to_toml(doc, self._login_data)
                with open(self.login_file_path, "w", encoding="utf-8") as f:
                    tomlkit.dump(doc, f)
            else:
                with open(self.login_file_path, "w", encoding="utf-8") as f:
                    f.write(DEFAULT_LOGIN_TOML)
            stale = [key for key in self.cache if key.startswith("login.")]
            for key in stale:
                del self.cache[key]
        except Exception as e:
            console.print(f"[red]Error saving login configuration: {e}")

# Initialize the ConfigManager when the module is imported
config_manager = ConfigManager()
