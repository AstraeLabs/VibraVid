# 01.03.24

import re
import time
import logging
from urllib.parse import urlparse, parse_qs, urlencode, urlunparse
from typing import Dict, Any


# External libraries
from bs4 import BeautifulSoup
from rich.console import Console


# Internal utilities
from StreamingCommunity.Util.http_client import create_client, get_userAgent


# Variable
console = Console()


class WindowVideo:
    def __init__(self, data: Dict[str, Any]):
        self.data = data
        self.id: int = data.get('id', '')
        self.name: str = data.get('name', '')
        self.filename: str = data.get('filename', '')
        self.size: str = data.get('size', '')
        self.quality: str = data.get('quality', '')
        self.duration: str = data.get('duration', '')
        self.views: int = data.get('views', '')
        self.is_viewable: bool = data.get('is_viewable', '')
        self.status: str = data.get('status', '')
        self.fps: float = data.get('fps', '')
        self.legacy: bool = data.get('legacy', '')
        self.folder_id: int = data.get('folder_id', '')
        self.created_at_diff: str = data.get('created_at_diff', '')

    def __str__(self):
        return f"WindowVideo(id={self.id}, name='{self.name}', filename='{self.filename}', size='{self.size}', quality='{self.quality}', duration='{self.duration}', views={self.views}, is_viewable={self.is_viewable}, status='{self.status}', fps={self.fps}, legacy={self.legacy}, folder_id={self.folder_id}, created_at_diff='{self.created_at_diff}')"


class WindowParameter:
    def __init__(self, data: Dict[str, Any]):
        self.data = data
        params = data.get('params', {})
        self.token: str = params.get('token', '')
        self.expires: str = str(params.get('expires', ''))
        self.url = data.get('url')

    def __str__(self):
        return (f"WindowParameter(token='{self.token}', expires='{self.expires}', url='{self.url}', data={self.data})")


class JavaScriptParser:
    @staticmethod
    def fix_string(ss):
        if ss is None:
            return None
        
        ss = str(ss)
        ss = ss.encode('utf-8').decode('unicode-escape')
        ss = ss.strip("\"'")
        ss = ss.strip()
        
        return ss
    
    @staticmethod
    def fix_url(url):
        if url is None:
            return None

        url = url.replace('\\/', '/')
        return url

    @staticmethod
    def parse_value(value):
        value = JavaScriptParser.fix_string(value)

        if 'http' in str(value) or 'https' in str(value):
            return JavaScriptParser.fix_url(value)
        
        if value is None or str(value).lower() == 'null':
            return None
        if str(value).lower() == 'true':
            return True
        if str(value).lower() == 'false':
            return False
        
        try:
            return int(value)
        except ValueError:
            try:
                return float(value)
            except ValueError:
                pass
        
        return value

    @staticmethod
    def parse_object(obj_str):
        obj_str = obj_str.strip('{}').strip()
        
        result = {}
        key_value_pairs = re.findall(r'([\'"]?[\w]+[\'"]?)\s*:\s*([^,{}]+|{[^}]*}|\[[^\]]*\]|\'[^\']*\'|"[^"]*")', obj_str)
        
        for key, value in key_value_pairs:
            key = JavaScriptParser.fix_string(key)
            value = value.strip()

            if value.startswith('{'):
                result[key] = JavaScriptParser.parse_object(value)
            elif value.startswith('['):
                result[key] = JavaScriptParser.parse_array(value)
            else:
                result[key] = JavaScriptParser.parse_value(value)
        
        return result

    @staticmethod
    def parse_array(arr_str):
        arr_str = arr_str.strip('[]').strip()
        result = []
        
        elements = []
        current_elem = ""
        brace_count = 0
        in_string = False
        quote_type = None

        for char in arr_str:
            if char in ['"', "'"]:
                if not in_string:
                    in_string = True
                    quote_type = char
                elif quote_type == char:
                    in_string = False
                    quote_type = None
            
            if not in_string:
                if char == '{':
                    brace_count += 1
                elif char == '}':
                    brace_count -= 1
                elif char == ',' and brace_count == 0:
                    elements.append(current_elem.strip())
                    current_elem = ""
                    continue
            
            current_elem += char
        
        if current_elem.strip():
            elements.append(current_elem.strip())
        
        for elem in elements:
            elem = elem.strip()
            
            if elem.startswith('{'):
                result.append(JavaScriptParser.parse_object(elem))
            elif 'active' in elem or 'url' in elem:
                key_value_match = re.search(r'([\w]+)\":([^,}]+)', elem)

                if key_value_match:
                    key = key_value_match.group(1)
                    value = key_value_match.group(2)
                    result[-1][key] = JavaScriptParser.parse_value(value.strip('"\\'))
            else:
                result.append(JavaScriptParser.parse_value(elem))
        
        return result

    @classmethod
    def parse(cls, js_string):
        assignments = re.findall(r'window\.(\w+)\s*=\s*([^;]+);?', js_string, re.DOTALL)
        result = {}
        
        for var_name, value in assignments:
            value = value.strip()
            
            if value.startswith('{'):
                result[var_name] = cls.parse_object(value)
            elif value.startswith('['):
                result[var_name] = cls.parse_array(value)
            else:
                result[var_name] = cls.parse_value(value)
        
        can_play_fhd_match = re.search(r'window\.canPlayFHD\s*=\s*(\w+);?', js_string)
        if can_play_fhd_match:
            result['canPlayFHD'] = cls.parse_value(can_play_fhd_match.group(1))
        
        return result


class VideoSource:
    def __init__(self, url: str, is_series: bool, media_id: int = None):
        """
        Initialize video source for streaming site.
        
        Args:
            - url (str): The URL of the streaming site.
            - is_series (bool): Flag for series or movie content
            - media_id (int, optional): Unique identifier for media item
        """
        self.headers = {'user-agent': get_userAgent()}
        self.url = url
        self.is_series = is_series
        self.media_id = media_id
        self.iframe_src = None
        self.window_parameter = None

    def get_iframe(self, episode_id: int) -> None:
        """
        Retrieve iframe source for specified episode.
        
        Args:
            episode_id (int): Unique identifier for episode
        """
        params = {}

        if self.is_series:
            params = {
                'episode_id': episode_id, 
                'next_episode': '1'
            }

        try:
            response = create_client(headers=self.headers).get(f"{self.url}/iframe/{self.media_id}", params=params)
            response.raise_for_status()

            # Parse response with BeautifulSoup to get iframe source
            soup = BeautifulSoup(response.text, "html.parser")
            self.iframe_src = soup.find("iframe").get("src")

        except Exception as e:
            logging.error(f"Error getting iframe source: {e}")
            raise

    def parse_script(self, script_text: str) -> None:
        """
        Convert raw script to structured video metadata.
        
        Args:
            script_text (str): Raw JavaScript/HTML script content
        """
        try:
            converter = JavaScriptParser.parse(js_string=str(script_text))

            # Create window video, streams and parameter objects
            self.canPlayFHD = bool(converter.get('canPlayFHD'))
            self.window_video = WindowVideo(converter.get('video'))
            self.window_parameter = WindowParameter(converter.get('masterPlaylist'))
            time.sleep(0.5)

        except Exception as e:
            logging.error(f"Error parsing script: {e}")
            raise

    def get_content(self) -> None:
        """
        Fetch and process video content from iframe source.
        
        Workflow:
            - Validate iframe source
            - Retrieve content
            - Parse embedded script
        """
        try:
            if self.iframe_src is not None:
                response = create_client(headers=self.headers).get(self.iframe_src)
                response.raise_for_status()

                # Parse response with BeautifulSoup to get content
                soup = BeautifulSoup(response.text, "html.parser")
                script = soup.find("body").find("script").text

                # Parse script to get video information
                self.parse_script(script_text=script)

        except Exception as e:
            logging.error(f"Error getting content: {e}")
            raise

    def get_playlist(self) -> str:
        """
        Generate authenticated playlist URL.

        Returns:
            str: Fully constructed playlist URL with authentication parameters, or None if content unavailable
        """
        if not self.window_parameter:
            return None
            
        params = {}

        if self.canPlayFHD:
            params['h'] = 1

        parsed_url = urlparse(self.window_parameter.url)
        query_params = parse_qs(parsed_url.query)

        if 'b' in query_params and query_params['b'] == ['1']:
            params['b'] = 1

        params.update({
            "token": self.window_parameter.token,
            "expires": self.window_parameter.expires
        })

        query_string = urlencode(params)
        return urlunparse(parsed_url._replace(query=query_string))


class VideoSourceAnime(VideoSource):
    def __init__(self, url: str):
        """
        Initialize anime-specific video source.
        
        Args:
            - url (str): The URL of the streaming site.
        
        Extends base VideoSource with anime-specific initialization
        """
        self.headers = {'user-agent': get_userAgent()}
        self.url = url
        self.src_mp4 = None
        self.iframe_src = None

    def get_embed(self, episode_id: int):
        """
        Retrieve embed URL and extract video source.
        
        Args:
            episode_id (int): Unique identifier for episode
        
        Returns:
            str: Parsed script content
        """
        try:
            response = create_client(headers=self.headers).get(f"{self.url}/embed-url/{episode_id}")
            response.raise_for_status()

            # Extract and clean embed URL
            embed_url = response.text.strip()
            self.iframe_src = embed_url

            # Fetch video content using embed URL
            video_response = create_client(headers=self.headers).get(embed_url)
            video_response.raise_for_status()

            # Parse response with BeautifulSoup to get content of the scriot
            soup = BeautifulSoup(video_response.text, "html.parser")
            script = soup.find("body").find("script").text
            self.src_mp4 = soup.find("body").find_all("script")[1].text.split(" = ")[1].replace("'", "")

            return script
        
        except Exception as e:
            logging.error(f"Error fetching embed URL: {e}")
            return None
