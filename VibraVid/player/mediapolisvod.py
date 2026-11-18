# 11.04.25

import json
from urllib.parse import quote, urlparse, urlunparse

from VibraVid.utils.http_client import create_client, get_headers


def _sanitize_url_port(url: str) -> str:
    """Rimuove la porta dall'URL se non è un numero decimale valido tra 0 e 65535."""
    try:
        parsed = urlparse(url)
    except ValueError:
        return url

    netloc = parsed.netloc
    if ":" in netloc:
        host, _, port = netloc.rpartition(":")
        if not port or not port.isdigit() or int(port) > 65535:
            netloc = host
            parsed = parsed._replace(netloc=netloc)
            url = urlunparse(parsed)

    return url


class VideoSource:
    @staticmethod
    def extract_m3u8_url(video_url: str) -> tuple[str | None, str | None]:
        """Extract the m3u8 streaming URL (and, if present, the .srt subtitle URL) from a RaiPlay video URL."""
        if not video_url.endswith(".json"):
            if "/video/" in video_url:
                video_id = video_url.split("/")[-1].split(".")[0]
                video_path = "/".join(video_url.split("/")[:-1])
                video_url = f"{video_path}/{video_id}.json"

            else:
                return "Error: Unable to determine video JSON URL", None

        try:
            with create_client(headers=get_headers()) as client:
                response = client.get(video_url)
            if response.status_code != 200:
                return f"Error: Failed to fetch video data (Status: {response.status_code})", None

            video_data = response.json()
            video_obj = video_data.get("video") or {}
            content_url = video_obj.get("content_url")

            # The same JSON already carries the .srt sidecar(s) (video.subtitlesArray), if any.
            subtitles_array = video_obj.get("subtitlesArray") or video_obj.get("subtitleList") or []
            subtitle_path = subtitles_array[0].get("url") if subtitles_array else None
            subtitle_url = f"https://www.raiplay.it{quote(subtitle_path)}" if subtitle_path else None

            if not content_url:
                return "Error: No content URL found in video data", subtitle_url

            # Extract the element key
            if "=" in content_url:
                element_key = content_url.split("=")[1]
            else:
                return "Error: Unable to extract element key", subtitle_url

            # Request the stream URL
            params = {
                "cont": element_key,
                "output": "62",
            }

            with create_client(headers=get_headers()) as client:
                stream_response = client.get("https://mediapolisvod.rai.it/relinker/relinkerServlet.htm", params=params)
            if stream_response.status_code != 200:
                return f"Error: Failed to fetch stream URL (Status: {stream_response.status_code})", subtitle_url

            try:
                stream_data = stream_response.json()
                m3u8_url = stream_data.get("video")[0] if "video" in stream_data else None
            except Exception:
                try:
                    response_text = stream_response.content.decode("latin-1")
                    stream_data = json.loads(response_text)
                    m3u8_url = stream_data.get("video")[0] if "video" in stream_data else None
                except Exception as decode_error:
                    return f"Error: Failed to decode response - {str(decode_error)}", subtitle_url

            return _sanitize_url_port(m3u8_url), subtitle_url

        except Exception as e:
            return f"Error: {str(e)}", None
