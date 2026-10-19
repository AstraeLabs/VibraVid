# 12.10.26

import base64
import json
import zlib
from dataclasses import asdict, dataclass, field, fields
from typing import Any

COMPRESS_THRESHOLD = 2048


def pack_text(text: str | None) -> dict | None:
    """Wrap a (possibly large) text blob so it travels as JSON: ``{"z": bool, "d": str}``."""
    if text is None:
        return None
    raw = text.encode("utf-8")
    if len(raw) >= COMPRESS_THRESHOLD:
        return {"z": True, "d": base64.b64encode(zlib.compress(raw, 6)).decode("ascii")}
    return {"z": False, "d": text}


def unpack_text(blob: dict | str | None) -> str | None:
    if blob is None:
        return None
    if isinstance(blob, str):
        return blob
    if blob.get("z"):
        return zlib.decompress(base64.b64decode(blob["d"])).decode("utf-8")
    return blob.get("d")


def _jsonable(value: Any) -> Any:
    """Round-trip through JSON so anything non-serializable fails here, on the server, with a clear error."""
    return json.loads(json.dumps(value, default=str))


@dataclass
class ResolvedMedia:
    """Everything a downloader needs, captured on the server in place of running ``start()``."""
    kind: str  # hls | dash | ism | mp4
    url: str
    manifest_content: str | None = None
    headers: dict = field(default_factory=dict)
    cookies: dict = field(default_factory=dict)
    license_url: str | None = None
    license_headers: dict | None = None
    license_data: Any = None
    license_certificate: str | None = None
    key: Any = None
    drm_preference: str | None = None
    has_drm: bool = False
    license_proxy: bool = False
    manifest_refresh: bool = False
    other_tracks: list = field(default_factory=list)
    custom_filters: dict | None = None
    display_selected_only: bool = False
    hls_enc_override: str | None = None
    post_process: dict | None = None
    chapters: list | None = None
    poster_url: str | None = None
    title: str | None = None
    media_type: str | None = None
    year: Any = None
    season: int = 0
    episode: int = 0
    episode_name: str | None = None
    
    # --export / --import only (the remote wire ignores them)
    version: int = 1
    site: str | None = None
    desc: str | None = None
    max_segments: Any = None
    max_time: Any = None
    exported_at: str | None = None
    vibravid_version: str | None = None

    def to_wire(self) -> dict:
        data = _jsonable(asdict(self))
        data["manifest_content"] = pack_text(self.manifest_content)
        return data

    @classmethod
    def from_wire(cls, data: dict) -> "ResolvedMedia":
        known = {f.name for f in fields(cls)}
        clean = {k: v for k, v in data.items() if k in known}
        clean["manifest_content"] = unpack_text(clean.get("manifest_content"))
        return cls(**clean)


class RemoteError(RuntimeError):
    """A remote call failed; ``status`` carries the HTTP status when there is one."""
    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status
        self.wanted_cache: list[str] = []
        self.wanted_cache: list[str] = []
        self.wanted: list[str] = []
