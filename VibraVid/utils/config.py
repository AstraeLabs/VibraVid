# 29.01.24

import json
import logging
import os
import re
import sys
import threading
import time
from typing import Any

from curl_cffi import requests
from rich.console import Console

from . import _startup_prefetch

console = Console()
logger = logging.getLogger(__name__)

CONFIG_FILENAME = "config.json"
LOGIN_FILENAME = "login.json"
DOMAINS_FILENAME = "domains.json"
GITHUB_DOMAINS_PATH = ".github/script/domains.json"

CONFIG_DOWNLOAD_URL = "https://raw.githubusercontent.com/AstraeLabs/VibraVid/refs/heads/main/Conf/config.json"
CONFIG_LOGIN_DOWNLOAD_URL = "https://raw.githubusercontent.com/AstraeLabs/VibraVid/refs/heads/main/Conf/login.json"
DOMAINS_DOWNLOAD_URL = "https://domains-tracker.server66.workers.dev"
DOMAIN_REFRESH_INTERVAL_SECONDS = 1800


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

        # Check if the value is in the cache
        if self._cache_enabled and cache_key in self._cache:
            return self._cache[cache_key]

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
                    if section in self._config_dict and key in self._config_dict.get(section, {}):
                        value = self._config_dict[section][key]
                        converted_value = self._convert_to_data_type(value, data_type)
                        if self._cache_enabled:
                            self._cache[cache_key] = converted_value
                        return converted_value

            raise ValueError(f"Section '{section}' not found in {self._cache_prefix} configuration")

        if key not in self._config_dict[section]:
            if default is not _MISSING:
                logger.debug(f"Key '{key}' not found in section '{section}' of {self._cache_prefix} configuration, returning default.")
                return default

            # Same one-time repair guard as above
            if self._repair_callback and not self._repair_attempted:
                self._repair_attempted = True
                logger.info(f"Key '{key}' in section '{section}' missing — attempting one-time config repair.")
                if self._repair_callback():
                    self._repair_attempted = False
                    if key in self._config_dict.get(section, {}):
                        value = self._config_dict[section][key]
                        converted_value = self._convert_to_data_type(value, data_type)
                        if self._cache_enabled:
                            self._cache[cache_key] = converted_value
                        return converted_value

            raise ValueError(f"Key '{key}' not found in section '{section}' of {self._cache_prefix} configuration")

        # Get and convert the value
        value = self._config_dict[section][key]
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
        return dict(self._config_dict.get(section) or (default or {}))

    def set_key(self, section: str, key: str, value: Any) -> None:
        """
        Set a key in the configuration and update cache.

        Args:
            section (str): Section in the configuration
            key (str): Key to set
            value (Any): Value to associate with the key
        """
        logger.info(f"Setting config: section='{section}', key='{key}', value='{value}'")
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


def save_config_compact(data, f):
    json_str = json.dumps(data, indent=4)
    json_str = re.sub(
        r'\[\s*\n\s*((?:"[^"]*"|\d+|true|false|null)(?:\s*,\s*(?:"[^"]*"|\d+|true|false|null))*\s*)\n\s*\]',
        lambda m: "[" + m.group(1).replace("\n", "").replace(" ", "") + "]",
        json_str,
        flags=re.MULTILINE | re.DOTALL,
    )
    f.write(json_str)


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
            logger.info("Configuration file not found, attempting to download from repository")
            self._download_file(CONFIG_DOWNLOAD_URL, self.config_file_path, "config.json")

        try:
            with open(self.config_file_path) as f:
                self._config_data.clear()
                self._config_data.update(json.load(f))

            # Environment variable overrides (highest priority)
            env_root = os.environ.get("VIBRAVID_OUTPUT_ROOT")
            if env_root:
                self._config_data.setdefault("OUTPUT", {})["root_path"] = env_root
                logger.info(f"OUTPUT.root_path overridden via VIBRAVID_OUTPUT_ROOT={env_root}")

            # Termux/Android: default output path to shared storage if not already absolute
            self._apply_termux_defaults()

            # Pre-cache commonly used configuration values
            self._precache_config_values()

        except json.JSONDecodeError as e:
            console.print(f"[red]Error parsing config JSON: {str(e)}")
            self._handle_config_error()

        except Exception as e:
            console.print(f"[red]Error loading configuration: {str(e)}")
            self._handle_config_error()

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
            logger.info("Login file not found, attempting to download from repository")

            try:
                self._download_file(CONFIG_LOGIN_DOWNLOAD_URL, self.login_file_path, "login.json")
            except Exception as e:
                console.print(f"[yellow]Could not download login.json: {str(e)}")
                console.print("[yellow]Creating empty login configuration...")
                self._login_data.clear()
                return

        try:
            with open(self.login_file_path) as f:
                self._login_data.clear()
                self._login_data.update(json.load(f))

        except json.JSONDecodeError as e:
            console.print(f"[red]Error parsing login JSON: {str(e)}")
            self._login_data.clear()

        except Exception as e:
            console.print(f"[red]Error loading login configuration: {str(e)}")
            self._login_data.clear()

    def _precache_config_values(self) -> None:
        """Pre-cache commonly used configuration values."""
        common_keys = [
            ("DOWNLOAD", "thread_count", int),
            ("DOWNLOAD", "concurrent_download", bool),
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
        self._download_file(CONFIG_DOWNLOAD_URL, self.config_file_path, "config.json")
        logger.info("Reference configuration downloaded successfully, attempting to load again")

        try:
            with open(self.config_file_path) as f:
                self._config_data.clear()
                self._config_data.update(json.load(f))

            self._apply_termux_defaults()
            self._precache_config_values()
            self._update_settings_from_config()
            console.print("[green]Reference configuration loaded successfully")

        except Exception as e:
            console.print(f"[red]Critical configuration error: {str(e)}")
            console.print("[red]Unable to proceed. The application will terminate.")
            sys.exit(1)

    def _repair_missing_config_keys(self) -> bool:
        """Download the remote config.json and merge only missing sections/keys into the current config, then persist to disk."""
        console.print("[yellow]Missing config key detected, downloading reference config to fill gaps...")
        logger.info("Attempting to repair missing config keys from remote reference")
        try:
            response = requests.get(CONFIG_DOWNLOAD_URL, headers={"User-Agent": "Mozilla/5.0"})
            if response.status_code != 200:
                console.print(f"[red]Could not download reference config: HTTP {response.status_code}")
                return False

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
                console.print("[green]Missing keys added and config.json saved.")
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

    def _download_file(self, url: str, file_path: str, file_name: str) -> None:
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
            logger.debug(f"Fetching site data from: {DOMAINS_DOWNLOAD_URL}")
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
        """Reload only config.json and refresh related settings."""
        self.cache.clear()
        self.config.reset_repair_flag()
        self._load_config()
        self._update_settings_from_config()

    def reload_login_only(self) -> None:
        """Reload only login.json."""
        self.cache.clear()
        self._load_login()

    def save_config(self) -> None:
        """Save the main configuration to file."""
        try:
            with open(self.config_file_path, "w") as f:
                save_config_compact(self._config_data, f)
        except Exception as e:
            console.print(f"[red]Error saving configuration: {e}")

    def save_login(self) -> None:
        """Save the login configuration to file."""
        logger.info("Saving login configuration to file")
        try:
            with open(self.login_file_path, "w") as f:
                json.dump(self._login_data, f, indent=4)
            stale = [key for key in self.cache if key.startswith("login.")]
            for key in stale:
                del self.cache[key]
        except Exception as e:
            console.print(f"[red]Error saving login configuration: {e}")

# Initialize the ConfigManager when the module is imported
config_manager = ConfigManager()
