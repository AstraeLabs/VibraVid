# 19.10.26

import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path

from VibraVid.remote.models import ResolvedMedia
from VibraVid.utils import config_manager
from VibraVid.utils.upload.version import __version__

logger = logging.getLogger(__name__)

_written: list[str] = []


def get_written_exports() -> list[str]:
    """Paths of every ``--export`` file written so far this run, in order."""
    return list(dict.fromkeys(_written))


def _default_path(media: ResolvedMedia) -> Path:
    export_dir = Path(config_manager.base_path or ".") / ".cache" / "export"
    parts = [media.site or "unknown", media.media_type or "Film", media.title or "output"]
    safe_name = re.sub(r'[\\/:*?"<>|]+', "_", "_".join(p for p in parts if p))
    safe_name = re.sub(r"\s+", "_", safe_name).strip("_") or "output"
    return export_dir / f"{safe_name}.json"


def write_export(media: ResolvedMedia, file: str | None = None) -> str | None:
    """Add *media* to the ``--export`` JSON array (*file*, or ``.cache/export/<site>_<type>_<title>.json`` when empty)."""
    try:
        path = Path(file.strip()) if file and file.strip() else _default_path(media)
        path.parent.mkdir(parents=True, exist_ok=True)

        media.exported_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        media.vibravid_version = __version__

        entries: list = []
        if str(path) in _written and path.exists():
            try:
                loaded = json.loads(path.read_text(encoding="utf-8"))
                entries = loaded if isinstance(loaded, list) else []
            except Exception:
                entries = []

        entries.append(media.to_wire())
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
