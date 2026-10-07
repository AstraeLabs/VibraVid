# 05.10.26

import logging
import os
import subprocess
import tempfile
from xml.sax.saxutils import escape

from mutagen.mp4 import MP4

from VibraVid.core.ui.tracker import context_tracker
from VibraVid.setup import get_mkvpropedit_path

logger = logging.getLogger(__name__)

TAG_PREFIX = "[VibraVid]"
TAG_ENCODER = "VibraVid"
_EPISODE_MEDIA_TYPES = ("EPISODE", "TV", "SERIES", "SHOW", "SERIE", "OVA", "ONA", "TV SHORT", "SPECIAL")
_MKV_EXTENSIONS = (".mkv", ".mka", ".mks", ".webm")
_MP4_EXTENSIONS = (".mp4", ".m4v", ".m4a")
_MP4_ATOMS = {
    "title": "\xa9nam",
    "comment": "\xa9cmt",
    "encoder": "\xa9too",
    "show": "tvsh",
}


def build_container_tags() -> dict:
    """Container-level tags for the file being produced, sourced from ``context_tracker``."""
    title = (context_tracker.title or "").strip()
    media_type = (context_tracker.media_type or "").upper().strip()
    season = context_tracker.season or 0
    episode = context_tracker.episode or 0
    episode_name = (context_tracker.episode_name or "").strip()

    is_episode = media_type in _EPISODE_MEDIA_TYPES or season > 0 or episode > 0

    tags: dict = {}
    if title:
        tags["title"] = f"{TAG_PREFIX} {title}"

    if is_episode:
        comment = episode_name or title
        if title:
            tags["show"] = title
        if season:
            tags["season_number"] = str(season)
        if episode:
            tags["episode_sort"] = str(episode)
        if episode_name:
            tags["episode_id"] = episode_name
    else:
        comment = title
    if comment:
        tags["comment"] = f"{TAG_PREFIX} {comment}"

    tags["encoder"] = TAG_ENCODER
    return tags


def _find_mkvpropedit() -> str | None:
    """mkvpropedit ships with MKVToolNix: resolved (PATH, binary dir, download) by the setup checker."""
    try:
        return get_mkvpropedit_path()
    except Exception:
        return None


def _retag_mkv(path: str, tags: dict) -> bool:
    """Write container tags into an already-produced Matroska file in place, without remuxing: uses ``mkvpropedit`` from MKVToolNix. Never raises: a failed retag must not fail the download."""
    tool = _find_mkvpropedit()
    if not tool:
        logger.warning("[retag] mkvpropedit not found (MKVToolNix): MKV tags left untouched")
        return False

    # Global tags are replaced as a whole by --tags, so every non-title tag is written, not only the changed ones.
    simples = "".join(
        f"<Simple><Name>{escape(k.upper())}</Name><String>{escape(v)}</String></Simple>"
        for k, v in tags.items()
        if k != "title"
    )
    xml = f'<?xml version="1.0" encoding="UTF-8"?><Tags><Tag><Targets/>{simples}</Tag></Tags>'

    fd, xml_path = tempfile.mkstemp(suffix=".xml")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(xml)
        cmd = [tool, path, "--edit", "info", "--set", f"title={tags.get('title', '')}", "--tags", f"global:{xml_path}"]
        result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
        if result.returncode > 1:  # 1 = warnings only
            logger.warning(f"[retag] mkvpropedit failed ({result.returncode}): {result.stdout.strip()} {result.stderr.strip()}")
            return False
        return True
    finally:
        try:
            os.remove(xml_path)
        except OSError:
            pass


def _retag_mp4(path: str, tags: dict) -> bool:
    """Write container tags into an already-produced MP4 file in place, without remuxing: uses mutagen. Never raises: a failed retag must not fail the download."""
    audio = MP4(path)
    if audio.tags is None:
        audio.add_tags()
    for key, atom in _MP4_ATOMS.items():
        if key in tags:
            audio.tags[atom] = [tags[key]]
    for key, atom in (("season_number", "tvsn"), ("episode_sort", "tves")):
        if key in tags:
            audio.tags[atom] = [int(tags[key])]
    audio.save()
    return True


def retag_file(path: str, tags: dict | None = None) -> bool:
    """Write container tags into an already-produced file in place, without remuxing: ``mkvpropedit`` for Matroska, mutagen for MP4. Other containers (ts, avi...) are skipped."""
    if not path or not os.path.isfile(path):
        return False

    ext = os.path.splitext(path)[1].lower()
    if ext not in _MKV_EXTENSIONS + _MP4_EXTENSIONS:
        logger.debug(f"[retag] unsupported extension '{ext}', skipping")
        return False

    tags = build_container_tags() if tags is None else tags
    if not tags:
        return False

    try:
        done = _retag_mkv(path, tags) if ext in _MKV_EXTENSIONS else _retag_mp4(path, tags)
    except Exception as e:
        logger.warning(f"[retag] failed for {os.path.basename(path)}: {e}")
        return False

    if done:
        logger.info(f"[retag] tags updated in place: {os.path.basename(path)}")
    return done
