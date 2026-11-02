# 02.11.26

import base64
import json
import re
import unicodedata

from VibraVid.utils.http_client import create_client, get_userAgent

BASE_URL = "https://www.la7.it"

# GraphQL persisted query hashes (extracted from la7.it JS chunks)
GQL_HASHES = {
    "allPrograms": "f7034d55158fc906",
}


def _serialize_vars(obj):
    if obj is None or not isinstance(obj, dict):
        return json.dumps(obj)
    items = []
    for k, v in sorted(obj.items()):
        if v is None:
            continue
        items.append(f'{json.dumps(k)}:{_serialize_vars(v)}')
    return "{" + ",".join(items) + "}"


def _urlsafe_b64_encode(s):
    std = base64.b64encode(s.encode("utf-8")).decode("ascii")
    return std.replace("+", "-").replace("/", "_").rstrip("=")


def _normalize(value: str) -> str:
    value = unicodedata.normalize("NFKD", value or "")
    return "".join(char for char in value if not unicodedata.combining(char)).lower()


def _gql_request(query_name: str, variables: dict):
    """Make a GET request to La7 GraphQL persisted-query API."""
    hash_id = GQL_HASHES.get(query_name)
    if not hash_id:
        return None
    serialized = _serialize_vars(variables)
    b64 = _urlsafe_b64_encode(serialized)
    url = f"{BASE_URL}/api/gql/{hash_id}/{b64}"
    with create_client(
        headers={
            "user-agent": get_userAgent(),
            "accept": "application/json",
            "referer": BASE_URL + "/",
        }
    ) as client:
        response = client.get(url, timeout=30)
        response.raise_for_status()
        return response.json()


def _extract_video_data_block(html: str) -> dict | None:
    """Extract key video fields from the videoData block in a La7 video page HTML.

    Uses regex instead of full JSON parsing because the embedded JSON has
    escaped quotes and may contain invalid JSON characters.
    """
    pos = html.find("videoData")
    if pos == -1:
        return None
    colon = html.find(":", pos)
    start = html.find("{", colon)
    if start == -1:
        return None
    depth = 1
    p = start + 1
    end = -1
    while p < len(html) and depth > 0:
        if html[p] == "{":
            depth += 1
        elif html[p] == "}":
            depth -= 1
            if depth == 0:
                end = p
                break
        p += 1
    if end == -1:
        return None
    block = html[start : end + 1]
    block = block.replace('\\"', '"').replace('\\\\', '\\')

    result = {}
    for field in ["title", "nome"]:
        m = re.search(r'"' + field + r'"\s*:\s*"([^"]+)"', block)
        if m:
            result[field] = m.group(1)
            break

    m = re.search(r'"cf_remote_url"\s*:\s*"([^"]+)"', block)
    if m:
        result["cf_remote_url"] = m.group(1)

    m = re.search(r'"drm"\s*:\s*(true|false)', block)
    if m:
        result["drm"] = m.group(1) == "true"

    return result if result else None


def _extract_player_settings(html: str) -> dict:
    """Extract playerSettings (abilitaDrm, hostnameCdn, hostnameCdnDrm, drmLicenseServer, drmTokenEndpoint) from page HTML."""
    result = {}

    # Player settings appear as escaped JSON within a JS string: \"key\":\"value\"
    # Unescape to make regex matching reliable.
    unescaped = html.replace('\\"', '"').replace("\\\\", "\\")

    m = re.search(r'"playerSettings"\s*:\s*(\{.*?\})', unescaped, re.S)
    if m:
        ps_block = m.group(1)

        m2 = re.search(r'"abilitaDrm"\s*:\s*(true|false)', ps_block)
        if m2:
            result["abilitaDrm"] = m2.group(1) == "true"

        m3 = re.search(r'"hostnameCdn"\s*:\s*"([^"]+)"', ps_block)
        if m3:
            result["hostnameCdn"] = m3.group(1)

        m4 = re.search(r'"hostnameCdnDrm"\s*:\s*"([^"]+)"', ps_block)
        if m4:
            result["hostnameCdnDrm"] = m4.group(1)

        m5 = re.search(r'"drmLicenseServer"\s*:\s*"([^"]+)"', ps_block)
        if m5:
            result["drmLicenseServer"] = m5.group(1)

        m7 = re.search(r'"drmContentId"\s*:\s*"([^"]+)"', ps_block)
        if m7:
            result["drmContentId"] = m7.group(1)

    return result


def _build_stream_urls(
    video_data: dict,
    hostname_cdn: str,
    hostname_cdn_drm: str | None = None,
    is_drm: bool = False,
) -> dict:
    """Build DASH/HLS/MP4 URLs from videoData cf_remote_url (Kaltura/iltrovatore format).

    For DRM-protected content (is_drm=True) the manifest is served from the
    DRM CDN (hostnameCdnDrm) using the cloudfront path format:
        {hostname_cdn_drm}/{id}/DASH/{id}.mpd
    """
    upload_video = video_data.get("uploadVideo")
    cf_remote_url = (
        video_data.get("cf_remote_url")
        or (upload_video.get("cf_remote_url", "") if isinstance(upload_video, dict) else "")
    )
    if not cf_remote_url:
        return {}

    raw_paths = [p.strip() for p in cf_remote_url.split(",") if p.strip()]
    uses_content_paths = raw_paths[0].startswith("//content/")
    paths = [re.sub(r"/+", "/", p).replace(".mp4", "") for p in raw_paths]

    # DRM content: cloudfront path format on the DRM CDN
    if is_drm and hostname_cdn_drm:
        entry_id = paths[0].lstrip("/") if paths else ""
        if not entry_id:
            return {}
        result = {}
        result["dash"] = f"{hostname_cdn_drm}/{entry_id}/DASH/{entry_id}.mpd"
        return result

    if uses_content_paths:
        transformed = [
            ("//" if index == 0 else "/") + path.lstrip("/")
            for index, path in enumerate(paths)
        ]
    else:
        transformed = []
        for p in paths:
            if p.startswith("/nk/"):
                transformed.append(p)
            elif p.startswith("/"):
                transformed.append("/nk" + p)
            else:
                transformed.append("/nk/" + p)

    joined = ",".join(transformed)

    result = {}
    dash_separator = "," if uses_content_paths else "/,"
    result["dash"] = f"{hostname_cdn}/local/dash/{dash_separator}{joined},.mp4.urlset/manifest.mpd"
    result["hls"] = f"{hostname_cdn}/local/hls/,{joined},.mp4.urlset/master.m3u8"

    mp4_path = paths[0] if paths else ""
    if mp4_path.startswith("/nk/"):
        result["mp4"] = f"{hostname_cdn}{mp4_path}.mp4"
    elif mp4_path.startswith("/"):
        result["mp4"] = f"{hostname_cdn}/nk{mp4_path}.mp4"
    else:
        result["mp4"] = f"{hostname_cdn}/nk/{mp4_path}.mp4"

    return result
