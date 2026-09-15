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
    hls_sources = [url for url in sources if ".m3u8" in _path(url)]
    dash_sources = [url for url in sources if ".mpd" in _path(url)]
    title = next((item.get("name") for item in _jsonld(html) if item.get("name")), page_url.rsplit("/", 1)[-1])
    protected = False
    manifest = ""
    if dash_sources:
        with create_client(headers={"user-agent": get_userAgent(), "referer": page_url}) as client:
            manifest = client.get(dash_sources[0]).text
        protected = "contentprotection" in manifest.lower() or "cenc:default_kid" in manifest.lower()
    if hls_sources:
        with create_client(headers={"user-agent": get_userAgent(), "referer": page_url}) as client:
            hls_manifest = client.get(hls_sources[0]).text
        protected = protected or "sample-aes" in hls_manifest.lower() or "fairplay" in hls_manifest.lower()
    return {
        "title": re.sub(r"\s+", " ", str(title)).strip(),
        "mp4_url": max(mp4_sources, key=len, default=None),
        "hls_url": hls_sources[0] if hls_sources else None,
        "dash_url": dash_sources[0] if dash_sources else None,
        "drm": "Widevine/PlayReady" if protected else None,
    }
