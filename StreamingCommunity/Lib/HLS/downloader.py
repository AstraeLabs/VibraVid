# 17.10.24

import os
import sys
import logging
import shutil
from typing import Any, Dict, List, Optional, Union


# External libraries
from rich.console import Console
from rich.table import Table


# Internal utilities
from StreamingCommunity.Util import config_manager, os_manager, internet_manager
from StreamingCommunity.Util.http_client import fetch, get_userAgent


# Logic class
from StreamingCommunity.Lib.FFmpeg import print_duration_table, join_video, join_audios, join_subtitle
from .parser import M3U8_Parser
from .url_fixer import M3U8_UrlFix
from .segments import M3U8_Segments



# Config
console = Console()
DOWNLOAD_SPECIFIC_AUDIO = config_manager.get_list('M3U8_DOWNLOAD', 'specific_list_audio')
DOWNLOAD_SPECIFIC_SUBTITLE = config_manager.get_list('M3U8_DOWNLOAD', 'specific_list_subtitles')
MERGE_SUBTITLE = config_manager.get_bool('M3U8_DOWNLOAD', 'merge_subs')
CLEANUP_TMP = config_manager.get_bool('M3U8_DOWNLOAD', 'cleanup_tmp_folder')
GET_ONLY_LINK = config_manager.get_int('M3U8_DOWNLOAD', 'get_only_link')
FILTER_CUSTOM_RESOLUTION = str(config_manager.get('M3U8_CONVERSION', 'force_resolution')).strip().lower()
EXTENSION_OUTPUT = config_manager.get("M3U8_CONVERSION", "extension")


class HLSClient:
    """Client for making HTTP requests to HLS endpoints with retry mechanism."""
    def __init__(self, custom_headers: Optional[Dict[str, str]] = None):
        self.headers = custom_headers if custom_headers else {'User-Agent': get_userAgent()}

    def request(self, url: str, return_content: bool = False) -> Optional[Union[str, bytes]]:
        """
        Makes HTTP GET requests with retry logic using http_client.

        Args:
            url: Target URL to request
            return_content: If True, returns response content instead of text

        Returns:
            Response content/text or None if all retries fail
        """
        # Check if URL is None or empty
        if not url:
            logging.error("URL is None or empty, cannot make request")
            return None

        return fetch(
            url,
            method="GET",
            headers=self.headers,
            timeout=15,
            return_content=return_content
        )


class PathManager:
    """Manages file paths and directories for downloaded content."""
    def __init__(self, m3u8_url: str, output_path: Optional[str]):
        """
        Args:
            m3u8_url: Source M3U8 playlist URL
            output_path: Desired output path for the final video file
        """
        self.m3u8_url = m3u8_url
        self.output_path = self._sanitize_output_path(output_path)
        base_name = os.path.basename(self.output_path).replace(EXTENSION_OUTPUT, "")
        self.temp_dir = os.path.join(os.path.dirname(self.output_path), f"{base_name}_tmp")

    def _sanitize_output_path(self, path: Optional[str]) -> str:
        """
        Ensures output path is valid and follows expected format.
        Creates a hash-based filename if no path is provided.
        """
        if not path:
            path = f"download{EXTENSION_OUTPUT}"
            
        if not path.endswith(EXTENSION_OUTPUT):
            path += EXTENSION_OUTPUT

        return os_manager.get_sanitize_path(path)

    def setup_directories(self):
        """Creates necessary directories for temporary files (video, audio, subtitles)."""
        os.makedirs(self.temp_dir, exist_ok=True)
        for subdir in ['video', 'audio', 'subs']:
            os.makedirs(os.path.join(self.temp_dir, subdir), exist_ok=True)
            
        # Create a .ignore file to prevent temp directory from being indexed by Jellyfin
        ignore_path = os.path.join(self.temp_dir, '.ignore')
        with open(ignore_path, 'a', encoding='utf-8'):
            os.utime(ignore_path, None)

    def move_final_file(self, final_file: str):
        """Moves the final merged file to the desired output location."""
        if os.path.exists(self.output_path):
            os.remove(self.output_path)
        shutil.move(final_file, self.output_path)

    def cleanup(self):
        """Removes temporary directories if configured to do so."""
        if CLEANUP_TMP:
            os_manager.remove_folder(self.temp_dir)


class M3U8Manager:
    """Handles M3U8 playlist parsing and stream selection."""
    def __init__(self, m3u8_url: str, client: HLSClient):
        self.m3u8_url = m3u8_url
        self.client = client
        self.parser = M3U8_Parser()
        self.url_fixer = M3U8_UrlFix()
        self.video_url = None
        self.video_res = None
        self.audio_streams = []
        self.sub_streams = []
        self.is_master = False

    def parse(self) -> bool:
        """
        Fetches and parses the M3U8 playlist content.
        Determines if it's a master playlist (index) or media playlist.
        
        Returns:
            bool: True if parsing was successful, False otherwise
        """
        try:
            content = self.client.request(self.m3u8_url)
            if not content:
                logging.error(f"Failed to fetch M3U8 content from {self.m3u8_url}")
                sys.exit(0)
                return False

            self.parser.parse_data(uri=self.m3u8_url, raw_content=content)
            self.url_fixer.set_playlist(self.m3u8_url)
            self.is_master = self.parser.is_master_playlist
            return True
            
        except Exception as e:
            logging.error(f"Error parsing M3U8 from {self.m3u8_url}: {str(e)}")
            return False

    def select_streams(self):
        """
        Selects video, audio, and subtitle streams based on configuration.
        If it's a master playlist, only selects video stream.
        Auto-selects first audio if only one is available and none match filters.
        """
        if not self.is_master:
            self.video_url, self.video_res = self.m3u8_url, "undefined"
            self.audio_streams = []
            self.sub_streams = []

        else:
            # Video selection logic
            if str(FILTER_CUSTOM_RESOLUTION) == "best":
                self.video_url, self.video_res = self.parser._video.get_best_uri()
            elif str(FILTER_CUSTOM_RESOLUTION) == "worst":
                self.video_url, self.video_res = self.parser._video.get_worst_uri()
            elif str(FILTER_CUSTOM_RESOLUTION).replace("p", "").replace("px", "").isdigit():
                resolution_value = int(str(FILTER_CUSTOM_RESOLUTION).replace("p", "").replace("px", ""))
                self.video_url, self.video_res = self.parser._video.get_custom_uri(resolution_value)

                # Fallback to best if custom resolution not found
                if self.video_url is None:
                    self.video_url, self.video_res = self.parser._video.get_best_uri()
            else:
                logging.error("Resolution not recognized.")
                self.video_url, self.video_res = self.parser._video.get_best_uri()

            # Audio selection with auto-select fallback
            all_audio = self.parser._audio.get_all_uris_and_names() or []
            
            # Try to match with configured languages
            self.audio_streams = [
                s for s in all_audio
                if s.get('language') in DOWNLOAD_SPECIFIC_AUDIO
            ]
            
            # Auto-select first audio if:
            # 1. No audio matched the filters
            # 2. At least one audio track is available
            # 3. Filters are configured (not empty)
            if not self.audio_streams and all_audio and DOWNLOAD_SPECIFIC_AUDIO:
                first_audio_lang = all_audio[0].get('language', 'unknown')
                console.print(f"[yellow]Auto-selecting first available audio track: {first_audio_lang}")
                self.audio_streams = [all_audio[0]]

            # Subtitle selection
            self.sub_streams = []
            if "*" in DOWNLOAD_SPECIFIC_SUBTITLE:
                self.sub_streams = self.parser._subtitle.get_all_uris_and_names() or []
            else:
                self.sub_streams = [
                    s for s in (self.parser._subtitle.get_all_uris_and_names() or [])
                    if s.get('language') in DOWNLOAD_SPECIFIC_SUBTITLE
                ]

    def log_selection(self):
        """Log the stream selection information in a formatted table."""
        def calculate_column_widths():
            data_rows = []
            
            # Video information
            tuple_available_resolution = self.parser._video.get_list_resolution() or []
            list_available_resolution = [f"{r[0]}x{r[1]}" for r in tuple_available_resolution]
            available_video = ', '.join(list_available_resolution) if list_available_resolution else "Nothing"
            
            downloadable_video = "Nothing"
            if isinstance(self.video_res, tuple) and len(self.video_res) >= 2:
                downloadable_video = f"{self.video_res[0]}x{self.video_res[1]}"
            elif self.video_res and self.video_res != "undefined":
                downloadable_video = str(self.video_res)
            
            data_rows.append(["Video", available_video, str(FILTER_CUSTOM_RESOLUTION), downloadable_video])
            
            # Subtitle information
            available_subtitles = self.parser._subtitle.get_all_uris_and_names() or []
            if available_subtitles:
                available_sub_languages = [sub.get('language') for sub in available_subtitles]
                available_subs = ', '.join(available_sub_languages) if available_sub_languages else "Nothing"
                downloadable_sub_languages = [sub.get('language') for sub in self.sub_streams]
                downloadable_subs = ', '.join(downloadable_sub_languages) if downloadable_sub_languages else "Nothing"
                
                data_rows.append(["Subtitle", available_subs, ', '.join(DOWNLOAD_SPECIFIC_SUBTITLE), downloadable_subs])

            # Audio information
            available_audio = self.parser._audio.get_all_uris_and_names() or []
            if available_audio:
                available_audio_languages = [audio.get('language') for audio in available_audio]
                available_audios = ', '.join(available_audio_languages)
                downloadable_audio_languages = [audio.get('language') for audio in self.audio_streams]
                downloadable_audios = ', '.join(downloadable_audio_languages) if downloadable_audio_languages else "Nothing"
                
                data_rows.append(["Audio", available_audios, ', '.join(DOWNLOAD_SPECIFIC_AUDIO), downloadable_audios])
            
            # Calculate max width for each column
            headers = ["Type", "Available", "Set", "Downloadable"]
            max_widths = [len(header) for header in headers]
            
            for row in data_rows:
                for i, cell in enumerate(row):
                    max_widths[i] = max(max_widths[i], len(str(cell)))
            
            # Add some padding
            max_widths = [w + 2 for w in max_widths]
            
            return data_rows, max_widths
        
        data_rows, column_widths = calculate_column_widths()
        
        table = Table(show_header=True, header_style="cyan", border_style="blue")
        table.add_column("Type", style="cyan bold", width=column_widths[0])
        table.add_column("Available", style="green", width=column_widths[1])
        table.add_column("Set", style="red", width=column_widths[2])
        table.add_column("Downloadable", style="yellow", width=column_widths[3])
        
        for row in data_rows:
            table.add_row(*row)

        console.print(table)
        print("")


class DownloadManager:
    """Manages downloading of video, audio, and subtitle streams."""
    def __init__(self, temp_dir: str, client: HLSClient, url_fixer: M3U8_UrlFix, license_url: Optional[str] = None, custom_headers: Optional[Dict[str, str]] = None):
        """
        Args:
            temp_dir: Directory for storing temporary files
            client: HLSClient instance for making requests
            url_fixer: URL fixer instance for generating complete URLs
            custom_headers: Optional custom headers to use for all requests
        """
        self.temp_dir = temp_dir
        self.client = client
        self.url_fixer = url_fixer
        self.license_url = license_url
        self.custom_headers = custom_headers
        self.missing_segments = []
        self.stopped = False
        self.video_segments_count = 0
        self.video_output_path = None
        self.audio_output_paths = {}

        # For progress tracking
        self.current_downloader: Optional[M3U8_Segments] = None
        self.current_download_type: Optional[str] = None

    def download_video(self, video_url: str) -> bool:
        """
        Downloads video segments from the M3U8 playlist.
        
        Returns:
            bool: True if download was successful, False otherwise
        """
        video_full_url = self.url_fixer.generate_full_url(video_url)
        video_tmp_dir = os.path.join(self.temp_dir, 'video')

        # Create downloader without segment limit for video
        downloader = M3U8_Segments(
            url=video_full_url,
            tmp_folder=video_tmp_dir,
            license_url=self.license_url,
            custom_headers=self.custom_headers
        )

        # Set current downloader for progress tracking
        self.current_downloader = downloader
        self.current_download_type = 'video'
        
        # Download video and get segment count
        result = downloader.download_streams("Video", "video")
        self.video_segments_count = downloader.get_segments_count()
        self.missing_segments.append(result)
        
        # Store the actual output path from the result
        if 'output_path' in result and result['output_path']:
            self.video_output_path = result['output_path']

        # Reset current downloader after completion
        self.current_downloader = None
        self.current_download_type = None

        if result.get('stopped', False):
            self.stopped = True
            return False

        return True

    def download_audio(self, audio: Dict) -> bool:
        """
        Downloads audio segments for a specific language track.
        Uses video segment count as a limit if available.
        
        Returns:
            bool: True if download was successful, False otherwise
        """
        try:
            audio_full_url = self.url_fixer.generate_full_url(audio['uri'])
            audio_tmp_dir = os.path.join(self.temp_dir, 'audio', audio['language'])
            
            # Create downloader with segment limit for audio
            downloader = M3U8_Segments(
                url=audio_full_url, 
                tmp_folder=audio_tmp_dir,
                license_url=self.license_url,
                limit_segments=self.video_segments_count if self.video_segments_count > 0 else None,
                custom_headers=self.custom_headers
            )

            # Set current downloader for progress tracking
            self.current_downloader = downloader
            self.current_download_type = f"audio_{audio['language']}"

            # Download audio
            result = downloader.download_streams(f"Audio {audio['language']}", "audio")
            self.missing_segments.append(result)
            
            # Store the actual output path from the result
            if 'output_path' in result and result['output_path']:
                self.audio_output_paths[audio['language']] = result['output_path']

            # Reset current downloader after completion
            self.current_downloader = None
            self.current_download_type = None

            if result.get('stopped', False):
                self.stopped = True
                return False
                
            return True
        
        except Exception as e:
            logging.error(f"Error downloading audio {audio.get('language', 'unknown')}: {str(e)}")
            self.current_downloader = None
            self.current_download_type = None
            return False

    def download_subtitle(self, sub: Dict) -> bool:
        """
        Downloads and saves subtitle file for a specific language.
        
        Returns:
            bool: True if download was successful, False otherwise
        """
        try:
            raw_content = self.client.request(sub['uri'])
            if raw_content:
                sub_path = os.path.join(self.temp_dir, 'subs', f"{sub['language']}.vtt")

                subtitle_parser = M3U8_Parser()
                subtitle_parser.parse_data(sub['uri'], raw_content)

                with open(sub_path, 'wb') as f:
                    vtt_url = subtitle_parser.subtitle[-1]
                    vtt_content = self.client.request(vtt_url, True)
                    if vtt_content:
                        f.write(vtt_content)
                        return True
                    
            return False
        
        except Exception as e:
            logging.error(f"Error downloading subtitle {sub.get('language', 'unknown')}: {str(e)}")
            return False

    def download_all(self, video_url: str, audio_streams: List[Dict], sub_streams: List[Dict]) -> bool:
        """
        Downloads all selected streams (video, audio, subtitles).
        For multiple downloads, continues even if individual downloads fail.
        
        Returns:
            bool: True if any critical download failed and should stop processing
        """
        critical_failure = False

        # Download video (this is critical)
        if not self.download_video(video_url):
            logging.error("Critical failure: Video download failed")
            critical_failure = True

        # Download audio streams (continue even if some fail)
        for audio in audio_streams:
            if self.stopped:
                break

            success = self.download_audio(audio)
            if not success:
                logging.warning(f"Audio download failed for language {audio.get('language', 'unknown')}, continuing...")

        # Download subtitle streams (continue even if some fail)
        for sub in sub_streams:
            if self.stopped:
                break

            success = self.download_subtitle(sub)
            if not success:
                logging.warning(f"Subtitle download failed for language {sub.get('language', 'unknown')}, continuing...")

        return critical_failure or self.stopped


class MergeManager:
    """Handles merging of video, audio, and subtitle streams."""
    def __init__(self, temp_dir: str, parser: M3U8_Parser, audio_streams: List[Dict], sub_streams: List[Dict], video_output_path: Optional[str] = None, audio_output_paths: Optional[Dict[str, str]] = None):
        """
        Args:
            temp_dir: Directory containing temporary files
            parser: M3U8 parser instance with codec information
            audio_streams: List of audio streams to merge
            sub_streams: List of subtitle streams to merge
        """
        self.temp_dir = temp_dir
        self.parser = parser
        self.audio_streams = audio_streams
        self.sub_streams = sub_streams
        self.video_output_path = video_output_path
        self.audio_output_paths = audio_output_paths or {}

    def _get_video_file(self) -> str:
        """Get the actual video file path from the provided output path."""
        if self.video_output_path and os.path.exists(self.video_output_path):
            return self.video_output_path
        
        # Fallback to check common locations if output_path wasn't set
        default_ts = os.path.join(self.temp_dir, 'video', '0.ts')
        if os.path.exists(default_ts):
            return default_ts
        
        default_mp4 = os.path.join(self.temp_dir, 'video', 'video_decrypted.mp4')
        if os.path.exists(default_mp4):
            return default_mp4
        
        raise FileNotFoundError(f"Video file not found. Expected at: {self.video_output_path}")

    def _get_audio_file(self, language: str) -> Optional[str]:
        """Get the actual audio file path for a given language from the stored output path."""
        if language in self.audio_output_paths:
            audio_path = self.audio_output_paths[language]
            if os.path.exists(audio_path):
                return audio_path
            else:
                logging.warning(f"Audio file not found at stored path: {audio_path}")
        
        # Fallback to check common locations if output_path wasn't stored
        default_ts = os.path.join(self.temp_dir, 'audio', language, '0.ts')
        if os.path.exists(default_ts):
            return default_ts
        
        default_mp4 = os.path.join(self.temp_dir, 'audio', language, 'audio_decrypted.mp4')
        if os.path.exists(default_mp4):
            return default_mp4
        
        return None

    def merge(self) -> tuple[str, bool]:
        """
        Merges downloaded streams into final video file.
        Returns path to the final merged file and use_shortest flag.

        Process:
        1. If no audio/subs, just process video
        2. If audio exists, merge with video
        3. If subtitles exist, add them to the video
        """
        video_file = self._get_video_file()
        merged_file = video_file
        use_shortest = False

        if not self.audio_streams and not self.sub_streams:
            merged_file = join_video(
                video_path=video_file,
                out_path=os.path.join(self.temp_dir, f'video.{EXTENSION_OUTPUT}'),
                codec=self.parser.codec
            )

        else:
            if self.audio_streams:

                # Only include audio tracks that actually exist
                existing_audio_tracks = []
                for a in self.audio_streams:
                    audio_path = self._get_audio_file(a['language'])
                    if audio_path and os.path.exists(audio_path):
                        existing_audio_tracks.append({
                            'path': audio_path,
                            'name': a['language']
                        })

                if existing_audio_tracks:
                    merged_audio_path = os.path.join(self.temp_dir, f'merged_audio.{EXTENSION_OUTPUT}')
                    merged_file, use_shortest = join_audios(
                        video_path=video_file,
                        audio_tracks=existing_audio_tracks,
                        out_path=merged_audio_path,
                        codec=self.parser.codec
                    )

            if MERGE_SUBTITLE and self.sub_streams:

                # Only include subtitle tracks that actually exist
                existing_sub_tracks = []
                for s in self.sub_streams:
                    sub_path = os.path.join(self.temp_dir, 'subs', f"{s['language']}.vtt")
                    if os.path.exists(sub_path):
                        existing_sub_tracks.append({
                            'path': sub_path,
                            'language': s['language']
                        })

                if existing_sub_tracks:
                    merged_subs_path = os.path.join(self.temp_dir, f'final.{EXTENSION_OUTPUT}')
                    merged_file = join_subtitle(
                        video_path=merged_file,
                        subtitles_list=existing_sub_tracks,
                        out_path=merged_subs_path
                    )

        return merged_file, use_shortest


class HLS_Downloader:
    """Main class for HLS video download and processing."""
    def __init__(self, m3u8_url: str, license_url: Optional[str] = None, output_path: Optional[str] = None, headers: Optional[Dict[str, str]] = None):
        """
        Initializes the HLS_Downloader with parameters.
        """
        self.m3u8_url = str(m3u8_url).strip()
        self.license_url = str(license_url).strip() if license_url else None
        self.path_manager = PathManager(m3u8_url, output_path)
        self.custom_headers = headers
        self.client = HLSClient(custom_headers=self.custom_headers)
        self.m3u8_manager = M3U8Manager(m3u8_url, self.client)
        self.download_manager: Optional[DownloadManager] = None
        self.merge_manager: Optional[MergeManager] = None

    def start(self) -> Dict[str, Any]:
        """
        Main execution flow with handling for both index and playlist M3U8s.
        Returns False for this download and continues with the next one in case of failure.

        Returns:
            Dict containing:
                - path: Output file path
                - url: Original M3U8 URL
                - is_master: Whether the M3U8 was a master playlist
                - msg: Status message
                - error: Error message if any
                - stopped: Whether download was stopped
        """

        if GET_ONLY_LINK:
            console.print(f"URL: [red]{self.m3u8_url}")
            return {
                'path': None,
                'url': self.m3u8_url,
                'is_master': getattr(self.m3u8_manager, 'is_master', None),
                'msg': None,
                'error': None,
                'stopped': True
            }

        try:
            if os.path.exists(self.path_manager.output_path):
                console.print(f"[red]Output file {self.path_manager.output_path} already exists!")
                response = {
                    'path': self.path_manager.output_path,
                    'url': self.m3u8_url,
                    'is_master': False,
                    'msg': 'File already exists',
                    'error': None,
                    'stopped': False
                }
                return response

            self.path_manager.setup_directories()

            # Parse M3U8 and determine if it's a master playlist
            self.m3u8_manager.parse()
            self.m3u8_manager.select_streams()

            if self.m3u8_manager.is_master:
                logging.info("Detected media playlist (not master)")
                self.m3u8_manager.log_selection()

            self.download_manager = DownloadManager(
                temp_dir=self.path_manager.temp_dir,
                client=self.client,
                url_fixer=self.m3u8_manager.url_fixer,
                license_url=self.license_url,
                custom_headers=self.custom_headers
            )

            # Check if download had critical failures
            download_failed = self.download_manager.download_all(
                video_url=self.m3u8_manager.video_url,
                audio_streams=self.m3u8_manager.audio_streams,
                sub_streams=self.m3u8_manager.sub_streams
            )

            if download_failed:
                error_msg = "Critical download failure occurred"
                console.print(f"[red]Download failed: {error_msg}")
                self.path_manager.cleanup()
                return {
                    'path': None,
                    'url': self.m3u8_url,
                    'is_master': self.m3u8_manager.is_master,
                    'msg': None,
                    'error': error_msg,
                    'stopped': self.download_manager.stopped
                }

            self.merge_manager = MergeManager(
                temp_dir=self.path_manager.temp_dir,
                parser=self.m3u8_manager.parser,
                audio_streams=self.m3u8_manager.audio_streams,
                sub_streams=self.m3u8_manager.sub_streams,
                video_output_path=self.download_manager.video_output_path,
                audio_output_paths=self.download_manager.audio_output_paths
            )

            final_file, use_shortest = self.merge_manager.merge()
            self.path_manager.move_final_file(final_file)
            self._print_summary(use_shortest)
            self.path_manager.cleanup()

            return {
                'path': self.path_manager.output_path,
                'url': self.m3u8_url,
                'is_master': self.m3u8_manager.is_master,
                'msg': 'Download completed successfully',
                'error': None,
                'stopped': self.download_manager.stopped
            }

        except KeyboardInterrupt:
            console.print("\n[yellow]Download interrupted by user")
            self.path_manager.cleanup()
            return {
                'path': None,
                'url': self.m3u8_url,
                'is_master': getattr(self.m3u8_manager, 'is_master', None),
                'msg': 'Download interrupted by user',
                'error': None,
                'stopped': True
            }

        except Exception as e:
            error_msg = str(e)
            console.print(f"[red]Download failed: {error_msg}")
            logging.error(f"Download error for {self.m3u8_url}", exc_info=True)

            # Cleanup on error
            self.path_manager.cleanup()

            return {
                'path': None,
                'url': self.m3u8_url,
                'is_master': getattr(self.m3u8_manager, 'is_master', None),
                'msg': None,
                'error': error_msg,
                'stopped': False
            }

    def _print_summary(self, use_shortest: bool):
        """Prints download summary including file size, duration, and any missing segments."""
        missing_ts = False
        missing_info = ""

        for item in self.download_manager.missing_segments:
            if int(item['nFailed']) >= 1:
                missing_ts = True
                missing_info += f"[red]TS Failed: {item['nFailed']} {item['type']} tracks"

        file_size = internet_manager.format_file_size(os.path.getsize(self.path_manager.output_path))
        duration = print_duration_table(self.path_manager.output_path, description=False, return_string=True)

        # Rename output file if there were missing segments or shortest used
        new_filename = self.path_manager.output_path
        if missing_ts and use_shortest:
            new_filename = new_filename.replace(EXTENSION_OUTPUT, f"_failed_sync_ts.{EXTENSION_OUTPUT}")
        elif missing_ts:
            new_filename = new_filename.replace(EXTENSION_OUTPUT, f"_failed_ts.{EXTENSION_OUTPUT}")
        elif use_shortest:
            new_filename = new_filename.replace(EXTENSION_OUTPUT, f"_failed_sync.{EXTENSION_OUTPUT}")

        # Rename the file accordingly
        if missing_ts or use_shortest:
            os.rename(self.path_manager.output_path, new_filename)
            self.path_manager.output_path = new_filename

        # Display file information
        console.print(f"[yellow]Output[white]: [red]{os.path.abspath(self.path_manager.output_path)} \n"
            f"  [cyan]with size[white]: [red]{file_size} \n"
            f"      [cyan]and duration[white]: [red]{duration}")

    def get_progress_data(self) -> Optional[Dict]:
        """Get current download progress data."""
        if not self.download_manager.current_downloader:
            return None

        try:
            progress = self.download_manager.current_downloader.get_progress_data()
            if progress:
                progress['download_type'] = self.download_manager.current_download_type
            return progress
            
        except Exception as e:
            logging.error(f"Error getting progress data: {e}")
            return None