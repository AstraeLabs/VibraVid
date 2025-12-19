# 26.05.24

import re
import logging


# External libraries
import jsbeautifier
from bs4 import BeautifulSoup


# Internal utilities
from StreamingCommunity.Util.http_client import create_client_curl, get_headers


class VideoSource:
    def __init__(self, url: str) -> None:
        """
        Initializes the VideoSource object with default values.

        Attributes:
            - url (str): The URL of the video source.
        """
        self.headers = get_headers()
        self.url = url

    def make_request(self, url: str) -> str:
        """
        Make an HTTP GET request to the provided URL.

        Parameters:
            - url (str): The URL to make the request to.

        Returns:
            - str: The response content if successful, None otherwise.
        """
        try:
            response = create_client_curl(headers=self.headers).get(url)
            if response.status_code >= 400:
                logging.error(f"Request failed with status code: {response.status_code}, to url: {url}")
                return None
            
            return response.text
        
        except Exception as e:
            logging.error(f"Request failed: {e}")
            return None
 
    def get_iframe(self, soup):
        """
        Extracts the source URL of the second iframe in the provided BeautifulSoup object.

        Parameters:
            - soup (BeautifulSoup): A BeautifulSoup object representing the parsed HTML.

        Returns:
            - str: The source URL of the second iframe, or None if not found.
        """
        iframes = soup.find_all("iframe")
        if iframes and len(iframes) > 1:
            return iframes[0].get("src") or iframes[0].get("data-src")
        
        return None

    def find_content(self, url):
        """
        Makes a request to the specified URL and parses the HTML content.

        Parameters:
            - url (str): The URL to fetch content from.

        Returns:
            - BeautifulSoup: A BeautifulSoup object representing the parsed HTML content, or None if the request fails.
        """
        content = self.make_request(url)
        if content:
            return BeautifulSoup(content, "html.parser")
        
        return None
        
    def get_result_node_js(self, soup):
        """
        Prepares and runs a Node.js script from the provided BeautifulSoup object to retrieve the video URL.

        Parameters:
            - soup (BeautifulSoup): A BeautifulSoup object representing the parsed HTML content.

        Returns:
            - str: The output from the Node.js script, or None if the script cannot be found or executed.
        """
        for script in soup.find_all("script"):
            if "eval" in str(script):
                return jsbeautifier.beautify(script.text)
            
        return None

    def get_playlist(self) -> str:
        """
        Download a video from the provided URL.

        Returns:
            str: The URL of the downloaded video if successful, None otherwise.
        """
        try:
            html_content = self.make_request(self.url)
            if not html_content:
                logging.error("Failed to fetch HTML content.")
                return None

            # Find master playlist
            data_js = self.get_result_node_js(BeautifulSoup(html_content, "html.parser"))

            if data_js is not None:
                match = re.search(r'sources:\s*\[\{\s*file:\s*"([^"]+)"', data_js)

                if match:
                    return match.group(1)
                else:
                    logging.error("Failed to find M3U8 URL: No match found")
                    
            else:

                iframe_src = self.get_iframe(BeautifulSoup(html_content, "html.parser"))
                if not iframe_src:
                    logging.error("No iframe found.")
                    return None

                down_page_soup = self.find_content(iframe_src)
                if not down_page_soup:
                    logging.error("Failed to fetch down page content.")
                    return None

                pattern = r'data-link="(//supervideo[^"]+)"'
                match = re.search(pattern, str(down_page_soup))
                if not match:
                    logging.error("No player available for download.")
                    return None

                supervideo_url = "https:" + match.group(1)
                supervideo_soup = self.find_content(supervideo_url)
                if not supervideo_soup:
                    logging.error("Failed to fetch supervideo content.")
                    return None

                # Find master playlist
                data_js = self.get_result_node_js(supervideo_soup)

                match = re.search(r'sources:\s*\[\{\s*file:\s*"([^"]+)"', data_js)

                if match:
                    return match.group(1)
                else:
                    logging.error("Failed to find M3U8 URL: No match found")
            
            return None

        except Exception as e:
            logging.error(f"An error occurred: {e}")
            return None
