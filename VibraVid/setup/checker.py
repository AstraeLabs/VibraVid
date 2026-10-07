# 18.07.25

import logging
import os
import shutil
import subprocess

from rich.console import Console

from VibraVid.utils import config_manager

from .binary_paths import binary_paths

console = Console()
logger = logging.getLogger(__name__)

INSTALLATION_LEVELS = {
    "": ["ffmpeg", "velora", "flux"],
    "yt": ["ffmpeg", "velora", "flux", "yt-dlp", "deno"],
    "full": ["ffmpeg", "velora", "flux", "dovi_tool", "mkvtoolnix", "yt-dlp", "deno"],
}
_TERMUX_PREBUILT = "prebuilt"
_TERMUX_RETRY = "retry"
_TERMUX_MANUAL = "manual"


def is_termux() -> bool:
    """Check if the application is running inside Termux on Android."""
    return binary_paths.is_termux


def _should_download(tool_group: str) -> bool:
    """Return True if the given tool group should be downloaded based on the installation level."""
    level = config_manager.config.get("DEFAULT", "installation") or ""
    return tool_group in INSTALLATION_LEVELS.get(level, INSTALLATION_LEVELS[""])

def _executable_name(name: str) -> str:
    return f"{name}.exe" if binary_paths.system == "windows" else name


def _find_installed(group: str, binary_exec: str) -> str | None:
    """STEP 1 and 2: system PATH, then the local binary directory."""
    binary_path = shutil.which(binary_exec)
    if binary_path:
        logger.debug(f"Found {binary_exec} in system PATH ({binary_path})")
        return binary_path

    binary_local = binary_paths.get_binary_path(group, binary_exec)
    if binary_local and os.path.isfile(binary_local):
        logger.debug(f"Found {binary_exec} in local binary directory ({binary_local})")
        return binary_local

    return None


def _download_binary(group: str, binary_exec: str) -> str | None:
    """STEP 3: download the binary from AstraeLabs/Binary and report a failure."""
    binary_downloaded = binary_paths.download_binary(group, binary_exec)
    if binary_downloaded:
        logger.debug(f"Downloaded {binary_exec} to {binary_downloaded}")
        return binary_downloaded

    logger.error(f"Failed to download {binary_exec}")
    console.print(f"Failed to download {binary_exec}", style="red")
    return None


def _check_binary(group: str, name: str, download: bool = True, termux: str = _TERMUX_PREBUILT, termux_hint: tuple[str, ...] = ()) -> str | None:
    """
    Find a helper binary or download it.
    Order: system PATH -> binary directory -> download (only when the installation level includes *group*)
    """
    binary_exec = _executable_name(name)
    found = _find_installed(group, binary_exec)
    if found or not download:
        return found

    if is_termux():
        if termux != _TERMUX_MANUAL and _should_download(group):
            binary_downloaded = binary_paths.download_binary(group, binary_exec)
            if binary_downloaded:
                logger.debug(f"Downloaded {binary_exec} to {binary_downloaded}")
                return binary_downloaded

        if termux != _TERMUX_RETRY:
            for line in termux_hint:
                console.print(line)
            return None

    if not _should_download(group):
        return None

    return _download_binary(group, binary_exec)


def check_flux(download: bool = True) -> str | None:
    """Check for a flux binary and download if not found."""
    hint = (
        "[red]No prebuilt flux binary available for this Termux device.[/red]",
        "[cyan]If required, please compile it and place it in system PATH.[/cyan]",
    )
    return _check_binary("flux", "flux", download, _TERMUX_PREBUILT, hint)


def check_ffmpeg(download: bool = True) -> tuple[str | None, str | None]:
    """
    Check for FFmpeg executables and download if not found.
    Order: system PATH -> binary directory -> download from GitHub
    """
    system_platform = binary_paths.system
    ffmpeg_name = "ffmpeg.exe" if system_platform == "windows" else "ffmpeg"
    ffprobe_name = "ffprobe.exe" if system_platform == "windows" else "ffprobe"

    # STEP 1: Check system PATH
    ffmpeg_path = shutil.which(ffmpeg_name)
    ffprobe_path = shutil.which(ffprobe_name)
    if ffmpeg_path and ffprobe_path:
        logger.debug(f"Found ffmpeg ({ffmpeg_path}) and ffprobe ({ffprobe_path}) in system PATH")
        return ffmpeg_path, ffprobe_path

    # STEP 2: Check binary directory
    ffmpeg_local = binary_paths.get_binary_path("ffmpeg", ffmpeg_name)
    ffprobe_local = binary_paths.get_binary_path("ffmpeg", ffprobe_name)
    if ffmpeg_local and os.path.isfile(ffmpeg_local) and ffprobe_local and os.path.isfile(ffprobe_local):
        logger.debug(f"Found ffmpeg ({ffmpeg_local}) and ffprobe ({ffprobe_local}) in local binary directory")
        return ffmpeg_local, ffprobe_local

    if not download:
        return None, None

    # Termux-specific check
    if is_termux():
        console.print("[red]FFmpeg/FFprobe is required on Termux.[/red]")
        console.print("[cyan]Please install it using: [yellow]pkg install ffmpeg[/cyan]")
        return None, None

    # STEP 3: Download (only if installation level includes ffmpeg)
    if not _should_download("ffmpeg"):
        return None, None

    ffmpeg_downloaded = binary_paths.download_binary("ffmpeg", ffmpeg_name)
    ffprobe_downloaded = binary_paths.download_binary("ffmpeg", ffprobe_name)
    if ffmpeg_downloaded and ffprobe_downloaded:
        logger.debug(f"Downloaded ffmpeg ({ffmpeg_downloaded}) and ffprobe ({ffprobe_downloaded})")
        return ffmpeg_downloaded, ffprobe_downloaded

    logger.error("Failed to download FFmpeg")
    console.print("Failed to download FFmpeg", style="red")
    return None, None


def check_dovi_tool(download: bool = True) -> str | None:
    """
    Check for dovi_tool binary and download if not found.
    Order: system PATH -> binary directory -> download from GitHub
    """
    binary_exec = _executable_name("dovi_tool")
    found = _find_installed("dovi_tool", binary_exec)
    if found or not download:
        return found

    # Termux-specific check
    if is_termux():
        console.print("[yellow]dovi_tool not found in Termux environment.[/yellow]")
        cargo_path = shutil.which("cargo")
        if cargo_path:
            console.print("[cyan]Cargo detected. Attempting to build dovi_tool from source...[/cyan]")
            binary_dir = binary_paths.ensure_binary_directory()
            try:
                cmd = [
                    "cargo",
                    "install",
                    "--quiet",
                    "--git",
                    "https://github.com/quietvoid/dovi_tool",
                    "--root",
                    os.path.dirname(binary_dir),
                ]
                subprocess.run(cmd, check=True)
                cargo_bin = os.path.join(os.path.dirname(binary_dir), "bin", "dovi_tool")
                dest_bin = os.path.join(binary_dir, "dovi_tool")
                if os.path.isfile(cargo_bin):
                    shutil.move(cargo_bin, dest_bin)
                    os.chmod(dest_bin, 0o755)
                    console.print("[green]dovi_tool compiled and installed successfully![/green]")
                    return dest_bin
            except Exception as e:
                console.print(f"[red]Failed to compile dovi_tool from source: {e}[/red]")
        console.print("[cyan]Please compile manually using: [yellow]cargo install --git https://github.com/quietvoid/dovi_tool[/cyan]")
        return None

    if not _should_download("dovi_tool"):
        return None

    return _download_binary("dovi_tool", binary_exec)


def _check_mkvtoolnix_binary(name: str, download: bool = True) -> str | None:
    """Check for a binary shipped with MKVToolNix (mkvmerge, mkvpropedit...) and download if not found."""
    hint = (
        f"[red]MKVToolNix ({name}) is required on Termux.[/red]",
        "[cyan]Please install it using: [yellow]pkg install mkvtoolnix[/cyan]",
    )
    return _check_binary("mkvtoolnix", name, download, _TERMUX_MANUAL, hint)


def check_mkvmerge(download: bool = True) -> str | None:
    """Check for mkvmerge binary and download if not found."""
    return _check_mkvtoolnix_binary("mkvmerge", download)


def check_mkvpropedit(download: bool = True) -> str | None:
    """Check for mkvpropedit binary (in-place MKV tag editing) and download if not found."""
    return _check_mkvtoolnix_binary("mkvpropedit", download)


def check_velora(download: bool = True) -> str | None:
    """Check for velora binary and download if not found (on Termux the prebuilt download is attempted twice)."""
    return _check_binary("velora", "velora", download, _TERMUX_RETRY)


def check_yt_dlp(download: bool = True) -> str | None:
    """Check for yt-dlp binary and download if not found."""
    return _check_binary("yt-dlp", "yt-dlp", download, _TERMUX_PREBUILT, ("[red]No prebuilt yt-dlp binary for this Termux device.[/red]",))


def check_deno(download: bool = True) -> str | None:
    """Check for deno binary and download if not found."""
    return _check_binary("deno", "deno", download, _TERMUX_PREBUILT, ("[red]No prebuilt deno binary for this Termux device.[/red]",))
