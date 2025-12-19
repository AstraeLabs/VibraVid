# 18.04.24

import os
import time
import logging
import binascii
import asyncio
from urllib.parse import urljoin, urlparse
from typing import Dict, Optional


# External libraries
import httpx
from tqdm import tqdm
from rich.console import Console


# Internal utilities
from StreamingCommunity.Util import config_manager, Colors
from StreamingCommunity.Util.http_client import create_client_curl, get_userAgent
from StreamingCommunity.Util.os import get_wvd_path



# Logic class
from .decrypt import M3U8_Decryption
from .estimator import M3U8_Ts_Estimator
from .parser import M3U8_Parser
from .url_fixer import M3U8_UrlFix


# External
from ..MP4 import MP4_Downloader
from ..DASH.cdm_helpher import get_widevine_keys
from ..DASH.decrypt import decrypt_with_mp4decrypt


# Config
REQUEST_MAX_RETRY = config_manager.get_int('REQUESTS', 'max_retry')
REQUEST_VERIFY = config_manager.get_bool('REQUESTS', 'verify')
DEFAULT_VIDEO_WORKERS = config_manager.get_int('M3U8_DOWNLOAD', 'default_video_workers')
DEFAULT_AUDIO_WORKERS = config_manager.get_int('M3U8_DOWNLOAD', 'default_audio_workers')
MAX_TIMEOUT = config_manager.get_int("REQUESTS", "timeout")
SEGMENT_MAX_TIMEOUT = config_manager.get_int("M3U8_DOWNLOAD", "segment_timeout")
LIMIT_SEGMENT = config_manager.get_int('M3U8_DOWNLOAD', 'limit_segment')
ENABLE_RETRY = config_manager.get_bool('M3U8_DOWNLOAD', 'enable_retry')


# Variable
console = Console()


class M3U8_Segments:
    def __init__(self, url: str, tmp_folder: str, license_url: Optional[str] = None, is_index_url: bool = True, limit_segments: int = None, custom_headers: Optional[Dict[str, str]] = None):
        """
        Initializes the M3U8_Segments object.

        Parameters:
            - url (str): The URL of the M3U8 playlist.
            - tmp_folder (str): The temporary folder to store downloaded segments.
            - is_index_url (bool): Flag indicating if url is a URL (default True).
            - limit_segments (int): Optional limit for number of segments (overrides LIMIT_SEGMENT if provided).
            - custom_headers (Dict[str, str]): Optional custom headers to use for all requests.
        """
        self.url = url
        self.tmp_folder = tmp_folder
        self.license_url = license_url
        self.is_index_url = is_index_url
        self.custom_headers = custom_headers if custom_headers else {'User-Agent': get_userAgent()}
        self.final_output_path = os.path.join(self.tmp_folder, "0.ts")
        self.drm_method = None
        os.makedirs(self.tmp_folder, exist_ok=True)

        # Use LIMIT_SEGMENT from config if limit_segments not specified or is 0
        if limit_segments is None or limit_segments == 0:
            self.limit_segments = LIMIT_SEGMENT if LIMIT_SEGMENT > 0 else None
        else:
            self.limit_segments = limit_segments
            
        self.enable_retry = ENABLE_RETRY

        # Util class
        self.decryption: M3U8_Decryption = None 
        self.class_ts_estimator = M3U8_Ts_Estimator(0, self) 
        self.class_url_fixer = M3U8_UrlFix(url)

        # Stats
        self.downloaded_segments = set()
        self.download_interrupted = False
        self.info_maxRetry = 0
        self.info_nRetry = 0
        self.info_nFailed = 0

        # Progress throttling
        self._last_progress_update = 0
        self._progress_update_interval = 0.1

    def __get_key__(self, m3u8_parser: M3U8_Parser) -> bytes:
        """
        Fetches the encryption key from the M3U8 playlist.
        """
        if m3u8_parser.keys.get('drm') is None:
            key_uri = urljoin(self.url, m3u8_parser.keys.get('uri'))
            parsed_url = urlparse(key_uri)
            self.key_base_url = f"{parsed_url.scheme}://{parsed_url.netloc}/"

            try:
                response = create_client_curl(headers=self.custom_headers).get(key_uri)
                response.raise_for_status()

                hex_content = binascii.hexlify(response.content).decode('utf-8')
                console.log(f"[cyan]Fetch key from URI: [green]{key_uri}")
                return bytes.fromhex(hex_content)
                
            except Exception as e:
                raise Exception(f"Failed to fetch key: {e}")
            
        else:
            self.drm_method = m3u8_parser.keys.get('method')
            logging.info("DRM key detected, method: " + str(m3u8_parser.keys.get('method')))
        
    def parse_data(self, m3u8_content: str) -> None:
        """Parses the M3U8 content and extracts necessary data."""
        m3u8_parser = M3U8_Parser()
        m3u8_parser.parse_data(uri=self.url, raw_content=m3u8_content)

        self.expected_real_time_s = m3u8_parser.duration
        self.segment_init_url = m3u8_parser.init_segment
        self.has_init_segment = self.segment_init_url is not None

        if m3u8_parser.keys:
            key = self.__get_key__(m3u8_parser)
            self.decryption = M3U8_Decryption(key, m3u8_parser.keys.get('iv'), m3u8_parser.keys.get('method'), m3u8_parser.keys.get('pssh'))

        segments = [
            self.class_url_fixer.generate_full_url(seg) if "http" not in seg else seg
            for seg in m3u8_parser.segments
        ]
        
        # Apply segment limit
        if self.limit_segments and len(segments) > self.limit_segments:
            logging.info(f"Limiting segments from {len(segments)} to {self.limit_segments}")
            segments = segments[:self.limit_segments]
            
        self.segments = segments
        self.stream_type = self.get_type_stream(self.segments)
        self.class_ts_estimator.total_segments = len(self.segments)
        console.log(f"[cyan]Detected stream type: [green]{self.stream_type}")
        
    def get_segments_count(self) -> int:
        """
        Returns the total number of segments.
        """
        return len(self.segments) if hasattr(self, 'segments') else 0
    
    def get_type_stream(self, segments) -> str:
        self.is_stream_ts = (".ts" in self.segments[len(self.segments) // 2]) if self.segments else False
        self.is_stream_mp4 = (".mp4" in self.segments[len(self.segments) // 2]) if self.segments else False
        self.is_stream_aac = (".aac" in self.segments[len(self.segments) // 2]) if self.segments else False

        if self.is_stream_ts:
            return "ts"
        elif self.is_stream_mp4:
            return "mp4"
        elif self.is_stream_aac:
            return "aac"
        else:
            return None

    def get_info(self) -> None:
        """
        Retrieves M3U8 playlist information from the given URL.
        """
        if self.is_index_url:
            response = create_client_curl(headers=self.custom_headers).get(self.url)
            response.raise_for_status()
            
            self.parse_data(response.text)
            with open(os.path.join(self.tmp_folder, "playlist.m3u8"), "w", encoding='utf-8') as f:
                f.write(response.text)
                    
    def _throttled_progress_update(self, content_size: int, progress_bar: tqdm):
        """
        Throttled progress update to reduce CPU usage.
        """
        current_time = time.time()
        if current_time - self._last_progress_update > self._progress_update_interval:
            self.class_ts_estimator.update_progress_bar(content_size, progress_bar)
            self._last_progress_update = current_time

    def _get_temp_segment_path(self, temp_dir: str, index: int) -> str:
        """
        Get the file path for a temporary segment.
        """
        return os.path.join(temp_dir, f"seg_{index:06d}.ts")

    async def _download_init_segment(self, client: httpx.AsyncClient, output_path: str, progress_bar: tqdm) -> bool:
        """
        Downloads the initialization segment and writes to output file.
        """
        if not self.has_init_segment:
            with open(output_path, 'wb') as f:
                pass
            return False
            
        init_url = self.segment_init_url
        if not init_url.startswith("http"):
            init_url = self.class_url_fixer.generate_full_url(init_url)
            
        try:
            response = await client.get(init_url, timeout=SEGMENT_MAX_TIMEOUT, headers=self.custom_headers)
            response.raise_for_status()
            init_content = response.content
            
            # Decrypt if needed
            if self.decryption is not None:
                try:
                    init_content = self.decryption.decrypt(init_content)
                except Exception as e:
                    logging.error(f"Decryption failed for init segment: {str(e)}")
                    return False
            
            # Write init segment to output file
            with open(output_path, 'wb') as f:
                f.write(init_content)
            
            progress_bar.update(1)
            self._throttled_progress_update(len(init_content), progress_bar)
            logging.info("Init segment downloaded successfully")
            return True
            
        except Exception as e:
            logging.error(f"Failed to download init segment: {str(e)}")
            with open(output_path, 'wb') as f:
                pass
            return False

    async def _download_single_segment(self, client: httpx.AsyncClient, ts_url: str, index: int, temp_dir: str, semaphore: asyncio.Semaphore, max_retry: int) -> tuple:
        """
        Downloads a single TS segment and saves to temp file IMMEDIATELY.

        Returns:
            tuple: (index, success, retry_count, file_size)
        """
        async with semaphore:
            temp_file = self._get_temp_segment_path(temp_dir, index)
            
            for attempt in range(max_retry):
                if self.download_interrupted:
                    return index, False, attempt, 0
                
                try:
                    timeout = min(SEGMENT_MAX_TIMEOUT, 10 + attempt * 5)
                    response = await client.get(ts_url, timeout=timeout, headers=self.custom_headers, follow_redirects=True)
                    response.raise_for_status()
                    segment_content = response.content

                    # Decrypt if needed
                    if self.decryption is not None:
                        try:
                            segment_content = self.decryption.decrypt(segment_content)
                        except Exception as e:
                            logging.error(f"Decryption failed for segment {index}: {str(e)}")
                            if attempt + 1 == max_retry:
                                return index, False, attempt, 0
                            raise e

                    # Write segment to temp file IMMEDIATELY
                    with open(temp_file, 'wb') as f:
                        f.write(segment_content)
                    
                    size = len(segment_content)
                    del segment_content
                    return index, True, attempt, size

                except Exception:
                    if attempt + 1 == max_retry:
                        console.print(f" -- [red]Final retry failed for segment: {index}")
                        return index, False, max_retry, 0
                    
                    sleep_time = 0.5 + attempt * 0.5 if attempt < 2 else min(3.0, 1.02 ** attempt)
                    await asyncio.sleep(sleep_time)
            
            return index, False, max_retry, 0

    async def _download_all_segments(self, client: httpx.AsyncClient, temp_dir: str, semaphore: asyncio.Semaphore, progress_bar: tqdm):
        """
        Download all segments in parallel with automatic retry.
        """
        
        # First pass: download all segments
        tasks = [
            self._download_single_segment(client, url, i, temp_dir, semaphore, REQUEST_MAX_RETRY)
            for i, url in enumerate(self.segments)
        ]
        
        for coro in asyncio.as_completed(tasks):
            try:
                idx, success, nretry, size = await coro
                
                if success:
                    self.downloaded_segments.add(idx)
                else:
                    self.info_nFailed += 1
                
                if nretry > self.info_maxRetry:
                    self.info_maxRetry = nretry
                self.info_nRetry += nretry
                
                progress_bar.update(1)
                self._throttled_progress_update(size, progress_bar)
                
            except KeyboardInterrupt:
                self.download_interrupted = True
                console.print("\n[red]Download interrupted by user (Ctrl+C).")
                break

        # Retry failed segments only if enabled
        if self.enable_retry and not self.download_interrupted:
            await self._retry_failed_segments(client, temp_dir, semaphore, progress_bar)

    async def _retry_failed_segments(self, client: httpx.AsyncClient, temp_dir: str, semaphore: asyncio.Semaphore, progress_bar: tqdm):
        """
        Retry failed segments up to 3 times.
        """
        max_global_retries = 3
        global_retry_count = 0

        while self.info_nFailed > 0 and global_retry_count < max_global_retries and not self.download_interrupted:
            failed_indices = [i for i in range(len(self.segments)) if i not in self.downloaded_segments]
            if not failed_indices:
                break

            console.print(f" -- [yellow]Retrying {len(failed_indices)} failed segments (attempt {global_retry_count+1}/{max_global_retries})...")
            
            retry_tasks = [
                self._download_single_segment(client, self.segments[i], i, temp_dir, semaphore, REQUEST_MAX_RETRY)
                for i in failed_indices
            ]
            
            nFailed_this_round = 0
            for coro in asyncio.as_completed(retry_tasks):
                try:
                    idx, success, nretry, size = await coro

                    if success:
                        self.downloaded_segments.add(idx)
                    else:
                        nFailed_this_round += 1

                    if nretry > self.info_maxRetry:
                        self.info_maxRetry = nretry
                    self.info_nRetry += nretry
                    
                    progress_bar.update(0)
                    self._throttled_progress_update(size, progress_bar)

                except KeyboardInterrupt:
                    self.download_interrupted = True
                    console.print("\n[red]Download interrupted by user (Ctrl+C).")
                    break
                    
            self.info_nFailed = nFailed_this_round
            global_retry_count += 1

    async def _concatenate_segments(self, output_path: str, temp_dir: str):
        """
        Concatenate all segment files in order to the final output file.
        """
        with open(output_path, 'ab') as outfile:
            for idx in range(len(self.segments)):
                temp_file = self._get_temp_segment_path(temp_dir, idx)
                
                if os.path.exists(temp_file):
                    with open(temp_file, 'rb') as infile:
                        outfile.write(infile.read())
                    os.remove(temp_file)

    async def download_segments_async(self, description: str, type: str):
        """
        Downloads all TS segments asynchronously.

        Parameters:
            - description: Description to insert on tqdm bar
            - type (str): Type of download: 'video' or 'audio'
        """
        self.get_info()

        # Setup directories
        temp_dir = os.path.join(self.tmp_folder, "segments_temp")
        os.makedirs(temp_dir, exist_ok=True)

        if self.stream_type in ["ts", "aac"]:

            # Initialize progress bar
            total_segments = len(self.segments) + (1 if self.has_init_segment else 0)
            progress_bar = tqdm(
                total=total_segments,
                bar_format=self._get_bar_format(description)
            )

            # Reset stats
            self.downloaded_segments = set()
            self.info_nFailed = 0
            self.info_nRetry = 0
            self.info_maxRetry = 0
            self.download_interrupted = False

            try:
                # Configure HTTP client
                timeout_config = httpx.Timeout(SEGMENT_MAX_TIMEOUT, connect=10.0)
                limits = httpx.Limits(max_keepalive_connections=20, max_connections=100)
                
                async with httpx.AsyncClient(timeout=timeout_config, limits=limits, verify=REQUEST_VERIFY) as client:
                    
                    # Download init segment first (writes to 0.ts)
                    await self._download_init_segment(client, self.final_output_path, progress_bar)
                    
                    # Determine worker count based on type
                    max_workers = self._get_worker_count(type)
                    semaphore = asyncio.Semaphore(max_workers)
                    
                    # Update estimator
                    self.class_ts_estimator.total_segments = len(self.segments)
                    
                    # Download all segments to temp files
                    await self._download_all_segments(client, temp_dir, semaphore, progress_bar)
                    
                    # Concatenate all segments to 0.ts
                    if not self.download_interrupted:
                        await self._concatenate_segments(self.final_output_path, temp_dir)

            except KeyboardInterrupt:
                self.download_interrupted = True
                console.print("\n[red]Download interrupted by user (Ctrl+C).")
                
            finally:
                self._cleanup_resources(temp_dir, progress_bar)

            if not self.download_interrupted:
                self._verify_download_completion()

            return self._generate_results(type)
        
        else:
            # DRM
            if self.decryption is not None:

                # Get Widevine keys
                content_keys = get_widevine_keys(self.decryption.pssh, self.license_url, get_wvd_path())

                # Download encrypted MP4 file
                encrypted_file, kill = MP4_Downloader(
                    url = self.segments[0],
                    path=os.path.join(self.tmp_folder, "encrypted.mp4"),
                    headers_=self.custom_headers,
                    show_final_info=False
                )

                # Decrypt MP4 file
                KID = content_keys[0]['kid']
                KEY = content_keys[0]['key']
                decrypted_file = os.path.join(self.tmp_folder, f"{type}_decrypted.mp4")
                mp4_output = decrypt_with_mp4decrypt("MP4", encrypted_file, KID, KEY, decrypted_file)
                
                return self._generate_results(type, mp4_output)
            
            # NOT DRM
            else:
                if len(self.segments) == 0:
                    console.print("[red]No segments found to download.")
                    return self._generate_results(type)
                
                decrypted_file, kill = MP4_Downloader(
                    url = self.segments[0],
                    path=os.path.join(self.tmp_folder, f"{type}_decrypted.mp4"),
                    headers_=self.custom_headers,
                    show_final_info=False
                )
                return self._generate_results(type, decrypted_file)


    def download_streams(self, description: str, type: str):
        """
        Synchronous wrapper for download_segments_async.

        Parameters:
            - description: Description to insert on tqdm bar
            - type (str): Type of download: 'video' or 'audio'
        """
        try:
            return asyncio.run(self.download_segments_async(description, type))
        
        except KeyboardInterrupt:
            self.download_interrupted = True
            console.print("\n[red]Download interrupted by user (Ctrl+C).")
            return self._generate_results(type)

    def _get_bar_format(self, description: str) -> str:
        """Generate platform-appropriate progress bar format."""
        return (
            f"{Colors.YELLOW}HLS{Colors.CYAN} {description}{Colors.WHITE}: "
            f"{Colors.MAGENTA}{{bar:40}} "
            f"{Colors.LIGHT_GREEN}{{n_fmt}}{Colors.WHITE}/{Colors.CYAN}{{total_fmt}} {Colors.LIGHT_MAGENTA}TS {Colors.WHITE}"
            f"{Colors.DARK_GRAY}[{Colors.YELLOW}{{elapsed}}{Colors.WHITE} < {Colors.CYAN}{{remaining}}{Colors.DARK_GRAY}] "
            f"{Colors.WHITE}{{postfix}}"
        )
    
    def _get_worker_count(self, stream_type: str) -> int:
        """Return parallel workers based on stream type."""
        return {
            'video': DEFAULT_VIDEO_WORKERS,
            'audio': DEFAULT_AUDIO_WORKERS
        }.get(stream_type.lower(), 1)
    
    def _generate_results(self, stream_type: str, output_path: str = None) -> Dict:
        """Package final download results."""
        return {
            'type': stream_type,
            'nFailed': self.info_nFailed,
            'stopped': self.download_interrupted,
            'stream': self.stream_type,
            'drm': self.drm_method,
            'output_path': output_path if output_path else self.final_output_path
        }
    
    def _verify_download_completion(self) -> None:
        """Validate final download integrity."""
        total = len(self.segments)
        if len(self.downloaded_segments) / total < 0.999:
            missing = sorted(set(range(total)) - self.downloaded_segments)
            raise RuntimeError(f"Download incomplete ({len(self.downloaded_segments)/total:.1%}). Missing segments: {missing}")
        
    def _cleanup_resources(self, temp_dir: str, progress_bar: tqdm) -> None:
        """Ensure resource cleanup and final reporting."""
        progress_bar.close()
        
        # Delete temp directory if exists
        if temp_dir and os.path.exists(temp_dir):
            try:
                # Remove any remaining files (in case of interruption)
                for file in os.listdir(temp_dir):
                    os.remove(os.path.join(temp_dir, file))
                os.rmdir(temp_dir)
            except Exception as e:
                console.print(f"[yellow]Warning: Could not clean temp directory: {e}")
        
        if self.info_nFailed > 0:
            self._display_error_summary()

    def _display_error_summary(self) -> None:
        """Generate final error report."""
        console.print(f" [cyan]Max retries: [red]{self.info_maxRetry} [white] | "
            f"[cyan]Total retries: [red]{self.info_nRetry} [white] | "
            f"[cyan]Failed segments: [red]{self.info_nFailed}")
        
    def get_progress_data(self) -> Dict:
        """Returns current download progress data for API consumption."""
        total = self.get_segments_count()
        downloaded = len(self.downloaded_segments)
        percentage = (downloaded / total * 100) if total > 0 else 0
        stats = self.class_ts_estimator.get_stats(downloaded, total)
        
        return {
            'total_segments': total,
            'downloaded_segments': downloaded,
            'failed_segments': self.info_nFailed,
            'current_speed': stats['download_speed'],
            'estimated_size': stats['estimated_total_size'],
            'percentage': round(percentage, 2),
            'eta_seconds': stats['eta_seconds']
        }