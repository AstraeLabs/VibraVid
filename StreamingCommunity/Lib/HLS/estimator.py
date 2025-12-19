# 21.04.25

import time
import logging
import threading
from collections import deque
from typing import Dict


# External libraries
import psutil
from tqdm import tqdm


# Internal utilities
from StreamingCommunity.Util import internet_manager, Colors


class M3U8_Ts_Estimator:
    def __init__(self, total_segments: int, segments_instance=None):
        """
        Initialize the M3U8_Ts_Estimator object.
        
        Parameters:
            - total_segments (int): Length of total segments to download.
        """
        self.ts_file_sizes = []
        self.total_segments = total_segments
        self.segments_instance = segments_instance
        self.lock = threading.Lock()
        self.speed = {"upload": "N/A", "download": "N/A"}
        self._running = True
        self.downloaded_segments_count = 0
        self.speed_thread = threading.Thread(target=self.capture_speed)
        self.speed_thread.daemon = True
        self.speed_thread.start()

    def __del__(self):
        """Ensure thread is properly stopped when the object is destroyed."""
        self._running = False
        
    def add_ts_file(self, size: int):
        """Add a file size to the list of file sizes."""
        if size <= 0:
            return

        with self.lock:
            self.ts_file_sizes.append(size)

    def capture_speed(self, interval: float = 3.0):
        """Capture the internet speed periodically."""
        last_upload, last_download = 0, 0
        speed_buffer = deque(maxlen=3)
        error_count = 0
        max_errors = 5
        current_interval = 0.1
        
        while self._running:
            try:
                io_counters = psutil.net_io_counters()
                if not io_counters:
                    raise ValueError("No IO counters available")
                
                current_upload, current_download = io_counters.bytes_sent, io_counters.bytes_recv
                
                if last_upload and last_download:
                    upload_speed = (current_upload - last_upload) / current_interval
                    download_speed = (current_download - last_download) / current_interval
                    
                    if download_speed > 1024:
                        speed_buffer.append(download_speed)

                        # Increase interval if we have a stable speed measurement
                        if len(speed_buffer) >= 2:
                            current_interval = min(interval, current_interval * 1.5)
                    
                        if speed_buffer:
                            avg_speed = sum(speed_buffer) / len(speed_buffer)
                            
                            try:
                                formatted_upload = internet_manager.format_transfer_speed(max(0, upload_speed))
                                formatted_download = internet_manager.format_transfer_speed(avg_speed)
                                
                                with self.lock:
                                    self.speed = {
                                        "upload": formatted_upload,
                                        "download": formatted_download
                                    }

                            except ImportError:
                                with self.lock:
                                    self.speed = {"upload": "N/A", "download": "N/A"}
                
                last_upload, last_download = current_upload, current_download
                error_count = 0
                
            except Exception as e:
                error_count += 1
                if error_count <= max_errors and self._running:
                    logging.debug(f"Speed capture error: {str(e)}")
                
                if error_count > max_errors:
                    with self.lock:
                        self.speed = {"upload": "N/A", "download": "N/A"}
                    current_interval = 10.0
        
            time.sleep(current_interval)

    def calculate_total_size(self) -> str:
        """
        Calculate the estimated total size of all segments.

        Returns:
            str: The estimated total size in a human-readable format.
        """
        try:
            with self.lock:
                if not self.ts_file_sizes:
                    return "0 B"
                    
                mean_segment_size = sum(self.ts_file_sizes) / len(self.ts_file_sizes)
                estimated_total_size = mean_segment_size * self.total_segments
                return internet_manager.format_file_size(estimated_total_size)

        except Exception as e:
            logging.error("An unexpected error occurred: %s", e)
            return "Error"
    
    def update_progress_bar(self, segment_size: int, progress_counter: tqdm) -> None:
        """
        Update progress bar with segment information.
        
        Parameters:
            - segment_size (int): Size in bytes of the current downloaded segment
            - progress_counter (tqdm): Progress bar instance to update
        """
        try:
            self.add_ts_file(segment_size)
            file_total_size = self.calculate_total_size()
            
            if file_total_size == "Error":
                return
                
            number_file_total_size, units_file_total_size = file_total_size.split(' ', 1)
        
            with self.lock:
                download_speed = self.speed['download']
            
            if download_speed != "N/A" and ' ' in download_speed:
                average_internet_speed, average_internet_unit = download_speed.split(' ', 1)
            else:
                average_internet_speed, average_internet_unit = "N/A", ""
            
            progress_str = (
                f"{Colors.LIGHT_GREEN}{number_file_total_size} {Colors.LIGHT_MAGENTA}{units_file_total_size} {Colors.WHITE}"
                f"{Colors.DARK_GRAY}@ {Colors.LIGHT_CYAN}{average_internet_speed} {Colors.LIGHT_MAGENTA}{average_internet_unit}"
            )
            
            progress_counter.set_postfix_str(progress_str)
            
        except Exception as e:
            logging.error(f"Error updating progress bar: {str(e)}")
            
    def stop(self):
        """Stop speed monitoring thread."""
        self._running = False
        if self.speed_thread.is_alive():
            self.speed_thread.join(timeout=5.0)
    
    def get_average_segment_size(self) -> int:
        """Returns average segment size in bytes."""
        with self.lock:
            if not self.ts_file_sizes:
                return 0
            return int(sum(self.ts_file_sizes) / len(self.ts_file_sizes))
    
    def get_stats(self, downloaded_count: int = None, total_segments: int = None) -> Dict:
        """Returns comprehensive statistics for API."""
        with self.lock:
            avg_size = self.get_average_segment_size()
            total_downloaded = sum(self.ts_file_sizes)
            
            # Calculate ETA
            eta_seconds = 0
            if downloaded_count is not None and total_segments is not None:
                speed = self.speed.get('download', 'N/A')
                if speed != 'N/A' and ' ' in speed:
                    try:
                        speed_value, speed_unit = speed.split(' ', 1)
                        speed_bps = float(speed_value) * (1024 * 1024 if 'MB/s' in speed_unit else 1024 if 'KB/s' in speed_unit else 1)
                        
                        remaining_segments = total_segments - downloaded_count
                        if remaining_segments > 0 and avg_size > 0 and speed_bps > 0:
                            eta_seconds = int((avg_size * remaining_segments) / speed_bps)
                    
                    except Exception:
                        pass
            
            return {
                'total_segments': self.total_segments,
                'downloaded_count': len(self.ts_file_sizes),
                'average_segment_size': avg_size,
                'total_downloaded_bytes': total_downloaded,
                'estimated_total_size': self.calculate_total_size(),
                'upload_speed': self.speed.get('upload', 'N/A'),
                'download_speed': self.speed.get('download', 'N/A'),
                'eta_seconds': eta_seconds
            }