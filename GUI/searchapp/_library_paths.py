# 06.06.25

import os
import re
from pathlib import Path

from VibraVid.utils import config_manager

VIDEO_EXTS = {".mkv", ".mp4", ".avi", ".webm", ".mov", ".m4v", ".ts"}

_CATEGORY_FOLDER = {
    "serie": ("serie_folder_name", "Serie"),
    "anime": ("anime_folder_name", "Anime"),
    "film": ("movie_folder_name", "Movie"),
    "musica": ("music_folder_name", "Music"),
}

_SXXEXX = re.compile(r"[Ss](\d{1,3})[\s._-]*[Ee](\d{1,4})")
_TARGET = re.compile(r"^(?P<series>.+?)\s*-\s*S(?P<s>\d{1,3})E(?P<e>\d{1,4})(?:\s*-\s*(?P<title>.+))?$")
_TAGS = re.compile(r"\s*[\[(][^\]\)]*[\])]\s*")
_SEASON_DIR = re.compile(r"^S(\d{1,3})$")
_SEASON_DIR_PLEX = re.compile(r"^Season\s+0*(\d{1,3})$", re.IGNORECASE)
_ILLEGAL = re.compile(r'[\\/:*?"<>|]+')


def _safe(name: str) -> str:
    """Clean a name for use as a file or directory name: remove illegal characters and collapse whitespace."""
    name = _ILLEGAL.sub(" ", name or "")
    name = re.sub(r"\s+", " ", name).strip()
    return name.rstrip(" .")


def _norm_cmp(name: str) -> str:
    """Normalize a name for comparison: lowercase, no accents, no punctuation, whitespace collapsed (e.g. "one-piece" == "One Piece")."""
    return re.sub(r"[-_\s]+", " ", name or "").strip().lower()


def titleize_name(name: str) -> str:
    """Title-case a name for display: capitalize first letter of each word, collapse whitespace."""
    cleaned = re.sub(r"[-_]+", " ", name or "")
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    words = [w[:1].upper() + w[1:] if w.islower() else w for w in cleaned.split(" ")]
    return " ".join(words)


def _category_dir(category: str) -> Path:
    """Return the base directory for a category (serie, anime, film, musica)."""
    root = config_manager.config.get("OUTPUT", "root_path", default="Video")
    key, default = _CATEGORY_FOLDER.get(category, _CATEGORY_FOLDER["serie"])
    folder = config_manager.config.get("OUTPUT", key, default=default)
    return Path(os.path.abspath(os.path.join(root, folder)))


def _title_from_filename(stem: str, marker_start: int) -> str | None:
    """Extract a title from a filename stem, given the start index of the SxxExx marker."""
    before = stem[:marker_start].strip(" -_.")
    before = _TAGS.sub(" ", before).strip()
    return before or None


def _parse(stem: str):
    """Parse a filename stem for season, episode, and optional title. Returns (season, episode, title) or None if not found."""
    m = _TARGET.match(stem)
    if m:
        title = m.group("title")
        if title:
            title = _safe(_TAGS.sub(" ", title)) or None
        return int(m.group("s")), int(m.group("e")), title

    m = _SXXEXX.search(stem)
    if not m:
        return None
    return int(m.group(1)), int(m.group(2)), _title_from_filename(stem, m.start())


def _season_dir_num(name: str):
    """Return the season number from a directory name, or None if not found."""
    m = _SEASON_DIR.match(name) or _SEASON_DIR_PLEX.match(name)
    return int(m.group(1)) if m else None
