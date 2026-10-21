# 03.08.26

import json
import logging

from rich.console import Console

from VibraVid.core.downloader.dash import DASH_Downloader
from VibraVid.core.drm.system import DRMType
from VibraVid.core.manifest.mpd import DashParser
from VibraVid.core.manifest.stream import Stream

console = Console()
logger = logging.getLogger(__name__)


class _KeyResolver:
    def __init__(self, license_url: str):
        self._dl = DASH_Downloader(license_url=license_url)

    def resolve(self, mpd_url: str, headers: dict, license_headers: dict, license_request_fn=None) -> tuple[list[Stream], list[str]]:
        """Parse *mpd_url* and return (streams, ["kid:key", ...]). Empty keys if unprotected/unresolved."""
        parser = DashParser(mpd_url, headers=headers)
        if not parser.fetch_manifest():
            return [], []

        streams = parser.parse_streams()
        drm_psshs = self._dl._collect_drm_from_streams(streams, check_selected=False)

        if not drm_psshs[DRMType.WIDEVINE] and not drm_psshs[DRMType.PLAYREADY]:
            return streams, []

        keys = self._dl.drm_manager.get_wv_keys(
            drm_psshs[DRMType.WIDEVINE],
            self._dl.license_url,
            license_certificate=self._dl.license_certificate,
            headers=license_headers,
            key=None,
            license_request_fn=license_request_fn,
        ) if drm_psshs[DRMType.WIDEVINE] else None

        return streams, list(keys.get_keys_list()) if keys else []


def _best(streams: list[Stream], stype: str) -> Stream | None:
    """Highest-bitrate stream of *stype* ("video"/"audio"), or None."""
    candidates = [s for s in streams if s.type == stype]
    if not candidates:
        return None
    return max(candidates, key=lambda s: (s.avg_bitrate or s.bitrate or 0))


def _videos(streams: list[Stream]) -> list[Stream]:
    """Video renditions of *streams*, best first."""
    return sorted((s for s in streams if s.type == "video"), key=lambda s: (s.avg_bitrate or s.bitrate or 0), reverse=True)


def _video_track_id(stream: Stream, existing: list[dict]) -> str:
    """Unique, readable id such as ``video-1080p-4861k`` (the manifest parser keys tracks by id)."""
    base = f"video-{stream.height or 0}p-{(stream.avg_bitrate or stream.bitrate or 0) // 1000}k"
    taken = {t["id"] for t in existing}
    candidate, n = base, 2
    while candidate in taken:
        candidate = f"{base}-{n}"
        n += 1
    return candidate


def _serialize_media_track(stream: Stream, track_id: str) -> dict:
    """Turn an already-resolved DASH ``Stream`` (real segment URLs, real KID) into a ``vibravid_manifest`` track dict."""
    track: dict = {
        "type": stream.type,
        "id": track_id,
        "codecs": stream.codecs,
        "bitrate": stream.bitrate,
        "avg_bitrate": stream.avg_bitrate,
        "duration": stream.duration,
        "format": stream.format,
    }

    if stream.type == "video":
        track["width"] = stream.width
        track["height"] = stream.height
        track["fps"] = stream.fps
        track["video_range"] = stream.video_range
    else:
        track["language"] = stream.language

    if stream.type == "audio":
        track["channels"] = stream.channels
        track["sample_rate"] = stream.sample_rate
        track["default"] = stream.default
        track["name"] = stream.language
        track["label"] = stream.language

    init_seg = next((s for s in stream.segments if s.seg_type == "init"), None)
    if init_seg is not None:
        init_entry: dict = {"url": init_seg.url}
        if init_seg.byte_range:
            init_entry["range"] = init_seg.byte_range
        track["init"] = init_entry

    media_segments = [s for s in stream.segments if s.seg_type != "init"]
    track["segments"] = {
        "list": [
            {
                "url": seg.url,
                "size": seg.size,
                "duration": seg.duration,
                **({"range": seg.byte_range} if seg.byte_range else {}),
            }
            for seg in media_segments
        ]
    }

    kids = stream.drm.get_all_kids() if stream.drm else []
    if kids:
        track["drm"] = {"kid": kids[0], "type": DRMType.WIDEVINE}

    return track


def _serialize_subtitle_track(sub: dict, track_id: str) -> dict:
    """Crunchyroll subtitles are plain (unencrypted) single-file downloads, not DASH segments."""
    return {
        "type": "subtitle",
        "id": track_id,
        "language": sub.get("language") or "und",
        "name": sub.get("label") or sub.get("name") or sub.get("language") or "Subtitle",
        "label": sub.get("label") or sub.get("name") or sub.get("language") or "Subtitle",
        "format": sub.get("format") or "ass",
        "is_cc": bool(sub.get("closed_caption")),
        "segments": {"list": [{"url": sub.get("url")}]},
    }


def build_unified_manifest(
    *,
    main_locale: str,
    main_mpd_url: str,
    main_mpd_headers: dict,
    main_license_headers: dict,
    extra_locales: list[dict],
    subtitles: list[dict],
    license_url: str,
    base_url: str = "",
    main_content_id: str | None = None,
    license_request_factory=None,
) -> tuple[str, list[str]] | tuple[None, None]:
    """Build a single JSON manifest containing all video/audio/subtitle tracks, and a list of KID:key strings for the license request."""
    def _license_fn(content_id):
        return license_request_factory(content_id) if license_request_factory and content_id else None

    resolver = _KeyResolver(license_url)
    merged_keys: dict[str, str] = {}
    tracks: list[dict] = []

    def _merge(new_keys: list[str]) -> None:
        for entry in new_keys:
            kid = entry.split(":", 1)[0].strip().lower()
            merged_keys.setdefault(kid, entry)

    main_streams, main_keys = resolver.resolve(main_mpd_url, main_mpd_headers, main_license_headers, _license_fn(main_content_id))
    if not main_streams:
        console.print(f"[red]Could not resolve main locale {main_locale}, aborting manifest build.")
        return None, None
    _merge(main_keys)

    # Every rendition goes in, so select_video / --select-video can pick among them (the selector falls back to the nearest one)
    for video in _videos(main_streams):
        tracks.append(_serialize_media_track(video, _video_track_id(video, tracks)))

    main_audio = _best(main_streams, "audio")
    if main_audio is not None:
        main_audio.language = main_locale
        tracks.append(_serialize_media_track(main_audio, f"audio-{main_locale}"))

    for spec in extra_locales:
        locale = spec.get("locale")
        console.print(f"[dim]Resolving audio ({locale}) ...")
        try:
            streams, keys = resolver.resolve(spec.get("mpd_url"), spec.get("headers"), spec.get("license_headers"), _license_fn(spec.get("content_id")))
        except Exception as e:
            console.print(f"[yellow]Could not resolve audio {locale}: {e}")
            continue

        audio = _best(streams, "audio")
        if audio is None:
            console.print(f"[yellow]No audio representation found for {locale}, skipping")
            continue

        audio.language = locale
        tracks.append(_serialize_media_track(audio, f"audio-{locale}"))
        _merge(keys)

    for idx, sub in enumerate(subtitles or []):
        if not isinstance(sub, dict) or not sub.get("url"):
            continue
        suffix = "-cc" if sub.get("closed_caption") else ""
        tracks.append(_serialize_subtitle_track(sub, f"sub-{sub.get('language') or idx}{suffix}-{idx}"))

    if not tracks:
        return None, None

    manifest = {
        "vibravid_manifest": True,
        "base_url": base_url,
        "tracks": tracks,
    }
    return json.dumps(manifest), list(merged_keys.values())
