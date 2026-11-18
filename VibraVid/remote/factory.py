# 12.10.26

from .models import RemoteError, ResolvedMedia


def _enc_override(override):
    """Bring an exported ``hls_enc_override`` back to what ``HLS_Downloader`` builds: the AES key as bytes (it travels as hex)."""
    if not override:
        return override
    restored = dict(override)
    if isinstance(restored.get("key_bytes"), str):
        restored["key_bytes"] = bytes.fromhex(restored["key_bytes"])
    return restored


def build_downloader(
    media: ResolvedMedia,
    output_path: str,
    *,
    key=None,
    license_request_fn=None,
    manifest_refresh_fn=None,
    with_extras: bool = True,
):
    """The HLS/DASH/ISM downloader matching ``media.kind``, fed with the resolved arguments."""
    from VibraVid.core.downloader import DASH_Downloader, HLS_Downloader, ISM_Downloader

    effective_key = key if key else media.key
    common = {
        "manifest_refresh_fn": manifest_refresh_fn,
        "output_path": output_path,
        "license_url": media.license_url,
        "license_headers": media.license_headers,
        "license_certificate": media.license_certificate,
        "license_data": media.license_data,
        "key": effective_key,
        "cookies": media.cookies,
        "other_tracks": media.other_tracks if with_extras else [],
        "chapters": media.chapters if with_extras else [],
        "poster_url": media.poster_url if with_extras else None,
    }
    if media.drm_preference:
        common["drm_preference"] = media.drm_preference

    if media.kind == "hls":
        downloader = HLS_Downloader(
            m3u8_url=media.url,
            m3u8_content=media.manifest_content,
            headers=media.headers or None,
            has_drm=media.has_drm,
            license_request_fn=license_request_fn,
            **common,
        )
        if media.hls_enc_override:
            downloader.hls_enc_override = _enc_override(media.hls_enc_override)
    elif media.kind == "dash":
        downloader = DASH_Downloader(
            mpd_url=media.url,
            mpd_content=media.manifest_content,
            mpd_headers=media.headers or None,
            license_request_fn=license_request_fn,
            **common,
        )
    elif media.kind == "ism":
        if license_request_fn is not None:
            raise RemoteError("ISM manifests cannot use the client CDM through a remote server: enable server_cdm for this server")
        downloader = ISM_Downloader(
            ism_url=media.url,
            ism_content=media.manifest_content,
            headers=media.headers or None,
            **common,
        )
    else:
        raise RemoteError(f"unsupported media kind '{media.kind}'")

    if media.custom_filters:
        downloader.custom_filters = dict(media.custom_filters)  # what the service asked for, as it does after building its downloader
    downloader.display_selected_only = bool(media.display_selected_only)
    return downloader


def download_media(media: ResolvedMedia, output_path: str, *, key=None, license_request_fn=None, manifest_refresh_fn=None):
    """Download one ``ResolvedMedia`` of any kind (mp4 included) and return its ``DownloadResult``."""
    from VibraVid.core.downloader.base import DownloadResult

    if media.kind == "mp4":
        from VibraVid.core.downloader import MP4_Downloader

        result = MP4_Downloader(
            media.url, output_path, headers=media.headers or None, key=key or media.key, chapters=media.chapters, poster_url=media.poster_url
        )
        return DownloadResult.from_raw(result)

    downloader = build_downloader(media, output_path, key=key, license_request_fn=license_request_fn, manifest_refresh_fn=manifest_refresh_fn)
    return DownloadResult.from_raw(downloader.start())
