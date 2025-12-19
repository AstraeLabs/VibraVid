# 25.07.25

import os
import asyncio
import time
from typing import Dict, Optional


# External libraries
import httpx
from tqdm import tqdm
from rich.console import Console


# Internal utilities
from StreamingCommunity.Util.http_client import get_userAgent
from StreamingCommunity.Lib.HLS.estimator import M3U8_Ts_Estimator
from StreamingCommunity.Util import config_manager, Colors


# Config
REQUEST_MAX_RETRY = config_manager.get_int('REQUESTS', 'max_retry')
DEFAULT_VIDEO_WORKERS = config_manager.get_int('M3U8_DOWNLOAD', 'default_video_workers')
DEFAULT_AUDIO_WORKERS = config_manager.get_int('M3U8_DOWNLOAD', 'default_audio_workers')
SEGMENT_MAX_TIMEOUT = config_manager.get_int("M3U8_DOWNLOAD", "segment_timeout")
LIMIT_SEGMENT = config_manager.get_int('M3U8_DOWNLOAD', 'limit_segment')
ENABLE_RETRY = config_manager.get_bool('M3U8_DOWNLOAD', 'enable_retry')
CLEANUP_TMP = config_manager.get_bool('M3U8_DOWNLOAD', 'cleanup_tmp_folder')


# Variable
console = Console()


class MPD_Segments:
    def __init__(self, tmp_folder: str, representation: dict, pssh: str = None, limit_segments: int = None):
        """
        Initialize MPD_Segments with temp folder, representation, optional pssh, and segment limit.
        
        Parameters:
            - tmp_folder (str): Temporary folder to store downloaded segments
            - representation (dict): Selected representation with segment URLs
            - pssh (str, optional): PSSH string for decryption
            - limit_segments (int, optional): Optional limit for number of segments to download
        """
        self.tmp_folder = tmp_folder
        self.selected_representation = representation
        self.pssh = pssh
        
        # Use LIMIT_SEGMENT from config if limit_segments is not specified or is 0
        if limit_segments is None or limit_segments == 0:
            self.limit_segments = LIMIT_SEGMENT if LIMIT_SEGMENT > 0 else None
        else:
            self.limit_segments = limit_segments
        
        self.enable_retry = ENABLE_RETRY
        self.download_interrupted = False
        self.info_nFailed = 0
        
        # OTHER INFO
        self.downloaded_segments = set()
        self.info_maxRetry = 0
        self.info_nRetry = 0
        
        # Progress
        self._last_progress_update = 0
        self._progress_update_interval = 0.1
        
        # Segment tracking - store only metadata, not content
        self.segment_status = {}  # {idx: {'downloaded': bool, 'size': int}}
        self.segments_lock = asyncio.Lock()
        
        # Estimator for progress tracking
        self.estimator: Optional[M3U8_Ts_Estimator] = None

    def get_concat_path(self, output_dir: str = None):
        """
        Get the path for the concatenated output file.
        """
        rep_id = self.selected_representation['id']
        return os.path.join(output_dir or self.tmp_folder, f"{rep_id}_encrypted.m4s")
        
    def get_segments_count(self) -> int:
        """
        Returns the total number of segments available in the representation.
        """
        return len(self.selected_representation.get('segment_urls', []))

    def download_streams(self, output_dir: str = None, description: str = "DASH"):
        """
        Synchronous wrapper for download_segments, compatible with legacy calls.
        
        Parameters:
            - output_dir (str): Output directory for segments
            - description (str): Description for progress bar (e.g., "Video", "Audio Italian")
        """
        concat_path = self.get_concat_path(output_dir)

        # Apply segment limit if specified
        if self.limit_segments is not None:
            orig_count = len(self.selected_representation.get('segment_urls', []))
            if orig_count > self.limit_segments:

                # Limit segment URLs
                self.selected_representation['segment_urls'] = self.selected_representation['segment_urls'][:self.limit_segments]

        # Run async download in sync mode
        try:
            asyncio.run(self.download_segments(output_dir=output_dir, description=description))

        except KeyboardInterrupt:
            self.download_interrupted = True
            console.print("\n[red]Download interrupted by user (Ctrl+C).")

        return {
            "concat_path": concat_path,
            "representation_id": self.selected_representation['id'],
            "pssh": self.pssh
        }

    async def download_segments(self, output_dir: str = None, concurrent_downloads: int = None, description: str = "DASH"):
        """
        Download segments to temporary files, then concatenate them in order.
        
        Parameters:
            - output_dir (str): Output directory for segments
            - concurrent_downloads (int): Number of concurrent downloads
            - description (str): Description for progress bar (e.g., "Video", "Audio Italian")
        """
        rep = self.selected_representation
        rep_id = rep['id']
        segment_urls = rep['segment_urls']
        init_url = rep.get('init_url')

        os.makedirs(output_dir or self.tmp_folder, exist_ok=True)
        concat_path = os.path.join(output_dir or self.tmp_folder, f"{rep_id}_encrypted.m4s")
        
        temp_dir = os.path.join(output_dir or self.tmp_folder, f"{rep_id}_segments")
        os.makedirs(temp_dir, exist_ok=True)

        # Determine stream type (video/audio) for progress bar
        stream_type = description
        if concurrent_downloads is None:
            worker_type = 'video' if 'Video' in description else 'audio'
            concurrent_downloads = self._get_worker_count(worker_type)

        progress_bar = tqdm(
            total=len(segment_urls) + 1,
            desc=f"Downloading {rep_id}",
            bar_format=self._get_bar_format(stream_type)
        )

        # Define semaphore for concurrent downloads
        semaphore = asyncio.Semaphore(concurrent_downloads)

        # Initialize estimator
        self.estimator = M3U8_Ts_Estimator(total_segments=len(segment_urls) + 1)

        self.segment_status = {}
        self.downloaded_segments = set()
        self.info_nFailed = 0
        self.download_interrupted = False
        self.info_nRetry = 0
        self.info_maxRetry = 0

        try:
            timeout_config = httpx.Timeout(SEGMENT_MAX_TIMEOUT, connect=10.0)
            limits = httpx.Limits(max_keepalive_connections=20, max_connections=100)
            
            async with httpx.AsyncClient(timeout=timeout_config, limits=limits) as client:
                
                # Download init segment
                await self._download_init_segment(client, init_url, concat_path, progress_bar)

                # Download all segments to temp files
                await self._download_segments_batch(
                    client, segment_urls, temp_dir, semaphore, REQUEST_MAX_RETRY, progress_bar
                )

                # Retry failed segments only if enabled
                if self.enable_retry:
                    await self._retry_failed_segments(
                        client, segment_urls, temp_dir, semaphore, REQUEST_MAX_RETRY, progress_bar
                    )

                # Concatenate all segments IN ORDER
                await self._concatenate_segments_in_order(temp_dir, concat_path, len(segment_urls))

        except KeyboardInterrupt:
            self.download_interrupted = True
            console.print("\n[red]Download interrupted by user (Ctrl+C).")

        finally:
            self._cleanup_resources(temp_dir, progress_bar)

        self._verify_download_completion()
        return self._generate_results(stream_type)

    async def _download_init_segment(self, client, init_url, concat_path, progress_bar):
        """
        Download the init segment and update progress/estimator.
        """
        if not init_url:
            with open(concat_path, 'wb') as outfile:
                pass
            return
        
        try:
            headers = {'User-Agent': get_userAgent()}
            response = await client.get(init_url, headers=headers, follow_redirects=True)

            with open(concat_path, 'wb') as outfile:
                if response.status_code == 200:
                    outfile.write(response.content)
                    if self.estimator:
                        self.estimator.add_ts_file(len(response.content))

            progress_bar.update(1)
            if self.estimator:
                self._throttled_progress_update(len(response.content), progress_bar)

        except Exception as e:
            progress_bar.close()
            raise RuntimeError(f"Error downloading init segment: {e}")

    def _throttled_progress_update(self, content_size: int, progress_bar):
        """
        Throttled progress update to reduce CPU usage.
        """
        current_time = time.time()
        if current_time - self._last_progress_update > self._progress_update_interval:
            if self.estimator:
                self.estimator.update_progress_bar(content_size, progress_bar)
            self._last_progress_update = current_time

    async def _download_segments_batch(self, client, segment_urls, temp_dir, semaphore, max_retry, progress_bar):
        """
        Download segments to temporary files - write immediately to disk, not memory.
        """
        async def download_single(url, idx):
            async with semaphore:
                headers = {'User-Agent': get_userAgent()}
                temp_file = os.path.join(temp_dir, f"seg_{idx:06d}.tmp")
                
                for attempt in range(max_retry):
                    if self.download_interrupted:
                        return idx, False, attempt, 0
                        
                    try:
                        timeout = min(SEGMENT_MAX_TIMEOUT, 10 + attempt * 3)
                        resp = await client.get(url, headers=headers, follow_redirects=True, timeout=timeout)

                        # Write directly to temp file
                        if resp.status_code == 200:
                            content_size = len(resp.content)
                            with open(temp_file, 'wb') as f:
                                f.write(resp.content)
                            
                            # Update status
                            async with self.segments_lock:
                                self.segment_status[idx] = {'downloaded': True, 'size': content_size}
                                self.downloaded_segments.add(idx)
                            
                            return idx, True, attempt, content_size
                        else:
                            if attempt < 2:
                                sleep_time = 0.5 + attempt * 0.5
                            else:
                                sleep_time = min(2.0, 1.1 * (2 ** attempt))
                            await asyncio.sleep(sleep_time)
                            
                    except Exception:
                        sleep_time = min(2.0, 1.1 * (2 ** attempt))
                        await asyncio.sleep(sleep_time)
                
                # Mark as failed
                async with self.segments_lock:
                    self.segment_status[idx] = {'downloaded': False, 'size': 0}
                        
                return idx, False, max_retry, 0

        # Download all segments concurrently
        tasks = [download_single(url, i) for i, url in enumerate(segment_urls)]

        for coro in asyncio.as_completed(tasks):
            try:
                idx, success, nretry, size = await coro
                
                if not success:
                    self.info_nFailed += 1
                
                if nretry > self.info_maxRetry:
                    self.info_maxRetry = nretry
                self.info_nRetry += nretry
                    
                progress_bar.update(1)
                if self.estimator:
                    self.estimator.add_ts_file(size)
                    self._throttled_progress_update(size, progress_bar)

            except KeyboardInterrupt:
                self.download_interrupted = True
                console.print("\n[red]Download interrupted by user (Ctrl+C).")
                break

    async def _retry_failed_segments(self, client, segment_urls, temp_dir, semaphore, max_retry, progress_bar):
        """
        Retry failed segments up to 3 times.
        """
        max_global_retries = 3
        global_retry_count = 0

        while self.info_nFailed > 0 and global_retry_count < max_global_retries and not self.download_interrupted:
            failed_indices = [i for i in range(len(segment_urls)) if i not in self.downloaded_segments]
            if not failed_indices:
                break
            
            async def download_single(url, idx):
                async with semaphore:
                    headers = {'User-Agent': get_userAgent()}
                    temp_file = os.path.join(temp_dir, f"seg_{idx:06d}.tmp")

                    for attempt in range(max_retry):
                        if self.download_interrupted:
                            return idx, False, attempt, 0
                            
                        try:
                            timeout = min(SEGMENT_MAX_TIMEOUT, 15 + attempt * 5)
                            resp = await client.get(url, headers=headers, timeout=timeout)
                            
                            # Write directly to temp file
                            if resp.status_code == 200:
                                content_size = len(resp.content)
                                with open(temp_file, 'wb') as f:
                                    f.write(resp.content)
                                
                                async with self.segments_lock:
                                    self.segment_status[idx] = {'downloaded': True, 'size': content_size}
                                    self.downloaded_segments.add(idx)
                                
                                return idx, True, attempt, content_size
                            else:
                                await asyncio.sleep(1.5 * (2 ** attempt))

                        except Exception:
                            await asyncio.sleep(1.5 * (2 ** attempt))
                            
                return idx, False, max_retry, 0

            retry_tasks = [download_single(segment_urls[i], i) for i in failed_indices]

            nFailed_this_round = 0
            for coro in asyncio.as_completed(retry_tasks):
                try:
                    idx, success, nretry, size = await coro

                    if not success:
                        nFailed_this_round += 1

                    if nretry > self.info_maxRetry:
                        self.info_maxRetry = nretry
                    self.info_nRetry += nretry
                    
                    progress_bar.update(0)
                    if self.estimator:
                        self.estimator.add_ts_file(size)
                        self._throttled_progress_update(size, progress_bar)

                except KeyboardInterrupt:
                    self.download_interrupted = True
                    console.print("\n[red]Download interrupted by user (Ctrl+C).")
                    break
                    
            self.info_nFailed = nFailed_this_round
            global_retry_count += 1

    async def _concatenate_segments_in_order(self, temp_dir, concat_path, total_segments):
        """
        Concatenate all segment files IN ORDER to the final output file.
        """
        with open(concat_path, 'ab') as outfile:
            for idx in range(total_segments):
                temp_file = os.path.join(temp_dir, f"seg_{idx:06d}.tmp")
                
                # Only concatenate successfully downloaded segments
                if idx in self.downloaded_segments and os.path.exists(temp_file):
                    with open(temp_file, 'rb') as infile:
                        
                        # Read and write in chunks to avoid memory issues
                        while True:
                            chunk = infile.read(8192)  # 8KB chunks
                            if not chunk:
                                break
                            outfile.write(chunk)

    def _get_bar_format(self, description: str) -> str:
        """
        Generate platform-appropriate progress bar format.
        """
        return (
            f"{Colors.YELLOW}DASH{Colors.CYAN} {description}{Colors.WHITE}: "
            f"{Colors.MAGENTA}{{bar:40}} "
            f"{Colors.LIGHT_GREEN}{{n_fmt}}{Colors.WHITE}/{Colors.CYAN}{{total_fmt}} {Colors.LIGHT_MAGENTA}TS {Colors.WHITE}"
            f"{Colors.DARK_GRAY}[{Colors.YELLOW}{{elapsed}}{Colors.WHITE} < {Colors.CYAN}{{remaining}}{Colors.DARK_GRAY}] "
            f"{Colors.WHITE}{{postfix}}"
        )

    def _get_worker_count(self, stream_type: str) -> int:
        """
        Calculate optimal parallel workers based on stream type and infrastructure.
        """
        base_workers = {
            'video': DEFAULT_VIDEO_WORKERS,
            'audio': DEFAULT_AUDIO_WORKERS
        }.get(stream_type.lower(), 2)
        return base_workers

    def _generate_results(self, stream_type: str) -> dict:
        """
        Package final download results.
        """
        return {
            'type': stream_type,
            'nFailed': getattr(self, 'info_nFailed', 0),
            'stopped': getattr(self, 'download_interrupted', False)
        }

    def _verify_download_completion(self) -> None:
        """
        Validate final download integrity - allow partial downloads.
        """
        total = len(self.selected_representation['segment_urls'])
        completed = getattr(self, 'downloaded_segments', set())

        if self.download_interrupted:
            return
        
        if total == 0:
            return
        
        completion_rate = len(completed) / total
        missing_count = total - len(completed)
        
        # Allow downloads with up to 30 missing segments or 90% completion rate
        if completion_rate >= 0.90 or missing_count <= 30:
            return
        
        else:
            missing = sorted(set(range(total)) - completed)
            console.print(f"[red]Missing segments: {missing[:10]}..." if len(missing) > 10 else f"[red]Missing segments: {missing}")

    def _cleanup_resources(self, temp_dir, progress_bar: tqdm) -> None:
        """
        Ensure resource cleanup and final reporting.
        """
        progress_bar.close()
        
        # Delete temp segment files
        if CLEANUP_TMP and temp_dir and os.path.exists(temp_dir):
            try:
                for idx in range(len(self.selected_representation.get('segment_urls', []))):
                    temp_file = os.path.join(temp_dir, f"seg_{idx:06d}.tmp")
                    if os.path.exists(temp_file):
                        os.remove(temp_file)
                os.rmdir(temp_dir)

            except Exception as e:
                console.print(f"[yellow]Warning: Could not clean temp directory: {e}")

        if getattr(self, 'info_nFailed', 0) > 0:
            self._display_error_summary()
            
        # Clear memory
        self.segment_status = {}

    def _display_error_summary(self) -> None:
        """
        Generate final error report.
        """
        total_segments = len(self.selected_representation.get('segment_urls', []))
        failed_indices = [i for i in range(total_segments) if i not in self.downloaded_segments]

        console.print(f" [cyan]Max retries: [red]{getattr(self, 'info_maxRetry', 0)} [white]| "
            f"[cyan]Total retries: [red]{getattr(self, 'info_nRetry', 0)} [white]| "
            f"[cyan]Failed segments: [red]{getattr(self, 'info_nFailed', 0)} [white]| "
            f"[cyan]Failed indices: [red]{failed_indices}")
    
    def get_progress_data(self) -> Dict:
        """Returns current download progress data for API."""
        if not self.estimator:
            return None
            
        total = self.get_segments_count()
        downloaded = len(self.downloaded_segments)
        percentage = (downloaded / total * 100) if total > 0 else 0
        stats = self.estimator.get_stats(downloaded, total)
        
        return {
            'total_segments': total,
            'downloaded_segments': downloaded,
            'failed_segments': self.info_nFailed,
            'current_speed': stats['download_speed'],
            'estimated_size': stats['estimated_total_size'],
            'percentage': round(percentage, 2),
            'eta_seconds': stats['eta_seconds']
        }