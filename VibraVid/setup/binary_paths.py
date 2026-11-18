# 19.09.25

import glob
import json
import logging
import os
import platform
import re
import subprocess
import threading

from rich.console import Console
from rich.progress import Progress, ProgressColumn, TextColumn
from rich.text import Text

console = Console()
logger = logging.getLogger(__name__)

VERSIONS_FILENAME = ".versions.json"
LFS_TOOLS = {"ffmpeg"}
MIN_BINARY_SIZE = {"ffmpeg": 64 * 1024}

DOWNLOAD_CHUNK_SIZE = 256 * 1024
_IDENTITY_PATTERNS = {
    "flux": re.compile(r"^flux \d+\.\d+\.\d+\s*$"),
    "velora": re.compile(r'"name"\s*:\s*"Velora"'),
}
_identity_cache: dict[tuple, bool] = {}


def is_broken_binary(tool: str, path: str | None) -> bool:
    """True if *path* exists but is too small to be a working binary of *tool* (see MIN_BINARY_SIZE)."""
    minimum = MIN_BINARY_SIZE.get(tool, 0)
    try:
        return bool(path) and minimum > 0 and os.path.isfile(path) and os.path.getsize(path) < minimum
    except OSError:
        return False


def is_genuine_binary(tool: str, path: str | None) -> bool:
    """False if *path* is a different program that merely shares *tool*'s name (e.g. Flux CD's `flux`). Tools without a fingerprint always pass."""
    pattern = _IDENTITY_PATTERNS.get(tool)
    if pattern is None or not path:
        return True

    try:
        st = os.stat(path)
        key = (path, st.st_size, st.st_mtime_ns)  # a re-downloaded file at the same path is re-checked
    except OSError:
        return False

    if key in _identity_cache:
        return _identity_cache[key]

    try:
        out = subprocess.run([path, "--version"], capture_output=True, text=True, timeout=5)
        genuine = bool(pattern.search((out.stdout or out.stderr).strip()))
    except (OSError, subprocess.SubprocessError):
        genuine = False

    _identity_cache[key] = genuine
    return genuine


def _format_size(n: float) -> str:
    """Format a byte count as a short human-readable string (e.g. '12.3MB')."""
    n = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.0f}{unit}" if unit == "B" else f"{n:.1f}{unit}"
        n /= 1024
    return f"{n:.1f}TB"


class _DownloadBarColumn(ProgressColumn):
    def __init__(
        self,
        bar_width=30,
        complete_char="-",
        incomplete_char="-",
        complete_style="bright_magenta",
        incomplete_style="dim white",
    ):
        super().__init__()
        self.bar_width = bar_width
        self.complete_char = complete_char
        self.incomplete_char = incomplete_char
        self.complete_style = complete_style
        self.incomplete_style = incomplete_style

    def render(self, task):
        completed = task.completed
        total = task.total or 100

        bar_width = int((completed / total) * self.bar_width) if total > 0 else 0
        bar_width = min(bar_width, self.bar_width)

        text = Text()
        if bar_width > 0:
            text.append(self.complete_char * bar_width, style=self.complete_style)
            text.append(">", style=self.complete_style)
        if bar_width < self.bar_width:
            text.append(self.incomplete_char * (self.bar_width - bar_width - 1), style=self.incomplete_style)

        return text


class _DownloadStatsColumn(ProgressColumn):
    """Same look as VibraVid.core.ui.progress_bar.TransferStatsColumn (size + speed only)."""

    def render(self, task):
        size = task.fields.get("size", "")
        text = Text()

        if size:
            if "/" in size:
                current, total = size.split("/", 1)
                text.append(current, style="dim")
                text.append(" / ", style="dim")
                text.append(total, style="green")
            else:
                text.append(size, style="green")

        return text


class BinaryPaths:
    def __init__(self):
        self.system = self._detect_system()
        self.arch = self._detect_arch()
        self.libc = self._detect_libc()
        self.is_termux = self._detect_termux()
        self.home_dir = os.path.expanduser("~")
        self.binary_dir_override = os.environ.get("VIBRAVID_BINARY_DIR") or os.environ.get("BINARY_DIR")
        self.github_repo = "https://raw.githubusercontent.com/AstraeLabs/Binary/main"
        self.github_repo_lfs = "https://media.githubusercontent.com/media/AstraeLabs/Binary/main"
        self._paths_json_cache: dict | None = None
        self._resolved: dict[str, str] = {}
        self._cache_lock = threading.Lock()
        self._download_lock = threading.Lock()
        self._version_lock = threading.Lock()

    def _detect_system(self) -> str:
        """Detect and normalize the operating system name."""
        system = platform.system().lower()
        supported_systems = ["windows", "darwin", "linux"]
        if system not in supported_systems:
            logger.warning(f"Unsupported OS detected ({system}), falling back to linux semantics")
            return "linux"

        return system

    def _detect_arch(self) -> str:
        """Detect and normalize the system architecture."""
        machine = platform.machine().lower()
        arch_map = {
            "amd64": "x64",
            "x86_64": "x64",
            "arm64": "arm64",
            "aarch64": "arm64",
            "armv7l": "arm",
            "armv8l": "arm",
        }
        return arch_map.get(machine, "x64")

    def _detect_termux(self) -> bool:
        """Check if running inside Termux on Android."""
        return "TERMUX_VERSION" in os.environ or os.path.exists("/data/data/com.termux/files/usr/bin")

    def _detect_libc(self) -> str:
        """Detect whether this Linux host is running glibc or musl."""
        if self.system != "linux":
            return "glibc"

        # 1) musl's dynamic loader has a distinctive, unmistakable name.
        musl_loader_globs = (
            "/lib/ld-musl-*.so.1",
            "/lib64/ld-musl-*.so.1",
            "/usr/lib/ld-musl-*.so.1",
        )
        for pattern in musl_loader_globs:
            if glob.glob(pattern):
                return "musl"

        # 2) glibc exposes gnu_get_libc_version() in the process's own symbol table; musl does not, so this raises on musl systems.
        try:
            import ctypes

            _ = ctypes.CDLL(None).gnu_get_libc_version
            return "glibc"
        except Exception:
            pass

        # 3) Last resort: musl's `ldd --version` prints a usage banner mentioning "musl libc" (and exits non-zero);
        try:
            result = subprocess.run(
                ["ldd", "--version"],
                capture_output=True,
                text=True,
                timeout=3,
            )
            combined = f"{result.stdout}{result.stderr}".lower()
            if "musl" in combined:
                return "musl"
        except Exception:
            pass

        return "glibc"

    def get_binary_directory(self) -> str:
        """Return the platform-specific directory where binaries are stored.

        Layout (Windows example):
            C:\\binary\\VibraVid
                tools\\          <-- eseguibili (ffmpeg, yt-dlp, deno, velora, flux, ...)
                wvd\\            <-- chiavi Widevine (.wvd)
                prd\\            <-- chiavi PlayReady (.prd)
        """
        if self.binary_dir_override:
            return self.binary_dir_override

        if self.system == "windows":
            return os.path.join(os.path.splitdrive(self.home_dir)[0] + os.path.sep, "binary", "VibraVid")
        elif self.system == "darwin":
            return os.path.join(self.home_dir, "Applications", "binary", "VibraVid")
        else:  # linux
            return os.path.join(self.home_dir, ".local", "bin", "binary", "VibraVid")

    @staticmethod
    def _configured_dir(key: str) -> str | None:
        """Folder set in ``[DEFAULT] <key>`` of config.toml ("" = default). ``~`` and ``$VAR`` are expanded, a relative path starts from the program folder."""
        try:
            from VibraVid.utils import config_manager

            value = str(config_manager.config.get("DEFAULT", key, default="") or "").strip()
            if not value:
                return None

            value = os.path.expanduser(os.path.expandvars(value))
            if not os.path.isabs(value):
                value = os.path.join(config_manager.base_path, value)
            return os.path.normpath(value)
        except Exception as e:
            logger.debug(f"Could not read '{key}' from config: {e}")
            return None

    def get_tools_directory(self) -> str:
        """Directory degli eseguibili di supporto (ffmpeg, yt-dlp, deno, velora, flux, ...): ``<binary_dir>/tools``."""
        return os.path.join(self.get_binary_directory(), "tools")

    def get_cdm_directory(self) -> str:
        """Directory radice per i file CDM (device.wvd / device.prd), ora direttamente sotto VibraVid."""
        return self.get_binary_directory()

    def get_wvd_directory(self) -> str:
        """Directory per i file Widevine (.wvd); ``[DEFAULT] wvd_dir`` overrides ``<binary_dir>/wvd``."""
        return self._configured_dir("wvd_dir") or os.path.join(self.get_binary_directory(), "wvd")

    def get_prd_directory(self) -> str:
        """Directory per i file PlayReady (.prd); ``[DEFAULT] prd_dir`` overrides ``<binary_dir>/prd``."""
        return self._configured_dir("prd_dir") or os.path.join(self.get_binary_directory(), "prd")

    def ensure_binary_directory(self, mode: int = 0o755) -> str:
        """Create the binary directory if it does not already exist."""
        binary_dir = self.get_binary_directory()
        os.makedirs(binary_dir, mode=mode, exist_ok=True)
        return binary_dir

    def _load_paths_json(self) -> dict:
        """Fetch the binary_paths.json manifest from GitHub (thread-safe)."""
        if self._paths_json_cache is not None:
            return self._paths_json_cache

        with self._cache_lock:
            # Double-checked locking: another thread may have populated the cache while we were waiting to acquire the lock.
            if self._paths_json_cache is not None:
                return self._paths_json_cache

            try:
                from VibraVid.utils.http_client import create_client, get_headers

                url = f"{self.github_repo}/binary_paths.json"
                logger.info(f"Loading binary paths JSON from {url}")
                with create_client(headers=get_headers(), browser=None) as client:
                    response = client.get(url)
                response.raise_for_status()
                self._paths_json_cache = response.json()
                logger.info(f"Loaded binary paths JSON ({len(self._paths_json_cache)} entries)")
                return self._paths_json_cache
            except Exception as e:
                logger.error(f"Failed to load binary paths JSON: {e}", exc_info=True)
                return {}

    def get_binary_path(self, tool: str, binary_name: str) -> str | None:
        """Return the local path of *binary_name* if it has already been resolved (cache hit) or if the file exists on disk.

        Lookup order:
        1. in-memory cache (``_resolved``)
        2. ``<tools dir>/<binary_name>``
        """
        if binary_name in self._resolved:
            return self._resolved[binary_name]

        local_path = os.path.join(self.get_tools_directory(), binary_name)
        if self._is_usable_file(tool, local_path):
            logger.debug(f"Found local binary {binary_name} at {local_path}")
            self._resolved[binary_name] = local_path
            return local_path

        return None

    @staticmethod
    def _is_usable_file(tool: str, path: str) -> bool:
        """A regular file that is not obviously broken or not the right program: such a file is skipped (and replaced on the next download)."""
        if not os.path.isfile(path):
            return False
        
        if is_broken_binary(tool, path):
            logger.warning(f"Ignoring {path}: only {os.path.getsize(path)} bytes, not a working {tool} binary (Git LFS pointer or interrupted download)")
            return False
        
        if not is_genuine_binary(tool, path):
            logger.warning(f"Ignoring {path}: reports no valid {tool} version (wrong program or corrupted download), it will be downloaded again")
            return False
        
        return True

    def invalidate_binary(self, binary_name: str) -> None:
        """Drop the cached resolution for *binary_name* so the next lookup re-resolves it."""
        with self._download_lock:
            self._resolved.pop(binary_name, None)

    def get_remote_tool_version(self, tool: str) -> str | None:
        """Fetch the version currently published for *tool* in AstraeLabs/Binary (main)."""
        try:
            from VibraVid.utils.http_client import create_client, get_headers

            url = f"{self.github_repo}/binaries/{tool}.version"
            with create_client(headers=get_headers(), browser=None) as client:
                response = client.get(url)
            response.raise_for_status()
            return response.text.strip() or None
        except Exception as e:
            logger.debug(f"Failed to fetch remote version for {tool}: {e}")
            return None

    def _versions_file(self) -> str:
        return os.path.join(self.get_tools_directory(), VERSIONS_FILENAME)

    def _read_versions(self) -> dict:
        try:
            with open(self._versions_file(), encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def _write_versions(self, versions: dict) -> None:
        path = self._versions_file()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp_path = path + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(versions, f, indent=2, sort_keys=True)
        os.replace(tmp_path, path)

    def get_local_tool_version(self, tool: str) -> str | None:
        """Return the version we last recorded as installed for *tool*, or None if never recorded."""
        with self._version_lock:
            return self._read_versions().get(tool) or None

    def set_local_tool_version(self, tool: str, version: str) -> None:
        """Record the version currently installed for *tool*."""
        with self._version_lock:
            versions = self._read_versions()
            versions[tool] = version.strip()
            try:
                self._write_versions(versions)
            except OSError as e:
                logger.warning(f"Failed to record local version for {tool}: {e}")

    def _download_with_progress(self, client, url: str, tmp_path: str, tool: str, binary_name: str) -> None:
        """Stream *url* to *tmp_path* via curl_cffi, rendering a rich progress bar"""
        progress = Progress(
            TextColumn("[purple]{task.description}", justify="left"),
            _DownloadBarColumn(),
            TextColumn("[dim]|[/dim]"),
            _DownloadStatsColumn(),
            console=console,
            refresh_per_second=5.0,
        )

        description = f"[cyan]{binary_name} [dim]({tool} · {self.system} {self.arch} · {self.libc})"

        with progress:
            response = client.get(url, stream=True)
            response.raise_for_status()

            total = int(response.headers.get("content-length") or 0)
            total_str = _format_size(total) if total else "?"

            task_id = progress.add_task(
                description,
                total=total or None,
                size=f"0B/{total_str}",
            )

            downloaded = 0
            with open(tmp_path, "wb") as f:
                for chunk in response.iter_content(chunk_size=DOWNLOAD_CHUNK_SIZE):
                    if not chunk:
                        continue
                    f.write(chunk)
                    downloaded += len(chunk)
                    progress.update(
                        task_id,
                        completed=downloaded if total else None,
                        size=f"{_format_size(downloaded)}/{total_str}",
                    )

            if total:
                progress.update(task_id, completed=total)

    def download_binary(self, tool: str, binary_name: str) -> str | None:
        """
        Download *binary_name* from GitHub and store it in the binary
        directory (thread-safe), showing a progress bar while it downloads.

        If the binary has already been resolved (cache hit) or the file is
        already present on disk, the cached path is returned immediately
        without any network access.

        When multiple threads request the same binary concurrently, only the
        first acquires the lock and performs the download; the others wait
        and then find the file ready on disk.
        """
        if binary_name in self._resolved:
            return self._resolved[binary_name]

        local_path = os.path.join(self.get_tools_directory(), binary_name)

        with self._download_lock:
            # Double-checked locking: another thread may have completed the download while we were waiting to acquire the lock.
            if binary_name in self._resolved:
                return self._resolved[binary_name]

            # The file may also be present on disk even if not in the cache (e.g. left over from a previous application session).
            if self._is_usable_file(tool, local_path) and os.path.getsize(local_path) > 0:
                logger.info(f"Binary {binary_name} already on disk, skipping download")
                self._resolved[binary_name] = local_path
                return local_path

            paths_json = self._load_paths_json()
            base_key = f"{self.system}_{self.arch}_{tool}"
            termux_key = f"{base_key}_termux"
            musl_key = f"{base_key}_musl"
            if self.is_termux:
                # A glibc/musl linux build will not execute under Termux's Bionic
                # libc, so never silently fall back to base_key/musl_key here.
                if termux_key not in paths_json:
                    logger.error(f"No Termux build available for key {termux_key}")
                    return None

                key = termux_key
            elif self.libc == "musl" and musl_key in paths_json:
                key = musl_key
            else:
                key = base_key

            logger.info(f"Looking up binary paths for key {key}")

            if key not in paths_json:
                logger.error(f"No binary paths found for key {key} in binary paths JSON")

                # Fallback: the file may have been installed manually or by a previous session
                # even though the remote manifest is unavailable (e.g. HTTP 404 / network error).
                if self._is_usable_file(tool, local_path) and os.path.getsize(local_path) > 0:
                    logger.info(f"Manifest unavailable but binary {binary_name} found on disk, using it")
                    self._resolved[binary_name] = local_path
                    return local_path
                return None

            for rel_path in paths_json[key]:
                if not rel_path.endswith(binary_name):
                    continue

                repo_base = self.github_repo_lfs if tool in LFS_TOOLS else self.github_repo
                url = f"{repo_base}/binaries/{rel_path}"
                logger.info(f"Downloading {binary_name} from {url} to {local_path}")
                os.makedirs(os.path.dirname(local_path), exist_ok=True)

                # Write to a temporary file first, then rename atomically.
                tmp_path = local_path + ".tmp"
                try:
                    from VibraVid.utils.http_client import create_client, get_headers

                    with create_client(headers=get_headers(), browser=None) as client:
                        self._download_with_progress(client, url, tmp_path, tool, binary_name)

                    if self.system != "windows":
                        os.chmod(tmp_path, 0o755)

                    # Atomic rename: the binary becomes visible to other processes only after the write is fully complete.
                    os.replace(tmp_path, local_path)

                    logger.info(f"Downloaded {binary_name} to {local_path}")
                    self._resolved[binary_name] = local_path
                    return local_path

                except Exception as e:
                    logger.error(f"Failed to download {binary_name} from {url}: {e}", exc_info=True)
                    try:
                        if os.path.exists(tmp_path):
                            os.remove(tmp_path)
                    except OSError:
                        pass
                    return None

            available = paths_json.get(key, [])
            logger.error(f"Binary {binary_name} not listed in manifest for key {key}; available entries: {available}")
            return None


binary_paths = BinaryPaths()