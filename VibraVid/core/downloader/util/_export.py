# 19.10.26

import json
import logging
import re
from pathlib import Path

from VibraVid.core.ui.tracker import context_tracker
from VibraVid.remote.models import ResolvedMedia
from VibraVid.utils import config_manager
from VibraVid.utils.upload.version import __version__

logger = logging.getLogger(__name__)

_written: list[str] = []
_NOT_EXPORTED = (
    "manifest_content", "headers", "cookies", "license_url", "license_headers", "license_data", "license_certificate",
    "drm_preference", "has_drm", "license_proxy", "manifest_refresh", "custom_filters", "display_selected_only",
    "hls_enc_override", "post_process", "chapters", "version", "desc", "exported_at",
)


def get_written_exports() -> list[str]:
    """Paths of every ``--export`` file written so far this run, in order."""
    return list(dict.fromkeys(_written))


def is_synthetic(media: ResolvedMedia) -> bool:
    """True when the manifest only exists as `manifest_content` and `url` is a made-up address (``custom://``, ``amazon://``...)."""
    return bool(media.manifest_content) and not str(media.url or "").lower().startswith(("http://", "https://"))


def track_links(streams: list) -> list[dict]:
    """The selected streams with their direct links: for a manifest with a single file per track (Netflix) this is the mp4 link."""
    tracks = []
    for stream in streams or []:
        if not getattr(stream, "selected", False):
            continue

        media = [seg for seg in stream.segments if seg.seg_type == "media" and seg.url]
        if not media:
            continue

        track = {
            "type": stream.type,
            "language": stream.language if stream.type != "video" else "",
            "resolution": stream.resolution,
            "codecs": stream.codecs,
            "video_range": stream.video_range if stream.type == "video" else "",
            "profile": stream.name,
            "bitrate": stream.bitrate,
            "url": media[0].url,
        }
        tracks.append({k: v for k, v in track.items() if v not in ("", 0, None)})
    return tracks


def primary_link(tracks: list[dict] | None) -> str | None:
    """The link shown as the entry's address: the video track's (the audio one for music), else the first track's."""
    for wanted in ("video", "audio"):
        link = next((t["url"] for t in tracks or [] if t.get("type") == wanted), None)
        if link:
            return link
    return next((t["url"] for t in tracks or [] if t.get("url")), None)


def _default_path(media: ResolvedMedia) -> Path:
    export_dir = Path(config_manager.base_path or ".") / ".cache" / "export"
    parts = [media.site or "unknown", media.media_type or "Film", media.title or "output"]
    safe_name = re.sub(r'[\\/:*?"<>|]+', "_", "_".join(p for p in parts if p))
    safe_name = re.sub(r"\s+", "_", safe_name).strip("_") or "output"
    return export_dir / f"{safe_name}.json"


def _download_command(media: ResolvedMedia) -> str:
    """A ready-to-run ``manual.py --down`` command that downloads *media* (the keys are in the command, no CDM needed)."""
    name = media.title or "output"
    if media.season or media.episode:
        name = f"{name} S{media.season}_E{media.episode}" + (f" {media.episode_name}" if media.episode_name else "")
    name = re.sub(r'[\\/:*?"<>|]+', "_", name).strip() or "output"

    keys = [media.key] if isinstance(media.key, str) else (media.key or [])
    parts = [f"python manual.py --down '{media.url}'"]
    parts += [f"--key {k}" for k in keys if k]
    parts.append(f"-o '{name}'")
    return " ".join(parts)


def write_export(media: ResolvedMedia, file: str | None = None) -> str | None:
    """Add *media* to the ``--export`` JSON array (*file*, or ``.cache/export/<site>_<type>_<title>.json`` when empty)."""
    try:
        explicit = bool(file and file.strip())
        path = Path(file.strip()) if explicit else _default_path(media)
        path.parent.mkdir(parents=True, exist_ok=True)

        media.vibravid_version = __version__

        entries: list = []
        if (str(path) in _written or not explicit) and path.exists():
            try:
                loaded = json.loads(path.read_text(encoding="utf-8"))
                entries = loaded if isinstance(loaded, list) else []
            except Exception:
                entries = []
            
            if not explicit:
                entries = [e for e in entries if (e.get("season"), e.get("episode")) != (media.season, media.episode)]

        wire = media.to_wire()
        entry = {k: v for k, v in wire.items() if k not in _NOT_EXPORTED and not (k == "tracks" and v is None)}
        if is_synthetic(media):
            entry["url"] = primary_link(media.tracks) or media.url
        else:
            entry = {"cmd": _download_command(media), **entry}

        if context_tracker.export_minimal:
            for key in ("url", "cmd", "tracks"):
                entry.pop(key, None)

        entries.append(entry)
        path.write_text(json.dumps(entries, indent=4, ensure_ascii=False), encoding="utf-8")
        _written.append(str(path))
        return str(path)
    except Exception as exc:
        logger.error(f"Could not write the export file: {exc}")
        return None


def read_export(path: str) -> list[ResolvedMedia]:
    """Load an ``--export`` file (a JSON array, or one object) as ``ResolvedMedia`` entries. Raises ``ValueError`` with a readable message."""
    file = Path(path.strip())
    if not file.is_file():
        raise ValueError(f"file not found: {file}")

    try:
        data = json.loads(file.read_text(encoding="utf-8"))
    except Exception as exc:
        raise ValueError(f"could not parse {file}: {exc}") from exc

    if isinstance(data, dict):
        data = [data]
    if not isinstance(data, list):
        raise ValueError(f"{file} does not contain a JSON array of entries")

    entries = []
    for i, item in enumerate(data, 1):
        if not isinstance(item, dict) or not item.get("kind") or not item.get("url"):
            raise ValueError(f"entry {i} of {file} has no 'kind'/'url': is it an --export file?")
        entries.append(ResolvedMedia.from_wire(item))
    return entries
