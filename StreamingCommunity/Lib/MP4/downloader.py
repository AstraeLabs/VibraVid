# 09.06.24

import os
import sys
import time
import signal
import logging
from functools import partial
import threading


# External libraries
from tqdm import tqdm
from rich.console import Console
from rich.prompt import Prompt


# Internal utilities
from StreamingCommunity.Util.http_client import create_client, get_userAgent
from StreamingCommunity.Util import config_manager, os_manager, internet_manager, Colors


# Logic class
from ..FFmpeg.util import print_duration_table


# Config
REQUEST_VERIFY = config_manager.get_bool('REQUESTS', 'verify')
REQUEST_TIMEOUT = config_manager.get_float('REQUESTS', 'timeout')


# Variable
msg = Prompt()
console = Console()
extension_output = config_manager.get("M3U8_CONVERSION", "extension")


class InterruptHandler:
    def __init__(self):
        self.interrupt_count = 0
        self.last_interrupt_time = 0
        self.kill_download = False
        self.force_quit = False


def signal_handler(signum, frame, interrupt_handler, original_handler):
    """Enhanced signal handler for multiple interrupt scenarios"""
    current_time = time.time()
    
    # Reset counter if more than 2 seconds have passed since last interrupt
    if current_time - interrupt_handler.last_interrupt_time > 2:
        interrupt_handler.interrupt_count = 0
    
    interrupt_handler.interrupt_count += 1
    interrupt_handler.last_interrupt_time = current_time

    if interrupt_handler.interrupt_count == 1:
        interrupt_handler.kill_download = True
        console.print("\n[yellow]First interrupt received. Download will complete and save. Press Ctrl+C three times quickly to force quit.")
    
    elif interrupt_handler.interrupt_count >= 3:
        interrupt_handler.force_quit = True
        console.print("\n[red]Force quit activated. Saving partial download...")
        signal.signal(signum, original_handler)


def MP4_Downloader(url: str, path: str, referer: str = None, headers_: dict = None, show_final_info: bool = True):
    """
    Downloads an MP4 video with enhanced interrupt handling.
    - Single Ctrl+C: Completes download gracefully
    - Triple Ctrl+C: Saves partial download and exits
    """
    url = str(url).strip()
    path = os_manager.get_sanitize_path(path)
    if os.path.exists(path):
        console.log("[red]Output file already exists.")
        return None, False

    if not (url.lower().startswith('http://') or url.lower().startswith('https://')):
        logging.error(f"Invalid URL: {url}")
        console.print(f"[red]Invalid URL: {url}")
        return None, False

    # Set headers
    headers = {}
    if referer:
        headers['Referer'] = referer
    
    if headers_:
        headers.update(headers_)
    else:
        headers['User-Agent'] = get_userAgent()

    # Set interrupt handler (only in main thread)
    temp_path = f"{path}.temp"
    interrupt_handler = InterruptHandler()
    original_handler = None

    try:
        if threading.current_thread() is threading.main_thread():
            original_handler = signal.signal(
                signal.SIGINT,
                partial(
                    signal_handler,
                    interrupt_handler=interrupt_handler,
                    original_handler=signal.getsignal(signal.SIGINT),
                ),
            )

    except Exception:
        original_handler = None

    # Ensure the output directory exists
    os.makedirs(os.path.dirname(path), exist_ok=True)

    try:
        with create_client() as client:
            with client.stream("GET", url, headers=headers) as response:
                response.raise_for_status()
                total = int(response.headers.get('content-length', 0))
                
                if total == 0:
                    console.print("[red]No video stream found.")
                    return None, False

                # Create progress bar with percentage instead of n_fmt/total_fmt
                progress_bar = tqdm(
                    total=total,
                    ascii='░▒█',
                    bar_format=f"{Colors.YELLOW}MP4{Colors.CYAN} Downloading{Colors.WHITE}: "
                               f"{Colors.MAGENTA}{{bar:40}} "
                               f"{Colors.LIGHT_GREEN}{{n_fmt}}{Colors.WHITE}/{Colors.CYAN}{{total_fmt}}"
                               f" {Colors.DARK_GRAY}[{Colors.YELLOW}{{elapsed}}{Colors.WHITE} < {Colors.CYAN}{{remaining}}{Colors.DARK_GRAY}]"
                               f"{Colors.WHITE}{{postfix}} ",
                    unit='B',
                    unit_scale=True,
                    mininterval=0.1,
                    file=sys.stdout
                )
                
                start_time = time.time()
                downloaded = 0
                with open(temp_path, 'wb') as file, progress_bar as bar:
                    try:
                        for chunk in response.iter_bytes(chunk_size=65536):
                            if interrupt_handler.force_quit:
                                console.print("\n[red]Force quitting... Saving partial download.")
                                break
                            
                            if chunk:
                                size = file.write(chunk)
                                downloaded += size
                                bar.update(size)
                                
                                # Update postfix with speed and final size
                                elapsed = time.time() - start_time
                                if elapsed > 0:
                                    speed = downloaded / elapsed
                                    speed_str = internet_manager.format_transfer_speed(speed)
                                    postfix_str = f"{Colors.LIGHT_MAGENTA}@ {Colors.LIGHT_CYAN}{speed_str}"
                                    bar.set_postfix_str(postfix_str)

                    except KeyboardInterrupt:
                        if not interrupt_handler.force_quit:
                            interrupt_handler.kill_download = True
                    
        if os.path.exists(temp_path):
            os.rename(temp_path, path)

        if os.path.exists(path):
            if show_final_info:
                file_size = internet_manager.format_file_size(os.path.getsize(path))
                duration = print_duration_table(path, description=False, return_string=True)
                console.print(f"[yellow]Output[white]: [red]{os.path.abspath(path)} \n"
                f"  [cyan]with size[white]: [red]{file_size} \n"
                f"      [cyan]and duration[white]: [red]{duration}")

            return path, interrupt_handler.kill_download
        
        else:
            console.print("[red]Download failed or file is empty.")
            return None, interrupt_handler.kill_download

    except Exception as e:
        console.print(f"[red]Unexpected Error: {e}")
        if os.path.exists(temp_path):
            os.remove(temp_path)
        return None, interrupt_handler.kill_download
    
    finally:
        if original_handler is not None:
            try:
                signal.signal(signal.SIGINT, original_handler)
            except Exception:
                pass