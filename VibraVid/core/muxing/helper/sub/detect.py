# 16.04.24

import json
import logging
import os
import platform
import subprocess

from rich.console import Console

from VibraVid.core.utils.codec import get_codec_extension
from VibraVid.setup import get_ffprobe_path

from ..font import FontManager
from .sanitize import sanitize_vtt_file

console = Console()
logger = logging.getLogger(__name__)


def extract_font_name_from_style(style_line: str) -> str | None:
    """Extract font name from ASS/SSA Style line."""
    try:
        if not style_line.startswith("Style:"):
            return None

        # Split by comma and get fields
        parts = style_line[6:].split(",")  # Skip 'Style:'

        if len(parts) < 2:
            return None

        # Font name is the second field (index 1)
        font_name = parts[1].strip()

        if not font_name:
            return None

        return font_name

    except Exception as e:
        console.print(f"[red]Error extracting font name from line: {style_line.strip()}: {str(e)}")
        return None


def process_subtitle_fonts(subtitle_path: str):
    """Process fonts in subtitle files (ASS/SSA), warn if not found."""
    format = detect_subtitle_format(subtitle_path)
    if format not in ["ass", "ssa"]:
        return

    font_manager = FontManager()
    installed_fonts = font_manager.get_installed_fonts()

    if not installed_fonts:
        console.print("[red]Error: No fonts detected on system. Cannot process subtitle fonts.")
        return

    installed_fonts_lower = [f.lower() for f in installed_fonts]

    try:
        with open(subtitle_path, encoding="utf-8") as f:
            lines = f.readlines()
    except Exception as e:
        console.print(f"[red]Error reading subtitle file {subtitle_path}: {str(e)}")
        return

    missing_fonts = set()
    found_fonts = set()

    for i, line in enumerate(lines):
        if line.startswith("Style:"):
            font_name = extract_font_name_from_style(line)

            if font_name is None:
                console.print(f"[yellow]Warning: Could not parse Style line {i + 1}: {line.strip()}")
                continue

            # Check if font is installed
            if font_name.lower() in installed_fonts_lower:
                found_fonts.add(font_name)
            else:
                missing_fonts.add(font_name)

    system = platform.system()
    if missing_fonts:
        for font in sorted(missing_fonts):
            console.print(f"[yellow][{system}] No font found for '{font}' in {os.path.basename(subtitle_path)}")

    if not found_fonts and not missing_fonts:
        console.print(f"[yellow]No Style definitions found in {os.path.basename(subtitle_path)}")


def detect_subtitle_format(subtitle_path: str) -> str | None:
    """Detects the actual format of a subtitle file using ffprobe and fallbacks."""
    try:
        cmd = [get_ffprobe_path(), "-v", "error", "-show_entries", "stream=codec_name", "-of", "json", subtitle_path]
        logger.debug(f"Running detect_subtitle_format for {os.path.basename(subtitle_path)} with cmd: {' '.join(cmd)}")
        result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
        
        if result.returncode == 0:
            data = json.loads(result.stdout)
            streams = data.get("streams", [])
            if streams:
                codec = streams[0].get("codec_name", "").lower()
                return get_codec_extension(codec.lower(), default="vtt")

        # 2. Fallback: Check binary signatures for formats like stpp in mp4/m4s or raw TTML
        with open(subtitle_path, "rb") as f:
            header = f.read(1024)
            # Check for MP4/M4S atoms or TTML content
            if any(sig in header for sig in [b"styp", b"ftyp", b"moof", b"moov", b"stpp"]):
                return "ttml"

            # Direct check for TTML tags
            if b"<tt" in header and b"http://www.w3.org/ns/ttml" in header:
                return "ttml"

        # 3. Fallback: Manual regex checks for text formats
        with open(subtitle_path, encoding="utf-8", errors="ignore") as f:
            content = f.read(4096).lower()

            if "webvtt" in content:
                return "vtt"

            if "<tt " in content or "<tt>" in content:
                return "ttml"

            if "[script info]" in content or "[v4+ styles]" in content:
                return "ass"

            if "-->" in content:
                return "srt"

    except Exception as e:
        console.print(f"[red]Error detecting subtitle format for {subtitle_path}: {str(e)}")

    return None


def fix_subtitle_extension(subtitle_path: str) -> str:
    """Detects the actual subtitle format and renames the file with the correct extension."""
    detected_format = detect_subtitle_format(subtitle_path)

    if detected_format is None:
        console.print(f"[yellow]    Warning: Could not detect format for {os.path.basename(subtitle_path)}, keeping original extension")
        return subtitle_path

    # Get current extension
    base_name, current_ext = os.path.splitext(subtitle_path)
    current_ext = current_ext.lower().lstrip(".")

    # If extension is already correct, just process fonts for ASS/SSA
    if current_ext == detected_format:
        if detected_format in ["ass", "ssa"]:
            process_subtitle_fonts(subtitle_path)
        elif detected_format == "vtt":
            sanitize_vtt_file(subtitle_path)
        return subtitle_path

    # Create new path with correct extension
    new_path = f"{base_name}.{detected_format}"

    try:
        if os.path.exists(new_path):
            os.remove(new_path)
        os.rename(subtitle_path, new_path)
        console.print(f"[yellow]    - [cyan]Detected [red]{current_ext} [cyan]but it is [red]{detected_format}[cyan], renamed: [green]{os.path.basename(new_path)}")
        return_path = new_path

    except Exception as e:
        console.print(f"[red]    Error renaming subtitle: {str(e)}")
        return_path = subtitle_path

    if detected_format in ["ass", "ssa"]:
        process_subtitle_fonts(return_path)

    elif detected_format == "vtt":
        sanitize_vtt_file(return_path)

    return return_path
