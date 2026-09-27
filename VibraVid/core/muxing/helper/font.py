# 17.01.25

import logging
import os
import platform
import subprocess

from rich.console import Console

console = Console()
logger = logging.getLogger(__name__)

class FontManager:
    def __init__(self):
        self._fonts = None

    def get_installed_fonts(self) -> list[str]:
        """Get list of installed fonts on the system (Windows, Linux, macOS)."""
        if self._fonts is None:
            self._fonts = self._get_fonts()
        return self._fonts

    def _get_fonts(self) -> list[str]:
        system = platform.system().lower()
        fonts = []

        try:
            if system == "windows":
                fonts = self._get_windows_fonts()
            elif system == "darwin":  # macOS
                fonts = self._get_macos_fonts()
            elif system == "linux":
                fonts = self._get_linux_fonts()
            else:
                console.log(f"[yellow]Warning: Unsupported OS '{system}', using empty font list[/yellow]")
        except Exception as e:
            console.log(f"[red]Error retrieving system fonts: {e}[/red]")

        return sorted(list(set(fonts)))

    def _get_windows_fonts(self) -> list[str]:
        """Get installed fonts on Windows."""
        import winreg

        fonts = []

        # Directory listing fallback
        font_dir = os.path.join(os.environ.get("WINDIR", "C:\\Windows"), "Fonts")
        if os.path.exists(font_dir):
            try:
                for f in os.listdir(font_dir):
                    name = os.path.splitext(f)[0]
                    fonts.append(name.lower())
            except Exception:
                pass

        # Registry paths for system and user fonts
        reg_paths = [
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\Fonts"),
            (winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows NT\CurrentVersion\Fonts"),
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Fonts"),
        ]

        for root, path in reg_paths:
            try:
                key = winreg.OpenKey(root, path)
                i = 0
                while True:
                    try:
                        full_name, _, _ = winreg.EnumValue(key, i)

                        # Remove (TrueType), (OpenType) etc.
                        name = full_name
                        if "(" in full_name:
                            name = full_name.rsplit(" (", 1)[0]

                        fonts.append(name.lower().strip())
                        i += 1
                    except OSError:
                        break
                winreg.CloseKey(key)
            except Exception:
                continue

        return fonts

    def _get_macos_fonts(self) -> list[str]:
        """Get installed fonts on macOS."""
        fonts = []

        # macOS font directories
        font_dirs = [
            "/Library/Fonts",  # System fonts
            "/System/Library/Fonts",  # System fonts (core)
            os.path.expanduser("~/Library/Fonts"),  # User fonts
            "/Network/Library/Fonts",  # Network fonts
        ]

        for font_dir in font_dirs:
            if os.path.exists(font_dir):
                try:
                    for _root, _dirs, files in os.walk(font_dir):
                        for f in files:
                            if f.lower().endswith((".ttf", ".otf", ".ttc", ".dfont")):
                                # Remove extension and common suffixes
                                name = os.path.splitext(f)[0]

                                # Remove common suffixes like "Bold", "Italic", etc.
                                name = name.replace("-Bold", "").replace("-Italic", "")
                                name = name.replace("Bold", "").replace("Italic", "")
                                name = name.replace("-Regular", "").replace("Regular", "")
                                fonts.append(name.lower().strip())
                except Exception:
                    pass

        return fonts

    def _get_linux_fonts(self) -> list[str]:
        """Get installed fonts on Linux."""
        fonts = []

        # Try using fc-list (fontconfig)
        try:
            logger.info(f"Running fc-list to get installed fonts with cmd: {'fc-list : family'}")
            result = subprocess.run(["fc-list", ":", "family"], capture_output=True, text=True, timeout=5)
            if result.returncode == 0:
                for line in result.stdout.splitlines():
                    # fc-list returns multiple names separated by comma
                    for font_name in line.split(","):
                        fonts.append(font_name.strip().lower())

        except (subprocess.TimeoutExpired, FileNotFoundError, Exception):
            pass

        # Fallback: scan common Linux font directories
        font_dirs = [
            "/usr/share/fonts",
            "/usr/local/share/fonts",
            os.path.expanduser("~/.fonts"),
            os.path.expanduser("~/.local/share/fonts"),
        ]

        for font_dir in font_dirs:
            if os.path.exists(font_dir):
                try:
                    for _root, _dirs, files in os.walk(font_dir):
                        for f in files:
                            if f.lower().endswith((".ttf", ".otf", ".ttc", ".pcf", ".bdf")):
                                name = os.path.splitext(f)[0]

                                # Clean up common suffixes
                                name = name.replace("-Bold", "").replace("-Italic", "")
                                name = name.replace("Bold", "").replace("Italic", "")
                                name = name.replace("-Regular", "").replace("Regular", "")
                                fonts.append(name.lower().strip())
                except Exception:
                    pass

        return fonts
