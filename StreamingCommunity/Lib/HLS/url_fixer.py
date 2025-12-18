# 20.03.24

import logging
from urllib.parse import urlparse, urljoin


class M3U8_UrlFix:
    def __init__(self, url: str = None) -> None:
        """
        Initializes an M3U8_UrlFix object with the provided playlist URL.

        Parameters:
            - url (str, optional): The URL of the playlist. Defaults to None.
        """
        self.url_playlist: str = url

    def set_playlist(self, url: str) -> None:
        """
        Set the M3U8 playlist URL.

        Parameters:
            - url (str): The M3U8 playlist URL.
        """
        self.url_playlist = url

    def generate_full_url(self, url_resource: str) -> str:
        """
        Generate a full URL for a given resource using the base URL from the playlist.

        Parameters:
            - url_resource (str): The relative URL of the resource within the playlist.

        Returns:
            str: The full URL for the specified resource.
        """
        if self.url_playlist is None:
            logging.error("[M3U8_UrlFix] Cant generate full url, playlist not present")
            raise

        # Parse the playlist URL to extract the base URL components
        parsed_playlist_url = urlparse(self.url_playlist)
        
        # Construct the base URL using the scheme, netloc, and path from the playlist URL
        base_url = f"{parsed_playlist_url.scheme}://{parsed_playlist_url.netloc}{parsed_playlist_url.path}"

        # Join the base URL with the relative resource URL to get the full URL
        full_url = urljoin(base_url, url_resource)

        return full_url