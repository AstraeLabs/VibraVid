# 17.01.25

import logging
import os
import platform
import subprocess
from dataclasses import dataclass

from rich.console import Console

console = Console()
logger = logging.getLogger(__name__)

FONT_FILE_EXTENSIONS = (".ttf", ".otf", ".ttc", ".dfont")
_STYLE_WORDS = {"bold", "italic", "oblique", "regular", "bolditalic", "boldoblique"}


@dataclass(frozen=True)
class FontFace:
    """One installed font face: lower-cased family name, weight/slant flags and the file that holds it."""
    family: str
    bold: bool
    italic: bool
    path: str


def parse_face_name(name: str) -> tuple[str, bool, bool]:
    """Split "Arial Bold Italic" into ("arial", True, True): trailing style words become flags, the rest is the lower-cased family."""
    tokens = name.split()
    bold = italic = False
    while len(tokens) > 1 and tokens[-1].lower() in _STYLE_WORDS:
        word = tokens.pop().lower()
        bold = bold or "bold" in word
        italic = italic or "italic" in word or "oblique" in word
    return " ".join(tokens).lower(), bold, italic


def face_from_filename(path: str) -> FontFace:
    """Best-effort face for a font file whose real family name can't be read (no font parser): derived from its file name."""
    stem = os.path.splitext(os.path.basename(path))[0].replace("_", " ").replace("-", " ")
    family, bold, italic = parse_face_name(stem)
    return FontFace(family, bold, italic, path)


class FontManager:
    def __init__(self):
        self._fonts = None
        self._faces = None

    def get_installed_fonts(self) -> list[str]:
        """Get list of installed fonts on the system (Windows, Linux, macOS)."""
        if self._fonts is None:
            self._fonts = self._get_fonts()
        return self._fonts

    def get_font_faces(self) -> list[FontFace]:
        """Get every installed font face together with the file that holds it (Windows, Linux, macOS)."""
        if self._faces is None:
            try:
                self._faces = self._get_font_faces()
            except Exception as e:
                logger.warning(f"Could not list installed font files: {e}")
                self._faces = []
        return self._faces

    def _get_font_faces(self) -> list[FontFace]:
        system = platform.system().lower()
        if system == "windows":
            return self._get_windows_font_faces()
        if system == "darwin":
            return self._get_scanned_font_faces(
                [
                    "/Library/Fonts",
                    "/System/Library/Fonts",
                    os.path.expanduser("~/Library/Fonts"),
                    "/Network/Library/Fonts",
                ]
            )
        if system == "linux":
            return self._get_linux_font_faces()
        return []

    def _get_windows_font_faces(self) -> list[FontFace]:
        """Read name -> file straight from the registry font tables (the file name is the value data)."""
        import winreg

        faces = []
        font_dir = os.path.join(os.environ.get("WINDIR", "C:\\Windows"), "Fonts")
        reg_paths = [
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\Fonts"),
            (winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows NT\CurrentVersion\Fonts"),
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Fonts"),
        ]

        for root, path in reg_paths:
            try:
                key = winreg.OpenKey(root, path)
            except OSError:
                continue

            i = 0
            while True:
                try:
                    full_name, data, _ = winreg.EnumValue(key, i)
                except OSError:
                    break
                i += 1

                if not isinstance(data, str) or not data.lower().endswith(FONT_FILE_EXTENSIONS):
                    continue

                file_path = data if os.path.isabs(data) else os.path.join(font_dir, data)
                base = full_name.rsplit(" (", 1)[0] if "(" in full_name else full_name

                # A .ttc entry lists every face it holds: "MS Gothic & MS UI Gothic & MS PGothic"
                for part in base.split(" & "):
                    family, bold, italic = parse_face_name(part.strip())
                    if family:
                        faces.append(FontFace(family, bold, italic, file_path))
            winreg.CloseKey(key)

        return faces

    def _get_linux_font_faces(self) -> list[FontFace]:
        faces = []
        try:
            result = subprocess.run(
                ["fc-list", "--format", "%{file}\\t%{family}\\t%{style}\\n"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode == 0:
                for line in result.stdout.splitlines():
                    parts = line.split("\t")
                    if len(parts) < 3 or not parts[0].lower().endswith(FONT_FILE_EXTENSIONS):
                        continue

                    style = parts[2].lower()
                    bold = "bold" in style
                    italic = "italic" in style or "oblique" in style
                    for family in parts[1].split(","):
                        if family.strip():
                            faces.append(FontFace(family.strip().lower(), bold, italic, parts[0]))
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
            pass

        if faces:
            return faces

        return self._get_scanned_font_faces(
            [
                "/usr/share/fonts",
                "/usr/local/share/fonts",
                os.path.expanduser("~/.fonts"),
                os.path.expanduser("~/.local/share/fonts"),
            ]
        )

    @staticmethod
    def _get_scanned_font_faces(font_dirs: list[str]) -> list[FontFace]:
        """Directory-scan fallback: the family and style are guessed from each file name."""
        faces = []
        for font_dir in font_dirs:
            if not os.path.isdir(font_dir):
                continue
            for root, _dirs, files in os.walk(font_dir):
                for f in files:
                    if f.lower().endswith(FONT_FILE_EXTENSIONS):
                        faces.append(face_from_filename(os.path.join(root, f)))
        return faces

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
