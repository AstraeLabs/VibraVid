# 16.12.25

from typing import Any

__all__ = [
    "DASH_Downloader",
    "HLS_Downloader",
    "ISM_Downloader",
    "MP4_Downloader",
    "Generic_Downloader",
    "YTDLPDownloader",
    "detect_stream_type",
    "download",
]


def __getattr__(name: str):
    if name == "DASH_Downloader":
        from .dash import DASH_Downloader
        return DASH_Downloader
    if name == "HLS_Downloader":
        from .hls import HLS_Downloader
        return HLS_Downloader
    if name == "ISM_Downloader":
        from .ism import ISM_Downloader
        return ISM_Downloader
    if name == "MP4_Downloader":
        from .mp4 import MP4_Downloader
        return MP4_Downloader
    if name == "Generic_Downloader":
        from ._generic import Generic_Downloader
        return Generic_Downloader
    if name == "YTDLPDownloader":
        from .yt_dlp_downloader import YTDLPDownloader
        return YTDLPDownloader
    if name == "detect_stream_type":
        from .util._detect import detect_stream_type
        return detect_stream_type
    raise AttributeError(name)


def download(
    url: str,
    output_path: str | None = None,
    *,
    headers: dict | None = None,
    key: Any = None,
    license_url: str | None = None,
    license_headers: dict | None = None,
    license_certificate: str | None = None,
    drm_preference: str | None = None,
    cookies: dict | None = None,
    max_segments: int | None = None,
    max_time: Any = None,
    manifest_content: str | None = None,
    manifest_refresh_fn: Any = None,
    other_tracks: list | None = None,
    chapters: list | None = None,
    **extra: Any,
) -> tuple:
    """
    Unified one-shot download entry point. Auto-detects the protocol from *url* and returns `(output_path, need_stop, error)`.
    """
    try:
        from VibraVid.utils import setup_logger

        setup_logger()
    except Exception:
        pass

    from .util._detect import detect_stream_type as _detect_stream_type

    stype = _detect_stream_type(url)

    if stype == "mp4":
        from VibraVid.utils import config_manager

        from .mp4 import MP4_Downloader
        from .util._detect import derive_output_path

        ext = config_manager.config.get("PROCESS", "extension")
        path = output_path or derive_output_path(url, None, ext)
        mp4_extra = {
            k: extra[k] for k in ("referer", "label", "max_percentage", "download_id", "site_name") if k in extra
        }
        return MP4_Downloader(url=url, path=path, headers=headers, key=key, chapters=chapters, **mp4_extra)

    common = dict(
        output_path=output_path,
        license_url=license_url,
        license_headers=license_headers,
        license_certificate=license_certificate,
        key=key,
        cookies=cookies,
        max_segments=max_segments,
        max_time=max_time,
        other_tracks=other_tracks,
        chapters=chapters,
        manifest_refresh_fn=manifest_refresh_fn,
    )
    if drm_preference is not None:
        common["drm_preference"] = drm_preference

    if stype == "hls":
        from .hls import HLS_Downloader

        dl = HLS_Downloader(m3u8_url=url, m3u8_content=manifest_content, headers=headers, **common, **extra)
    elif stype == "ism":
        from .ism import ISM_Downloader

        dl = ISM_Downloader(ism_url=url, ism_content=manifest_content, headers=headers, **common, **extra)
    elif stype == "dash":
        from .dash import DASH_Downloader

        dl = DASH_Downloader(mpd_url=url, mpd_content=manifest_content, mpd_headers=headers, **common, **extra)
    elif stype == "custom":
        from ._generic import Generic_Downloader

        return Generic_Downloader(
            sources=[{"url": url, "protocol": "custom", "headers": headers or {}, "key": key}],
            output_path=output_path,
            max_segments=max_segments,
            max_time=max_time,
            cookies=cookies,
            chapters=chapters,
        ).start()
    else:
        raise ValueError(f"Unsupported stream type detected: {stype!r}")

    return dl.start()
