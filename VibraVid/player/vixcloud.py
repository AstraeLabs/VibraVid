# 01.03.24

import logging
import re
from types import SimpleNamespace
from urllib.parse import parse_qs, urlencode, urljoin, urlparse, urlunparse

from bs4 import BeautifulSoup
from rich.console import Console

from VibraVid.utils.http_client import create_client, get_userAgent

console = Console()
logger = logging.getLogger(__name__)
ENABLE_VIXCLOUD_API_V2 = True


class VideoSource:
    def __init__(self, url: str, is_series: bool, media_id: int = None, tmdb_data: dict = None):
        """
        Initialize video source for streaming site.

        Args:
            url (str): The URL of the streaming site.
            is_series (bool): Flag for series or movie content
            media_id (int, optional): Unique identifier for media item
            tmdb_data (dict, optional): TMDB data with 'id' key for API V2
        """
        self.headers = {"user-agent": get_userAgent()}
        self.url = url
        self.is_series = is_series
        self.media_id = media_id
        self.iframe_src = None
        self.window_parameter = None
        self.canPlayFHD = False
        self.window_video = None
        self.season_number = None
        self.episode_number = None
        self.episode_id = None

        if tmdb_data is not None:
            self.tmdb_id = tmdb_data.get("id")
            self.season_number = tmdb_data.get("s")
            self.episode_number = tmdb_data.get("e")
        else:
            self.tmdb_id = None

    def get_iframe(self, episode_id: int) -> None:
        """
        Retrieve iframe source for specified episode.

        Args:
            episode_id (int): Unique identifier for episode
        """
        params = {}
        self.episode_id = episode_id

        if self.is_series:
            params = {"episode_id": episode_id, "next_episode": "1"}

        try:
            with create_client(headers=self.headers) as client:
                response = client.get(f"{self.url}/iframe/{self.media_id}", params=params)
            response.raise_for_status()

            # Parse response with BeautifulSoup to get iframe source
            soup = BeautifulSoup(response.text, "html.parser")
            self.iframe_src = soup.find("iframe").get("src")

        except Exception as e:
            logger.error(f"Error getting iframe source: {e}")
            raise

    def parse_script(self, script_text: str) -> None:
        try:
            # token / expires / url (inside masterPlaylist)
            token_m = re.search(r"(?:['\"]token['\"]|token)\s*:\s*['\"](?P<token>[^'\"]+)['\"]", script_text)
            expires_m = re.search(r"(?:['\"]expires['\"]|expires)\s*:\s*['\"](?P<expires>[^'\"]+)['\"]", script_text)
            url_m = re.search(r"(?:['\"]url['\"]|url)\s*:\s*['\"](?P<url>https?://[^'\"]+)['\"]", script_text)

            # simple video id and canPlayFHD
            video_id_m = re.search(r"window\.video\s*=\s*\{[^}]*\bid\s*:\s*['\"](?P<id>\d+)['\"]", script_text)
            canplay_m = re.search(r"window\.canPlayFHD\s*=\s*(true|false)", script_text)

            # Extract values if matches found
            token = token_m.group("token") if token_m else None
            expires = expires_m.group("expires") if expires_m else None
            url = url_m.group("url") if url_m else None
            video_id = int(video_id_m.group("id")) if video_id_m else None
            canplay = bool(canplay_m and canplay_m.group(1).lower() == "true")
            self.canPlayFHD = canplay
            self.window_video = SimpleNamespace(id=video_id) if video_id is not None else None

            if token or expires or url:
                self.window_parameter = SimpleNamespace(token=token, expires=expires, url=url)
            else:
                self.window_parameter = None

        except Exception as e:
            logger.error(f"Error parsing script: {e}")
            raise

    def _resolve_tmdb_embed_url(self) -> None:
        logger.info("Resolving TMDB embed URL with API V2")
        if self.tmdb_id is None:
            return

        language = urlparse(self.url).path.rstrip("/").rsplit("/", 1)[-1]
        if language not in {"it", "en"}:
            language = "it"

        if self.is_series:
            if self.season_number is None or self.episode_number is None:
                return
            api_url = f"https://vixsrc.to/api/tv/{self.tmdb_id}/{self.season_number}/{self.episode_number}?lang={language}"
        else:
            api_url = f"https://vixsrc.to/api/movie/{self.tmdb_id}?lang={language}"

        client = create_client(headers=self.headers)
        try:
            response = client.get(api_url)
            response.raise_for_status()
            payload = response.json()
            src = payload.get("src")
            if src:
                self.iframe_src = urljoin("https://vixsrc.to", src)

        finally:
            client.close()

    def get_content(self) -> None:
        """
        Fetch and process video content from iframe source.
        """
        try:
            if ENABLE_VIXCLOUD_API_V2 and self.tmdb_id is not None:
                self._resolve_tmdb_embed_url()

            # Fetch content from iframe source
            if self.iframe_src is not None:
                with create_client(headers=self.headers) as client:
                    response = client.get(self.iframe_src)
                response.raise_for_status()
                self.parse_script(script_text=response.text)

        except Exception as e:
            logger.error(f"Error getting content: {e}")
            raise

    def get_playlist(self) -> str:
        """
        Generate authenticated playlist URL.

        Returns:
            str: Fully constructed playlist URL with authentication parameters, or None if content unavailable
        """
        if not self.window_parameter:
            return None

        if not getattr(self.window_parameter, "url", None):
            return None

        params = {}

        if self.canPlayFHD:
            params["h"] = 1

        parsed_url = urlparse(str(self.window_parameter.url))
        query_params = parse_qs(str(parsed_url.query))

        if "b" in query_params and query_params["b"] == ["1"]:
            params["b"] = 1

        params.update({"token": str(self.window_parameter.token), "expires": str(self.window_parameter.expires)})

        query_string = urlencode(params)
        return urlunparse(parsed_url._replace(query=str(query_string)))


class VideoSourceAnime(VideoSource):
    def __init__(self, url: str):
        """
        Initialize anime-specific video source.

        Args:
            url (str): The URL of the streaming site.

        Extends base VideoSource with anime-specific initialization
        """
        self.headers = {"user-agent": get_userAgent()}
        self.url = url
        self.src_mp4 = None
        self.master_playlist = None
        self.iframe_src = None
        self.tmdb_id = None

    def get_embed(self, episode_id: int, prefer_mp4: bool = True) -> str:
        """
        Retrieve embed URL and extract video source.

        Args:
            episode_id (int): Unique identifier for episode

        Returns:
            str: Parsed script content
        """
        try:
            with create_client(headers=self.headers) as client:
                response = client.get(f"{self.url}/embed-url/{episode_id}")
            response.raise_for_status()

            # Extract and clean embed URL
            embed_url = response.text.strip()
            self.iframe_src = embed_url

            # Fetch video content using embed URL
            with create_client(headers=self.headers) as client:
                video_response = client.get(embed_url)
            video_response.raise_for_status()

            # Parse response with BeautifulSoup to get content of the scriot
            soup = BeautifulSoup(video_response.text, "html.parser")
            script = soup.find("body").find("script").text
            self.src_mp4 = soup.find("body").find_all("script")[1].text.split(" = ")[1].replace("'", "")

            if not prefer_mp4:
                self.get_content()
                self.master_playlist = self.get_playlist()

            return script

        except Exception as e:
            logger.error(f"Error fetching embed URL: {e}")
            return None
