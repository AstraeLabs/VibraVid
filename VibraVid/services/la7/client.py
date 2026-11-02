# 02.11.26

import re

from VibraVid.utils.http_client import create_client, get_userAgent

from ._helpers import (
    _build_stream_urls,
    _extract_player_settings,
    _extract_video_data_block,
)


def get_playback_info(page_url: str) -> dict[str, str | bool | None]:
    """Get playback info from a La7 video page.

    Extracts videoData from the embedded page JSON, then builds DASH/HLS/MP4
    URLs from cf_remote_url + hostnameCdn (Kaltura/iltrovatore format).
    """
    with create_client(
        headers={"user-agent": get_userAgent(), "referer": page_url}
    ) as client:
        response = client.get(page_url)
        response.raise_for_status()
        html = response.text

    video_data = _extract_video_data_block(html)
    player_settings = _extract_player_settings(html)

    if not video_data:
        return {
            "title": page_url.rsplit("/", 1)[-1],
            "mp4_url": None,
            "hls_url": None,
            "dash_url": None,
            "drm": None,
            "hostname_cdn": None,
            "cf_remote_url": "",
            "drm_license_server": "",
            "drm_token_endpoint": "",
            "drm_content_id": "",
            "abilita_drm": False,
        }

    # Extract title
    title = video_data.get("title") or video_data.get("nome") or ""
    if not title:
        title = page_url.rsplit("/", 1)[-1]

    # Detect DRM: videoData.drm flag takes precedence; fall back to playerSettings.abilitaDrm
    video_data_drm = bool(video_data.get("drm"))
    abilita_drm = video_data_drm or player_settings.get("abilitaDrm", False)

    # Choose CDN: DRM content uses hostnameCdnDrm, otherwise hostnameCdn
    if abilita_drm:
        hostname_cdn = player_settings.get("hostnameCdnDrm") or "https://d3iki3eydrtvsa.cloudfront.net"
    else:
        hostname_cdn = (
            player_settings.get("hostnameCdn") or "https://awsvodpkg.iltrovatore.it"
        )

    drm_license_server = player_settings.get("drmLicenseServer", "")
    drm_token_endpoint = player_settings.get("drmTokenEndpoint", "")
    drm_content_id = player_settings.get("drmContentId", "")

    # Determine DRM type
    drm_type = None
    if abilita_drm:
        dl = drm_license_server.lower()
        if "widevine" in dl:
            drm_type = "Widevine"
        elif "playready" in dl:
            drm_type = "PlayReady"
        # videoData.drm=true without a license server still implies Widevine
        if drm_type is None and video_data_drm:
            drm_type = "Widevine"

    # Build stream URLs (DRM content uses cloudfront path format on hostnameCdnDrm)
    stream_urls = _build_stream_urls(
        video_data,
        hostname_cdn,
        hostname_cdn_drm=player_settings.get("hostnameCdnDrm"),
        is_drm=abilita_drm,
    )

    return {
        "title": re.sub(r"\s+", " ", str(title)).strip(),
        "mp4_url": stream_urls.get("mp4"),
        "hls_url": stream_urls.get("hls"),
        "dash_url": stream_urls.get("dash"),
        "drm": drm_type,
        "hostname_cdn": hostname_cdn,
        "cf_remote_url": video_data.get("cf_remote_url", ""),
        "drm_license_server": drm_license_server,
        "drm_token_endpoint": drm_token_endpoint,
        "drm_content_id": drm_content_id,
        "abilita_drm": abilita_drm,
    }
