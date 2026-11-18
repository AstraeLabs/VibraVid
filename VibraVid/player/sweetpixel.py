# 21.03.25

import logging

from VibraVid.utils.http_client import create_client, get_userAgent

logger = logging.getLogger(__name__)


class VideoSource:
    def __init__(self, site_url, episode_data, session_id, csrf_token):
        """Initialize the VideoSource with session details, episode data, and URL."""
        self.session_id = session_id
        self.csrf_token = csrf_token
        self.episode_data = episode_data
        self.number = episode_data.number
        self.token = getattr(episode_data, "token", "")
        play_url = getattr(episode_data, "play_url", "")
        self.link = site_url + episode_data.url
        referer = (site_url + play_url) if play_url else site_url

        # Create an HTTP client with session cookies, headers, and base URL.
        self.client = create_client(
            cookies={"sessionId": session_id},
            headers={
                "User-Agent": get_userAgent(),
                "CSRF-Token": csrf_token,
                "X-Requested-With": "XMLHttpRequest",
                "Accept": "application/json, text/javascript, */*; q=0.01",
                "Origin": site_url,
                "Referer": referer,
            },
            follow_redirects=True,
        )

    def get_playlist(self):
        """Fetch the direct MP4 URL from AnimeWorld via api/episode/info."""
        try:
            res = self.client.get(self.link, params={"id": self.token, "alt": "0"})
            data = res.json()

            if data.get("error"):
                logger.error(f"api/episode/info error for token={self.token}: {data}")
                return None

            grabber = data.get("grabber")
            if grabber:
                return grabber

            logger.error(f"No grabber in response for token={self.token}: {data}")
            return None

        except Exception as e:
            logger.error(f"Error fetching episode info: {e}")
            return None

    def close(self):
        """Close the HTTP client session."""
        if self.client:
            self.client.close()
