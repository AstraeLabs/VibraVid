# 01.03.23

import importlib.metadata
import json
import logging
import os
import stat
import sys

from rich.console import Console

from VibraVid.setup import get_is_binary_installation
from VibraVid.setup.binary_paths import binary_paths
from VibraVid.utils import _startup_prefetch, config_manager
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

auto_update_check = config_manager.config.get_bool("DEFAULT", "auto_update_check")
timeout = config_manager.config.get_int("REQUESTS", "timeout")
_GENERIC_UPDATABLE_TOOLS = {
    "ffmpeg": ["ffmpeg", "ffprobe"],
    "flux": ["flux"],
    "dovi_tool": ["dovi_tool"],
    "mkvtoolnix": ["mkvmerge", "mkvinfo"],
    "velora": ["velora"],
    "yt-dlp": ["yt-dlp"],
    "deno": ["deno"],
}

def fetch_github_releases():
    """Fetch releases data from GitHub API (sync)"""
    prefetched = _startup_prefetch.collect("releases", timeout=timeout)
    if prefetched is not None:
        return prefetched

    url = f"https://api.github.com/repos/{__author__}/{__title__}/releases"
    logger.info(f"Checking latest {__title__} release: {url}")
    with create_client(headers=get_headers()) as client:
        response = client.get(url)
    return response.json()


def get_execution_mode():
    """Get the execution mode of the application"""
    if get_is_binary_installation():
        return "installer"

    try:
        package_location = importlib.metadata.files(__title__)
        if any("site-packages" in str(path) for path in package_location):
            return "pip"
    except importlib.metadata.PackageNotFoundError:
        pass

    return "source_code"


def auto_update():
    """Automatically update the binary to latest version"""
    if not get_is_binary_installation():
        console.print("[#E63946]Auto-update works only for binary installations")
        return False

    try:
        console.print("[#00BCD4]Checking for updates...")
        releases = fetch_github_releases()
        latest = releases[0]
        latest_version = latest.get("name", "").replace("v", "").replace(".", "")

        # Current version
        try:
            current = importlib.metadata.version(__title__)
        except Exception:
            current = source_code_version
        current_version = str(current).replace("v", "").replace(".", "")

        # Version comparison
        if current_version == latest_version:
            console.print(f"[#06A77D]Already on latest version: {current}")
            return False
        console.print(f"[#FFD60A]Current: {current} -> Latest: {latest.get('name')}")

        # Find appropriate asset
        system = binary_paths._detect_system()
        patterns = {"windows": ".exe", "linux": "linux", "darwin": "macos"}
        pattern = patterns.get(system, "")

        asset = None
        for a in latest.get("assets", []):
            if pattern in a["name"].lower():
                asset = a
                break
        console.print(f"[#00BCD4]Downloading {asset['name']}...")

        # Download
        with create_client(headers=get_headers(), timeout=300, follow_redirects=True) as client:
            response = client.get(asset["browser_download_url"])

        if response.status_code != 200:
            console.print("[#E63946]Download failed")
            return False

        # Save new executable
        current_exe = sys.executable
        new_exe = current_exe + ".new"
        with open(new_exe, "wb") as f:
            f.write(response.content)
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
    if local is None:
        binary_paths.set_local_tool_version(tool, remote)
        return {
            "success": True,
            "updated": False,
            "local": None,
            "latest": remote,
            "message": f"{tool} version baseline recorded ({remote}).",
        }

    if local == remote:
        logger.debug(f"{tool} is up to date (local: {local}, latest: {remote})")
        return {
            "success": True,
            "updated": False,
            "local": local,
            "latest": remote,
            "message": f"{tool} is up to date ({local}).",
        }

    console.print(f"[#FFD60A]{tool} outdated (local: {local} -> latest: {remote}), updating...")

    managed_dir = os.path.abspath(binary_paths.get_binary_directory())
    ext = ".exe" if binary_paths.system == "windows" else ""
    updated_any = False

    for name in exec_names:
        binary_name = f"{name}{ext}"
        path = binary_paths.get_binary_path(tool, binary_name)
        if not path:
            continue  # not installed locally; nothing to refresh

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

    if updated_any:
        binary_paths.set_local_tool_version(tool, remote)

    return {
        "success": True,
        "updated": updated_any,
        "local": local,
        "latest": remote,
        "message": (
            f"{tool} updated: {local} -> {remote}."
            if updated_any
            else f"{tool}: nothing installed locally to update."
        ),
    }


def check_all_binaries_update() -> dict:
    """Refresh every managed third-party binary published in AstraeLabs/Binary."""
    results = {}
    for tool, exec_names in _GENERIC_UPDATABLE_TOOLS.items():
        try:
            results[tool] = check_binary_update(tool, exec_names)
        except Exception as e:
            logger.debug(f"{tool} update check failed: {e}")
            results[tool] = {"success": False, "message": str(e)}
    return results


def update():
    """Check for updates on GitHub and display relevant information."""
    if auto_update_check:
        try:
            response_releases = fetch_github_releases()
        except Exception as e:
            logger.warning(f"Error accessing GitHub API: {e}")
            console.print("[#E63946]Failed to fetch latest version")
            return

        # Get latest version tag
        if response_releases:
            last_version = response_releases[0].get("tag_name", "Unknown")
        else:
            last_version = "Unknown"

    else:
        last_version = "Unknown"

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

    logger.info(f"Execution mode: {get_execution_mode()}, System: {binary_paths._detect_system()}, Version: {current_version}, Latest: {last_version}, Country: {country_code}")
    console.print(f"      [green]{get_execution_mode()} [dim]·[/] [red]{current_version} [dim]·[/] [cyan]{binary_paths.system} {binary_paths.arch} [dim]·[/] [purple]{country_code if country_code else 'None'} [dim]·[/] [link=https://discord.com/invite/8vV68UGRc7][#5865F2]Discord[/link] [dim]·[/] [link=https://www.paypal.com/donate/?hosted_button_id=UXTWMT8P6HE2C][#ea4aaa]Donate[/link]")

    if str(current_version).lower().replace("v.", "").replace("v", "") != str(last_version).lower().replace(
        "v.", ""
    ).replace("v", ""):
        if last_version == "Unknown" or last_version == "Beta Build":
            return

        tag_url = last_version if last_version.startswith("v") else f"v{last_version}"
        mode = get_execution_mode()
        if mode == "installer":
            console.print(f"\n[red]New [#00BCD4]version available: [#FFD60A][link=https://github.com/AstraeLabs/VibraVid/releases/tag/{tag_url}]{last_version}[/link] [dim]·[/] [#00BCD4]Run with [#FFD60A]-UP [#00BCD4]to auto-update")
        elif mode == "source_code":
            console.print(f"\n[red]New [#00BCD4]version available: [#FFD60A][link=https://github.com/AstraeLabs/VibraVid/releases/tag/{tag_url}]{last_version}[/link] [dim]·[/] [#00BCD4]Run [#FFD60A]git pull [#00BCD4]to update")
