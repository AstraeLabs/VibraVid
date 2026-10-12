# 01.03.23

import hashlib
import importlib.metadata
import json
import logging
import os
import platform
import re
import stat
import sys
import tempfile
import time
from pathlib import Path

from rich.console import Console

from VibraVid.setup import get_is_binary_installation
from VibraVid.setup.binary_paths import binary_paths
from VibraVid.utils import config_manager
from VibraVid.utils.http_client import create_client, get_headers

from .version import __author__, __title__
from .version import __version__ as source_code_version

# Variable
if get_is_binary_installation():
    base_path = os.path.join(sys._MEIPASS, "VibraVid")
else:
    base_path = os.path.dirname(__file__)

console = Console()
logger = logging.getLogger(__name__)

_GENERIC_UPDATABLE_TOOLS = {
    "ffmpeg": ["ffmpeg", "ffprobe"],
    "flux": ["flux"],
    "dovi_tool": ["dovi_tool"],
    "mkvtoolnix": ["mkvmerge", "mkvinfo", "mkvpropedit"],
    "velora": ["velora"],
    "yt-dlp": ["yt-dlp"],
    "deno": ["deno"],
}
_RELEASE_URL = f"https://api.github.com/repos/{__author__}/{__title__}/releases/latest"
_RELEASE_CACHE_TTL = 6 * 60 * 60
_CHECK_TIMEOUT = 4


def _numeric_version(value: str) -> tuple[int, ...] | None:
    """Only compare stable numeric release tags; never compare flattened strings."""
    match = re.fullmatch(r"v?(\d+(?:\.\d+)*)", str(value).strip(), re.IGNORECASE)
    return tuple(map(int, match.group(1).split("."))) if match else None


def _newer_version(latest: str, current: str) -> bool:
    left, right = _numeric_version(latest), _numeric_version(current)
    if left is None or right is None:
        return False
    width = max(len(left), len(right))
    return left + (0,) * (width - len(left)) > right + (0,) * (width - len(right))


def _os_release_version() -> str | None:
    try:
        values = {}
        for line in Path("/etc/os-release").read_text(encoding="utf-8").splitlines():
            if "=" in line:
                key, value = line.split("=", 1)
                values[key] = value.strip().strip('"')
        if values.get("ID") == "ubuntu":
            return values.get("VERSION_ID")
    except OSError:
        pass
    return None


def _compatible_asset_names(system: str, arch: str, os_version: str | None) -> list[str]:
    """Exact CI build targets, in priority order. Unknown targets are intentionally unsupported."""
    prefix = "VibraVid"
    if system == "windows" and arch == "x64":
        if os_version == "2025":
            return [f"{prefix}_win_2025_x64.exe", f"{prefix}_win_2022_x64.exe"]
        if os_version == "2022":
            return [f"{prefix}_win_2022_x64.exe"]
    if system == "linux" and arch == "x64":
        if os_version == "24.04":
            return [f"{prefix}_linux_24_04_x64", f"{prefix}_linux_22_04_x64"]
        if os_version == "22.04":
            return [f"{prefix}_linux_22_04_x64"]
    if system == "darwin":
        if not os_version or not os_version.split(".")[0].isdigit():
            return []
        if arch == "x64" and os_version and int(os_version.split(".")[0]) >= 15:
            return [f"{prefix}_mac_15_x64"]
        if arch == "arm64" and os_version:
            major = int(os_version.split(".")[0])
            if major >= 26:
                return [f"{prefix}_mac_26_arm64", f"{prefix}_mac_15_arm64"]
            if major >= 15:
                return [f"{prefix}_mac_15_arm64"]
    return []


def _local_platform() -> tuple[str, str, str | None]:
    system, arch = binary_paths._detect_system(), binary_paths.arch
    if system == "windows":
        # Windows Server 2022 = 20348; Server 2025 = 26100. Also cover desktop Windows.
        build = sys.getwindowsversion().build if hasattr(sys, "getwindowsversion") else 0
        version = "2025" if build >= 26100 else "2022"
    elif system == "linux":
        version = _os_release_version()
    else:
        version = platform.mac_ver()[0] or None
    return system, arch, version


def pick_release_asset(assets: list[dict], system: str, arch: str, os_version: str | None = None) -> dict | None:
    """Match the release's exact OS version and CPU architecture."""
    if os_version is None:
        detected_system, detected_arch, detected_version = _local_platform()
        if system == detected_system and arch == detected_arch:
            os_version = detected_version
    by_name = {asset.get("name", "").lower(): asset for asset in assets}
    for name in _compatible_asset_names(system, arch, os_version):
        if name.lower() in by_name:
            return by_name[name.lower()]
    return None


def fetch_github_releases():
    """Legacy release listing for other callers; bounded request."""
    url = f"https://api.github.com/repos/{__author__}/{__title__}/releases"
    logger.info(f"Checking latest {__title__} release: {url}")
    with create_client(headers=get_headers(), timeout=_CHECK_TIMEOUT) as client:
        response = client.get(url)
        response.raise_for_status()
    return response.json()


def get_execution_mode():
    """Get the execution mode of the application"""
    if os.environ.get("VIBRAVID_RUNNING_IN_DOCKER") or (sys.platform.startswith("linux") and Path("/.dockerenv").exists()):
        return "docker"
    if get_is_binary_installation():
        return "installer"

    try:
        importlib.metadata.distribution(__title__)
        return "pip"
    except importlib.metadata.PackageNotFoundError:
        pass

    return "source_code"


def _current_version() -> str:
    if get_is_binary_installation():
        return source_code_version
    try:
        return importlib.metadata.version(__title__)
    except importlib.metadata.PackageNotFoundError:
        return source_code_version


def _latest_release(*, force: bool = False, timeout: float = _CHECK_TIMEOUT) -> dict:
    cache_path = Path(config_manager.base_path) / ".cache" / "vibravid_latest_release.json"
    if not force:
        try:
            if time.time() - cache_path.stat().st_mtime < _RELEASE_CACHE_TTL:
                cached = json.loads(cache_path.read_text(encoding="utf-8"))
                if isinstance(cached, dict) and cached.get("tag_name"):
                    return cached
        except (OSError, ValueError, TypeError):
            pass
    with create_client(headers={"Accept": "application/vnd.github+json", "User-Agent": "VibraVid-Updater"}, timeout=timeout) as client:
        response = client.get(_RELEASE_URL, timeout=timeout)
        response.raise_for_status()
        release = response.json()
    if not isinstance(release, dict) or not release.get("tag_name") or not isinstance(release.get("assets"), list):
        raise ValueError("Invalid GitHub release metadata")
    try:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=cache_path.parent, delete=False) as f:
            temporary = Path(f.name)
            json.dump(release, f)
        os.replace(temporary, cache_path)
    except OSError as exc:
        logger.debug("Release cache could not be saved: %s", exc)
    return release


def check_for_updates(*, force: bool = False, timeout: float = _CHECK_TIMEOUT) -> dict:
    """Read-only update check. Never downloads a release asset or changes the running app."""
    mode = get_execution_mode()
    current = _current_version()
    result = {"available": False, "current": current, "latest": None, "release": None,
              "asset": None, "mode": mode, "error": None}
    try:
        release = _latest_release(force=force, timeout=timeout)
        latest = release["tag_name"]
        result.update(latest=latest, release=release, available=_newer_version(latest, current))
        if result["available"] and mode == "installer":
            result["asset"] = pick_release_asset(release["assets"], *_local_platform())
    except Exception as exc:
        logger.debug("Update check unavailable: %s", exc)
        result["error"] = str(exc)
    return result


def download_release_asset(asset: dict, *, destination: str | Path | None = None, timeout: float = 60) -> Path:
    """Download to an adjacent .part file, verify GitHub's SHA-256 digest, then atomically rename.

    Explicit caller action is required; this function never executes or installs the download.
    """
    from urllib.parse import urlparse

    name = asset.get("name", "")
    url = asset.get("browser_download_url", "")
    parsed = urlparse(url)
    if not name or name in {".", ".."} or name != Path(name).name or not re.fullmatch(r"[\w.\-]+", name):
        raise ValueError("Unsafe release asset name")
    if (parsed.scheme, parsed.netloc) != ("https", "github.com") or not parsed.path.startswith(
        f"/{__author__}/{__title__}/releases/download/"
    ):
        raise ValueError("Unexpected release asset URL")
    target = Path(destination) if destination is not None else Path.home() / "Downloads" / "VibraVid" / name
    if target.resolve() == Path(sys.executable).resolve():
        raise ValueError("Cannot overwrite the running executable")
    digest = asset.get("digest") or ""
    if digest and not re.fullmatch(r"sha256:[a-fA-F0-9]{64}", digest):
        raise ValueError("Unsupported release digest")
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="wb", prefix=target.name + ".", suffix=".part", dir=target.parent, delete=False) as file:
            temporary = Path(file.name)
            sha256 = hashlib.sha256()
            downloaded_bytes = 0
            with create_client(headers={"User-Agent": "VibraVid-Updater"}, timeout=timeout, follow_redirects=True) as client:
                response = client.get(url, stream=True, timeout=timeout)
                try:
                    response.raise_for_status()
                    for chunk in response.iter_content(chunk_size=256 * 1024):
                        if chunk:
                            file.write(chunk)
                            sha256.update(chunk)
                            downloaded_bytes += len(chunk)
                finally:
                    response.close()
            if not downloaded_bytes or (asset.get("size") and downloaded_bytes != asset["size"]):
                raise ValueError("Incomplete release asset download")
            if digest and sha256.hexdigest().lower() != digest.split(":", 1)[1].lower():
                raise ValueError("SHA-256 mismatch for downloaded release asset")
        if os.name != "nt":
            temporary.chmod(0o755)
        os.replace(temporary, target)
        return target
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def auto_update():
    """Explicit --update legacy behavior: download, then replace after process exit."""
    if get_execution_mode() != "installer":
        console.print("[#E63946]Auto-update works only for binary installations")
        return False

    try:
        console.print("[#00BCD4]Checking for updates...")
        check = check_for_updates(force=True)
        if check["error"]:
            raise RuntimeError(check["error"])
        if not check["available"]:
            console.print(f"[#06A77D]Already on latest version: {check['current']}")
            return False
        console.print(f"[#FFD60A]Current: {check['current']} -> Latest: {check['latest']}")

        # Find appropriate asset
        system = binary_paths._detect_system()
        asset = check["asset"]
        if asset is None:
            console.print(f"[#E63946]No release asset found for {system} {binary_paths.arch}")
            return False
        console.print(f"[#00BCD4]Downloading {asset['name']}...")
        current_exe = sys.executable
        new_exe = current_exe + ".new"
        download_release_asset(asset, destination=new_exe)
        console.print("[#06A77D]Download completed!")

        # Write update script
        if system == "windows":
            script = current_exe + ".bat"
            with open(script, "w") as f:
                f.write("@echo off\n")
                f.write("timeout /t 2 /nobreak >nul\n")
                f.write(f'move /y "{new_exe}" "{current_exe}"\n')
                f.write(f'start "" "{current_exe}"\n')
                f.write('del "%~f0"\n')

            os.startfile(script)

        else:
            os.chmod(new_exe, stat.S_IRWXU | stat.S_IRGRP | stat.S_IXGRP)

            script = current_exe + ".sh"
            with open(script, "w") as f:
                f.write("#!/bin/bash\n")
                f.write("sleep 2\n")
                f.write(f'mv "{new_exe}" "{current_exe}"\n')
                f.write(f'chmod +x "{current_exe}"\n')
                f.write(f'"{current_exe}" &\n')
                f.write(f'rm "{script}"\n')

            os.chmod(script, stat.S_IRWXU)
            os.system(f'nohup "{script}" &')

        console.print("[#00BCD4]Restarting...")
        sys.exit(0)

    except Exception as e:
        console.print(f"[#E63946]Update failed: {e}")
        return False


def check_binary_update(tool: str, exec_names: list[str]) -> dict:
    """Re-download *tool*'s binaries when AstraeLabs/Binary has published a newer version."""
    remote = binary_paths.get_remote_tool_version(tool)
    if not remote:
        if tool in {"yt-dlp", "deno"}:
            from VibraVid.setup.checker import check_deno, check_yt_dlp

            checker = check_yt_dlp if tool == "yt-dlp" else check_deno
            if checker(download=False):
                return {
                    "success": True,
                    "updated": False,
                    "message": "up to date.",
                }
            return {
                "success": False,
                "updated": False,
                "message": "not installed.",
            }
        return {"success": False, "message": f"Could not fetch the latest {tool} version."}

    local = binary_paths.get_local_tool_version(tool)

    managed_dir = os.path.abspath(binary_paths.get_binary_directory())
    ext = ".exe" if binary_paths.system == "windows" else ""

    if local is not None and local == remote:
        missing = [f"{name}{ext}" for name in exec_names if not binary_paths.get_binary_path(tool, f"{name}{ext}")]
        if not missing:
            logger.debug(f"{tool} is up to date (local: {local}, latest: {remote})")
            return {
                "success": True,
                "updated": False,
                "local": local,
                "latest": remote,
                "message": f"{tool} is up to date ({local}).",
            }

        console.print(f"[#FFD60A]{tool} is up to date ({local}) but missing locally, reinstalling {len(missing)} binary(ies)...")
        reinstalled_any = False
        for binary_name in missing:
            if binary_paths.download_binary(tool, binary_name):
                reinstalled_any = True

        return {
            "success": True,
            "updated": reinstalled_any,
            "local": local,
            "latest": remote,
            "message": (
                f"{tool}: reinstalled {len(missing)} missing binary(ies) ({local})."
                if reinstalled_any
                else f"{tool}: {len(missing)} binary(ies) missing locally and reinstall failed."
            ),
        }

    if local is None:
        console.print(f"[#FFD60A]{tool} local version unknown, verifying installation against latest ({remote})...")
    else:
        console.print(f"[#FFD60A]{tool} outdated (local: {local} -> latest: {remote}), updating...")

    updated_any = False

    for name in exec_names:
        binary_name = f"{name}{ext}"
        path = binary_paths.get_binary_path(tool, binary_name)

        if path:
            # Only manage the binary we downloaded ourselves; never touch a system-PATH install.
            if os.path.dirname(os.path.abspath(path)) != managed_dir:
                logger.info(f"{binary_name} resolved outside the managed binary directory; skipping")
                continue

            try:
                os.remove(path)
            except OSError as e:
                logger.warning(f"Failed to remove stale {binary_name}: {e}")
                continue

            binary_paths.invalidate_binary(binary_name)

        if binary_paths.download_binary(tool, binary_name):
            updated_any = True

    # Only record the remote version once it has actually been installed/verified;
    # never baseline an unknown local version against a binary we haven't confirmed.
    if updated_any:
        binary_paths.set_local_tool_version(tool, remote)

    if local is None:
        message = (
            f"{tool} installation verified ({remote})."
            if updated_any
            else f"{tool}: nothing installed locally and installation failed."
        )
    else:
        message = (
            f"{tool} updated: {local} -> {remote}."
            if updated_any
            else f"{tool}: nothing installed locally to update."
        )

    return {
        "success": True,
        "updated": updated_any,
        "local": local,
        "latest": remote,
        "message": message,
    }


def check_all_binaries_update() -> dict:
    """Refresh every managed third-party binary published in AstraeLabs/Binary"""
    from VibraVid.setup.checker import _should_download

    results = {}
    for tool, exec_names in _GENERIC_UPDATABLE_TOOLS.items():
        if not _should_download(tool):
            continue
        try:
            results[tool] = check_binary_update(tool, exec_names)
        except Exception as e:
            logger.debug(f"{tool} update check failed: {e}")
            results[tool] = {"success": False, "message": str(e)}
    return results


def update():
    """Print the one-line status banner: execution mode, version, platform, country and community links."""
    # Get the current version (installed version)
    try:
        current_version = importlib.metadata.version(__title__)
    except importlib.metadata.PackageNotFoundError:
        current_version = source_code_version

    # Get country code
    country_code = None
    try:
        CACHE_FILE = os.path.join(config_manager.base_path, ".cache", "ip.json")
        if os.path.exists(CACHE_FILE):
            data_json = json.load(open(CACHE_FILE))
            country_code = data_json.get("country_code")
    except Exception:
        pass

    logger.info(f"Execution mode: {get_execution_mode()}, System: {binary_paths._detect_system()}, Version: {current_version}, Country: {country_code}")
    console.print(f"      [green]{get_execution_mode()} [dim]·[/] [red]{current_version} [dim]·[/] [cyan]{binary_paths.system} {binary_paths.arch} [dim]·[/] [purple]{country_code if country_code else 'None'} [dim]·[/] [link=https://discord.com/invite/8vV68UGRc7][#5865F2]Discord[/link] [dim]·[/] [link=https://www.paypal.com/donate/?hosted_button_id=UXTWMT8P6HE2C][#ea4aaa]Donate[/link]")
