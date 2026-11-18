# 22.08.26
# By @sync-luca98

import json
import re
from urllib.parse import urljoin

from VibraVid.utils.http_client import create_client, get_userAgent


def _jsonld(html: str) -> list[dict]:
    result = []
    for raw in re.findall(r"<script[^>]+type=[\"']application/ld\+json[\"'][^>]*>(.*?)</script>", html, re.I | re.S):
        try:
            value = json.loads(raw.strip())
        except (TypeError, ValueError):
            continue
        result.extend(value if isinstance(value, list) else [value])
    return [item for item in result if isinstance(item, dict)]


def _extract_dash_url(html: str) -> str | None:
    """Construct DASH manifest URL from flavor IDs found in the page."""
    # Find partner ID and entry ID from MP4 URL
    mp4_match = re.search(r'https?://awsvodpkg\.iltrovatore\.it/content/entry/data/0/(\d+)/(0_[a-z0-9]{8})_0_[a-z0-9]{8}_\d+\.mp4', html)
    if not mp4_match:
        return None
    partner_id, entry_id = mp4_match.groups()
    
    # Find all flavor IDs for this entry
    flavor_pattern = rf'{re.escape(entry_id)}_0_([a-z0-9]+)'
    flavor_matches = re.findall(flavor_pattern, html)
    # Use unique flavor IDs
    unique_flavors = list(dict.fromkeys(flavor_matches))
    if len(unique_flavors) < 2:
        return None
    
    # Use first two unique flavor IDs
    flavor1, flavor2 = unique_flavors[0], unique_flavors[1]
    
    # Construct DASH URL: /local/dash//,/content/entry/data/0/{partner_id}/{entry_id}_0_{flavor1}_1,/content/entry/data/0/{partner_id}/{entry_id}_0_{flavor2}_1,.mp4.urlset/manifest.mpd
    dash_url = (
        f"https://awsvodpkg.iltrovatore.it/local/dash//,"
        f"/content/entry/data/0/{partner_id}/{entry_id}_0_{flavor1}_1,"
        f"/content/entry/data/0/{partner_id}/{entry_id}_0_{flavor2}_1,"
        f".mp4.urlset/manifest.mpd"
    )
    return dash_url


def get_playback_info(page_url: str) -> dict:
    with create_client(headers={"user-agent": get_userAgent(), "referer": page_url}) as client:
        response = client.get(page_url)
        response.raise_for_status()
        html = response.text
    sources = []
    for item in _jsonld(html):
        content_url = item.get("contentUrl")
        if isinstance(content_url, str):
            sources.append(urljoin(page_url, content_url))
    sources.extend(re.findall(r'https?[^"\'\s<>]+(?:\.mp4|\.m3u8|\.mpd)(?:\?[^"\'\s<>]*)?', html, re.I))
    sources = list(dict.fromkeys(sources))
    def _path(url: str) -> str:
        return url.split("?", 1)[0].lower()

    mp4_sources = [url for url in sources if _path(url).endswith(".mp4")]
    # Skip Akamai HLS URLs (la7-vh.akamaihd.net) as they return 400 errors
    hls_sources = [url for url in sources if ".m3u8" in _path(url) and "la7-vh.akamaihd.net" not in url]
    dash_sources = [url for url in sources if ".mpd" in _path(url)]
    
    # Try to construct DASH URL from flavor IDs
    constructed_dash = _extract_dash_url(html)
    if constructed_dash:
        dash_sources.insert(0, constructed_dash)
    
    title = next((item.get("name") for item in _jsonld(html) if item.get("name")), page_url.rsplit("/", 1)[-1])
    protected = False
    drm_type = None
    manifest = ""
    if dash_sources:
        with create_client(headers={"user-agent": get_userAgent(), "referer": page_url}) as client:
            manifest = client.get(dash_sources[0]).text
        protected = "contentprotection" in manifest.lower() or "cenc:default_kid" in manifest.lower()
        # Prefer Widevine over PlayReady (as per Kodi plugin approach)
        if "urn:uuid:edef8ba9-79d6-4ace-a3c8-27dcd51d21ed" in manifest:
            drm_type = "Widevine"
        elif "mspr:pro" in manifest:
            drm_type = "PlayReady"
    if hls_sources:
        with create_client(headers={"user-agent": get_userAgent(), "referer": page_url}) as client:
            hls_manifest = client.get(hls_sources[0]).text
        protected = protected or "sample-aes" in hls_manifest.lower() or "fairplay" in hls_manifest.lower()
    return {
        "title": re.sub(r"\s+", " ", str(title)).strip(),
        "mp4_url": max(mp4_sources, key=len, default=None),
        "hls_url": hls_sources[0] if hls_sources else None,
        "dash_url": dash_sources[0] if dash_sources else None,
        "drm": drm_type,
    }
