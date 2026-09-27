# 12.04.26

import logging
import re
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from VibraVid.core.muxing.hybrid import probe_media_file
from VibraVid.core.velora.downloader import MediaDownloader as ManualMediaDownloader

logger = logging.getLogger(__name__)


def _safe_token(value: str, default: str = "track") -> str:
    cleaned = re.sub(r"[^\w\-]+", "_", str(value or "")).strip("_")
    return cleaned or default


def _split_track_type(track_type: str) -> tuple[str, str]:
    raw = (track_type or "").strip().lower()
    if ":" in raw:
        kind, tag = raw.split(":", 1)
    else:
        kind, tag = raw, ""
    if kind in ("sub", "subtitle", "subtitles"):
        kind = "subtitle"
    elif kind in ("aud", "audio"):
        kind = "audio"
    elif kind in ("vid", "video"):
        kind = "video"
    return kind, tag


def _normalize_keys(keys: Iterable[str] | None) -> list[str]:
    if not keys:
        return []
    return [key.strip() for key in keys if isinstance(key, str) and key.strip()]


def split_other_tracks(
    other_tracks: list[dict[str, Any]] | None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    video_tracks: list[dict[str, Any]] = []
    audio_tracks: list[dict[str, Any]] = []
    subtitle_tracks: list[dict[str, Any]] = []

    for raw_track in other_tracks or []:
        track = dict(raw_track or {})
        kind, _tag = _split_track_type(track.get("type", ""))
        if kind == "video":
            video_tracks.append(track)
        elif kind == "audio":
            audio_tracks.append(track)
        elif kind == "subtitle":
            subtitle_tracks.append(track)

    return video_tracks, audio_tracks, subtitle_tracks


def _kind_to_filters(_kind: str, _tag: str, quality: str = "worst") -> dict[str, str]:
    if _tag == "dv":
        q = (quality or "worst").strip().lower()

        if q in ("best", "worst"):
            video_filter = f"c=dvh:f={q}"
        elif re.match(r"^\d+$", q):
            video_filter = f"r={q}:c=dvh:f=worst"
        elif "|" in q:
            res_part, _, fallback = q.partition("|")
            if re.match(r"^\d+$", res_part.strip()):
                fallback = fallback.strip() or "worst"
                video_filter = f"r={res_part.strip()}:c=dvh:f={fallback}"
            else:
                video_filter = quality
        else:
            video_filter = quality

        return {"video": video_filter, "audio": "false", "subtitle": "false"}

    return {"video": quality or "worst", "audio": "false", "subtitle": "false"}


def _track_label(track: dict[str, Any], kind: str, tag: str) -> str:
    if kind == "video":
        video_tag = _safe_token(tag or track.get("label") or "video", "video")
        return f"Vid {video_tag.upper()}"

    if kind == "audio":
        lang = track.get("language") or tag or "audio"
        return f"Aud {lang}"

    if kind == "subtitle":
        lang = track.get("language") or tag or "sub"
        return f"Sub {lang}"

    return f"Track {kind or 'other'}"


def _pick_status_entry(status: dict[str, Any], kind: str) -> dict[str, Any] | None:
    if status.get("video"):
        return status.get("video")
    if kind == "video":
        return status.get("video")
    if kind == "audio":
        audios = status.get("audios") or []
        return audios[0] if audios else None
    if kind == "subtitle":
        subtitles = status.get("subtitles") or status.get("external_subtitles") or []
        return subtitles[0] if subtitles else None

    return None


def download_other_tracks(
    other_tracks: list[dict[str, Any]] | None,
    output_dir: Path,
    filename: str,
    keys: Iterable[str] | None = None,
    headers: dict[str, str] | None = None,
    cookies: dict[str, str] | None = None,
    max_segments: int | None = None,
    max_time: float | None = None,
    show_progress: bool = True,
) -> list[dict[str, Any]]:
    """Download extra video/audio/subtitle tracks with the manual backend."""
    results: list[dict[str, Any]] = []
    if not other_tracks:
        return results

    normalized_keys = _normalize_keys(keys)
    output_dir.mkdir(parents=True, exist_ok=True)
    hybrid_tmp = output_dir / f"{filename}_hybrid_tmp"
    hybrid_tmp.mkdir(parents=True, exist_ok=True)

    for index, raw_track in enumerate(other_tracks, 1):
        track = dict(raw_track or {})
        kind, tag = _split_track_type(track.get("type", ""))
        if kind not in {"video", "audio"}:
            logger.info(f"Skipping unsupported other_track type: {track.get('type')}")
            continue

        url = track.get("url")
        if not url:
            logger.warning(f"Skipping other_track without url: {track}")
            continue

        label_token = _safe_token(tag or track.get("language") or str(index))
        track_filename = f"{filename}.{_safe_token(kind)}.{label_token}"
        track_dir = hybrid_tmp / f"{kind}_{index}"
        track_dir.mkdir(parents=True, exist_ok=True)

        label = _track_label(track, kind, tag)
        logger.info(f"Downloading other track with manual backend: {label} ({url})")

        downloader = ManualMediaDownloader(
            url=url,
            output_dir=str(track_dir),
            filename=track_filename,
            headers=headers or {},
            key=normalized_keys,
            cookies=cookies or {},
            download_id=None,
            site_name=None,
            max_segments=max_segments,
            max_time=max_time,
        )
        quality = str(track.get("quality") or "worst").strip() or "worst"
        downloader.custom_filters = {**_kind_to_filters(kind, tag, quality), "dv_auto": False}

        try:
            downloader.parse_stream(show_table=False)
            if kind == "video" and tag:
                # Set resolution to the tag label so _prepare_labels shows "Vid DV"
                tag_up = _safe_token(tag, tag).upper()
                for s in downloader.streams:
                    if s.selected and s.type == "video" and not s.is_external:
                        s.resolution = tag_up
                        break

            elif kind == "audio":
                lang = track.get("language") or tag or "und"

                # computes "Aud en" instead of "Vid main".
                for s in downloader.streams:
                    if s.selected and s.type == "video" and not s.is_external:
                        s.type = "audio"
                        if lang and lang != "und":
                            s.language = lang

            result = downloader.start_download(show_progress=show_progress)
        except Exception as exc:
            logger.error(f"Other track download failed ({label}): {exc}", exc_info=True)
            continue

        if result.get("error") == "cancelled":
            logger.info(f"Other track download cancelled ({label})")
            continue

        downloaded = _pick_status_entry(result, kind)
        if not downloaded:
            logger.error(f"Output not found for other track after download: {track_filename}")
            continue

        out_path_str = str(downloaded.get("path") or "").strip()
        if not out_path_str:
            logger.error(f"Output path missing for other track: {track_filename}")
            continue

        out_path = Path(out_path_str)
        if not out_path.exists():
            logger.error(f"Output path missing for other track: {out_path}")
            continue

        size = out_path.stat().st_size
        probe = probe_media_file(str(out_path)) if kind == "video" else {}

        entry: dict[str, Any] = {
            "path": str(out_path),
            "url": url,
            "type": track.get("type", kind),
            "kind": kind,
            "tag": tag,
            "language": track.get("language") or tag or "und",
            "name": track.get("name") or tag or kind,
            "size": size,
            "probe": probe,
        }
        if probe:
            entry.update(probe)

        results.append(entry)

        logger.info(f"Downloaded other track {label} -> {out_path.name}")
        if probe:
            logger.info(f"Probe other track {label}: hdr={probe.get('hdr')} dolby_vision={probe.get('dolby_vision')} video_codec={probe.get('video_codec')} base_info={probe.get('base_info')}")

    return results
