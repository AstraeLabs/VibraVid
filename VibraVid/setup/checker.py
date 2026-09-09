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
    "": ["ffmpeg", "velora", "flux", "yt-dlp", "deno"],
    "full": ["ffmpeg", "velora", "flux", "dovi_tool", "mkvtoolnix", "yt-dlp", "deno"],
}


def is_termux() -> bool:
    """Check if the application is running inside Termux on Android."""
    return binary_paths.is_termux


def _should_download(tool_group: str) -> bool:
    """Return True if the given tool group should be downloaded based on the installation level."""
    level = config_manager.config.get("DEFAULT", "installation") or ""
    return tool_group in INSTALLATION_LEVELS.get(level, INSTALLATION_LEVELS[""])

def check_flux(download: bool = True) -> str | None:
    """
    Check for a flux binary and download if not found.
    Order: system PATH -> binary directory -> download from GitHub
    """
    system_platform = binary_paths.system
    binary_exec = "flux.exe" if system_platform == "windows" else "flux"

    # STEP 1: Check system PATH
    binary_path = shutil.which(binary_exec)
    if binary_path:
        logger.debug(f"Found {binary_exec} in system PATH ({binary_path})")
        return binary_path

    # STEP 2: Check local binary directory
    binary_local = binary_paths.get_binary_path("flux", binary_exec)
    if binary_local and os.path.isfile(binary_local):
        logger.debug(f"Found {binary_exec} in local binary directory ({binary_local})")
        return binary_local

    if not download:
        return None

    # Termux-specific check: try the prebuilt android-target binary first
    if is_termux():
        if _should_download("flux"):
            binary_downloaded = binary_paths.download_binary("flux", binary_exec)
            if binary_downloaded:
                logger.debug(f"Downloaded {binary_exec} to {binary_downloaded}")
                return binary_downloaded
        
        console.print("[red]No prebuilt flux binary available for this Termux device.[/red]")
        console.print("[cyan]If required, please compile it and place it in system PATH.[/cyan]")
        return None

    # STEP 3: Download (only if installation level includes flux)
    if not _should_download("flux"):
        return None

    binary_downloaded = binary_paths.download_binary("flux", binary_exec)
    if binary_downloaded:
        logger.debug(f"Downloaded {binary_exec} to {binary_downloaded}")
        return binary_downloaded

    logger.error(f"Failed to download {binary_exec}")
    console.print(f"Failed to download {binary_exec}", style="red")
    return None


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
    system_platform = binary_paths.system
    binary_exec = "dovi_tool.exe" if system_platform == "windows" else "dovi_tool"

    # STEP 1: Check system PATH
    binary_path = shutil.which(binary_exec)
    if binary_path:
        logger.debug(f"Found {binary_exec} in system PATH ({binary_path})")
        return binary_path

    # STEP 2: Check local binary directory
    binary_local = binary_paths.get_binary_path("dovi_tool", binary_exec)
    if binary_local and os.path.isfile(binary_local):
        logger.debug(f"Found {binary_exec} in local binary directory ({binary_local})")
        return binary_local

    if not download:
        return None

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

    # STEP 3: Download (only if installation level includes dovi_tool)
    if not _should_download("dovi_tool"):
        return None

    binary_downloaded = binary_paths.download_binary("dovi_tool", binary_exec)
    if binary_downloaded:
        logger.debug(f"Downloaded {binary_exec} to {binary_downloaded}")
        return binary_downloaded

    logger.error(f"Failed to download {binary_exec}")
    console.print(f"Failed to download {binary_exec}", style="red")
    return None


def check_mkvmerge(download: bool = True) -> str | None:
    """
    Check for mkvmerge binary and download if not found.
    Order: system PATH -> binary directory -> download from GitHub
    """
    system_platform = binary_paths.system
    binary_exec = "mkvmerge.exe" if system_platform == "windows" else "mkvmerge"

    # STEP 1: Check system PATH
    binary_path = shutil.which(binary_exec)
    if binary_path:
        logger.debug(f"Found {binary_exec} in system PATH ({binary_path})")
        return binary_path

    # STEP 2: Check local binary directory
    binary_local = binary_paths.get_binary_path("mkvtoolnix", binary_exec)
    if binary_local and os.path.isfile(binary_local):
        logger.debug(f"Found {binary_exec} in local binary directory ({binary_local})")
        return binary_local

    if not download:
        return None

    # Termux-specific check
    if is_termux():
        console.print("[red]MKVToolNix (mkvmerge) is required on Termux.[/red]")
        console.print("[cyan]Please install it using: [yellow]pkg install mkvtoolnix[/cyan]")
        return None

    # STEP 3: Download (only if installation level includes mkvtoolnix)
    if not _should_download("mkvtoolnix"):
        return None

    binary_downloaded = binary_paths.download_binary("mkvtoolnix", binary_exec)
    if binary_downloaded:
        logger.debug(f"Downloaded {binary_exec} to {binary_downloaded}")
        return binary_downloaded

    logger.error(f"Failed to download {binary_exec}")
    console.print(f"Failed to download {binary_exec}", style="red")
    return None


def check_velora(download: bool = True) -> str | None:
    """
    Check for velora binary and download if not found.
    Order: system PATH -> binary directory -> download from GitHub
    """
    system_platform = binary_paths.system
    binary_exec = "velora.exe" if system_platform == "windows" else "velora"

    # STEP 1: Check system PATH
    binary_path = shutil.which(binary_exec)
    if binary_path:
        logger.debug(f"Found {binary_exec} in system PATH ({binary_path})")
        return binary_path

    # STEP 2: Check local binary directory
    binary_local = binary_paths.get_binary_path("velora", binary_exec)
    if binary_local and os.path.isfile(binary_local):
        logger.debug(f"Found {binary_exec} in local binary directory ({binary_local})")
        return binary_local

    if not download:
        return None

    # Termux-specific check: try the prebuilt android-target binary first
    if is_termux():
        if _should_download("velora"):
            binary_downloaded = binary_paths.download_binary("velora", binary_exec)
            if binary_downloaded:
                logger.debug(f"Downloaded {binary_exec} to {binary_downloaded}")
                return binary_downloaded

    # STEP 3: Download (only if installation level includes velora)
    if not _should_download("velora"):
        return None

    binary_downloaded = binary_paths.download_binary("velora", binary_exec)
    if binary_downloaded:
        logger.debug(f"Downloaded {binary_exec} to {binary_downloaded}")
        return binary_downloaded

    logger.error(f"Failed to download {binary_exec}")
    console.print(f"Failed to download {binary_exec}", style="red")
    return None


def check_yt_dlp(download: bool = True) -> str | None:
    """
    Check for yt-dlp binary and download if not found.
    Order: system PATH -> binary directory -> AstraeLabs/Binary -> GitHub official release
    """
    system_platform = binary_paths.system
    binary_exec = "yt-dlp.exe" if system_platform == "windows" else "yt-dlp"

    # STEP 1: Check system PATH
    binary_path = shutil.which(binary_exec)
    if binary_path:
        logger.debug(f"Found {binary_exec} in system PATH ({binary_path})")
        return binary_path

    # STEP 2: Check local binary directory
    binary_local = binary_paths.get_binary_path("yt-dlp", binary_exec)
    if binary_local and os.path.isfile(binary_local):
        logger.debug(f"Found {binary_exec} in local binary directory ({binary_local})")
        return binary_local

    if not download:
        return None

    # Termux-specific check
    if is_termux():
        if _should_download("yt-dlp"):
            binary_downloaded = binary_paths.download_binary("yt-dlp", binary_exec)
            if binary_downloaded:
                return binary_downloaded
        console.print("[red]No prebuilt yt-dlp binary for this Termux device.[/red]")
        return None

    # STEP 3: Download from AstraeLabs/Binary (only if installation level includes yt-dlp)
    if not _should_download("yt-dlp"):
        return None

    binary_downloaded = binary_paths.download_binary("yt-dlp", binary_exec)
    if binary_downloaded:
        logger.debug(f"Downloaded {binary_exec} to {binary_downloaded}")
        return binary_downloaded

    # STEP 4: Fallback to official GitHub release
    logger.info(f"AstraeLabs binary unavailable, falling back to GitHub release for {binary_exec}")
    return _download_yt_dlp_github(binary_exec)


def check_deno(download: bool = True) -> str | None:
    """
    Check for deno binary and download if not found.
    Order: system PATH -> binary directory -> AstraeLabs/Binary -> GitHub official release
    """
    system_platform = binary_paths.system
    binary_exec = "deno.exe" if system_platform == "windows" else "deno"

    # STEP 1: Check system PATH
    binary_path = shutil.which(binary_exec)
    if binary_path:
        logger.debug(f"Found {binary_exec} in system PATH ({binary_path})")
        return binary_path

    # STEP 2: Check local binary directory
    binary_local = binary_paths.get_binary_path("deno", binary_exec)
    if binary_local and os.path.isfile(binary_local):
        logger.debug(f"Found {binary_exec} in local binary directory ({binary_local})")
        return binary_local

    if not download:
        return None

    # Termux-specific check
    if is_termux():
        if _should_download("deno"):
            binary_downloaded = binary_paths.download_binary("deno", binary_exec)
            if binary_downloaded:
                return binary_downloaded
        console.print("[red]No prebuilt deno binary for this Termux device.[/red]")
        return None

    # STEP 3: Download from AstraeLabs/Binary (only if installation level includes deno)
    if not _should_download("deno"):
        return None

    binary_downloaded = binary_paths.download_binary("deno", binary_exec)
    if binary_downloaded:
        logger.debug(f"Downloaded {binary_exec} to {binary_downloaded}")
        return binary_downloaded

    # STEP 4: Fallback to official GitHub release
    logger.info(f"AstraeLabs binary unavailable, falling back to GitHub release for {binary_exec}")
    return _download_deno_github(binary_exec)


def _download_yt_dlp_github(binary_exec: str) -> str | None:
    """Download yt-dlp from official GitHub release."""
    import urllib.request

    system_platform = binary_paths.system
    arch = binary_paths.arch

    # Map to GitHub release assets
    if system_platform == "windows":
        url = "https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp.exe"
    elif system_platform == "darwin":
        if arch == "arm64":
            url = "https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp_macos_arm64"
        else:
            url = "https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp_macos_x86_64"
    else:  # linux
        url = "https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp"

    try:
        binary_dir = binary_paths.ensure_binary_directory()
        dest_path = os.path.join(binary_dir, binary_exec)

        console.print(f"[cyan]Downloading yt-dlp from GitHub ({url})...[/cyan]")

        # Download with progress
        with urllib.request.urlopen(url) as response:
            total = int(response.headers.get("Content-Length", 0))
            downloaded = 0
            chunk_size = 8192

            with open(dest_path + ".tmp", "wb") as f:
                while True:
                    chunk = response.read(chunk_size)
                    if not chunk:
                        break
                    f.write(chunk)
                    downloaded += len(chunk)
                    if total:
                        percent = (downloaded / total) * 100
                        console.print(f"  [dim]{percent:.1f}%[/dim]", end="\r")

        if system_platform != "windows":
            os.chmod(dest_path + ".tmp", 0o755)
        os.replace(dest_path + ".tmp", dest_path)

        console.print(f"[green]yt-dlp downloaded to {dest_path}[/green]")
        return dest_path

    except Exception as e:
        logger.error(f"Failed to download yt-dlp from GitHub: {e}")
        console.print(f"[red]Failed to download yt-dlp from GitHub: {e}[/red]")
        return None


def _download_deno_github(binary_exec: str) -> str | None:
    """Download deno from official GitHub release."""
    import urllib.request
    import zipfile
    import tempfile

    system_platform = binary_paths.system
    arch = binary_paths.arch

    # Map to GitHub release assets
    if system_platform == "windows":
        if arch == "x64":
            asset_name = "deno-x86_64-pc-windows-msvc.zip"
        else:
            asset_name = "deno-aarch64-pc-windows-msvc.zip"
    elif system_platform == "darwin":
        if arch == "arm64":
            asset_name = "deno-aarch64-apple-darwin.zip"
        else:
            asset_name = "deno-x86_64-apple-darwin.zip"
    else:  # linux
        if arch == "x64":
            asset_name = "deno-x86_64-unknown-linux-gnu.zip"
        elif arch == "arm64":
            asset_name = "deno-aarch64-unknown-linux-gnu.zip"
        else:
            asset_name = "deno-x86_64-unknown-linux-gnu.zip"

    url = f"https://github.com/denoland/deno/releases/latest/download/{asset_name}"

    try:
        binary_dir = binary_paths.ensure_binary_directory()
        dest_path = os.path.join(binary_dir, binary_exec)

        console.print(f"[cyan]Downloading deno from GitHub ({url})...[/cyan]")

        # Download to temp file
        with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as tmp:
            tmp_path = tmp.name

        urllib.request.urlretrieve(url, tmp_path)

        # Extract
        if system_platform == "windows":
            with zipfile.ZipFile(tmp_path, "r") as zf:
                # Find deno.exe in zip
                for name in zf.namelist():
                    if name.endswith("deno.exe"):
                        zf.extract(name, binary_dir)
                        extracted = os.path.join(binary_dir, name)
                        if extracted != dest_path:
                            shutil.move(extracted, dest_path)
                        break
        else:
            # For Unix, it's a zip with deno binary
            with zipfile.ZipFile(tmp_path, "r") as zf:
                for name in zf.namelist():
                    if name.endswith("deno") or name == "deno":
                        zf.extract(name, binary_dir)
                        extracted = os.path.join(binary_dir, name)
                        if extracted != dest_path:
                            shutil.move(extracted, dest_path)
                        break

        os.remove(tmp_path)

        if system_platform != "windows":
            os.chmod(dest_path, 0o755)

        console.print(f"[green]deno downloaded to {dest_path}[/green]")
        return dest_path

    except Exception as e:
        logger.error(f"Failed to download deno from GitHub: {e}")
        console.print(f"[red]Failed to download deno from GitHub: {e}[/red]")
        return None
