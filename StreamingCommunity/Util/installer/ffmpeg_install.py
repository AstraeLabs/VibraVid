# 24.01.2024

import os
import glob
import gzip
import shutil
import logging
import subprocess
from typing import Optional, Tuple


# External library
import requests
from rich.console import Console


# Internal utilities
from .binary_paths import binary_paths


# Variable
console = Console()


FFMPEG_CONFIGURATION = {
    'windows': {
        'download_url': 'https://github.com/eugeneware/ffmpeg-static/releases/latest/download/ffmpeg-win32-{arch}.gz',
        'file_extension': '.gz',
        'executables': ['ffmpeg-win32-{arch}', 'ffprobe-win32-{arch}']
    },
    'darwin': {
        'download_url': 'https://github.com/eugeneware/ffmpeg-static/releases/latest/download/ffmpeg-darwin-{arch}.gz',
        'file_extension': '.gz',
        'executables': ['ffmpeg-darwin-{arch}', 'ffprobe-darwin-{arch}']
    },
    'linux': {
        'download_url': 'https://github.com/eugeneware/ffmpeg-static/releases/latest/download/ffmpeg-linux-{arch}.gz',
        'file_extension': '.gz',
        'executables': ['ffmpeg-linux-{arch}', 'ffprobe-linux-{arch}']
    }
}


class FFMPEGDownloader:
    def __init__(self):
        self.os_name = binary_paths.system
        self.arch = binary_paths.arch
        self.home_dir = binary_paths.home_dir
        self.base_dir = binary_paths.ensure_binary_directory()

    def _check_existing_binaries(self) -> Tuple[Optional[str], Optional[str], Optional[str]]:
        """
        Check if FFmpeg binaries already exist.
        Order: system PATH (where/which) -> binary directory
        
        Returns:
            Tuple[Optional[str], Optional[str], Optional[str]]: Paths to ffmpeg, ffprobe, ffplay
        """
        try:

            # STEP 1: Check system PATH first
            if self.os_name == 'windows':
                try:
                    ffmpeg_path = subprocess.check_output(
                        ['where', 'ffmpeg'], stderr=subprocess.DEVNULL, text=True
                    ).strip().split('\n')[0]
                    
                    ffprobe_path = subprocess.check_output(
                        ['where', 'ffprobe'], stderr=subprocess.DEVNULL, text=True
                    ).strip().split('\n')[0]
                    
                    try:
                        ffplay_path = subprocess.check_output(
                            ['where', 'ffplay'], stderr=subprocess.DEVNULL, text=True
                        ).strip().split('\n')[0]
                    except subprocess.CalledProcessError:
                        ffplay_path = None
                        
                    return ffmpeg_path, ffprobe_path, ffplay_path
                    
                except subprocess.CalledProcessError:
                    pass
            
            else:
                ffmpeg_path = shutil.which('ffmpeg')
                ffprobe_path = shutil.which('ffprobe')
                ffplay_path = shutil.which('ffplay')
                
                if ffmpeg_path and ffprobe_path:
                    return ffmpeg_path, ffprobe_path, ffplay_path

            # STEP 2: Check in binary directory
            console.print("[cyan]Checking for FFmpeg in binary directory...")
            config = FFMPEG_CONFIGURATION[self.os_name]
            executables = [exe.format(arch=self.arch) for exe in config['executables']]
            found_executables = []

            for executable in executables:

                # Check for exact match first
                exe_paths = glob.glob(os.path.join(self.base_dir, executable))
                if exe_paths:
                    found_executables.append(exe_paths[0])
                    
                else:
                    # Check for standard names
                    if self.os_name == 'windows':
                        if 'ffmpeg' in executable:
                            standard_path = os.path.join(self.base_dir, 'ffmpeg.exe')
                        elif 'ffprobe' in executable:
                            standard_path = os.path.join(self.base_dir, 'ffprobe.exe')
                        else:
                            standard_path = None
                    else:
                        if 'ffmpeg' in executable:
                            standard_path = os.path.join(self.base_dir, 'ffmpeg')
                        elif 'ffprobe' in executable:
                            standard_path = os.path.join(self.base_dir, 'ffprobe')
                        else:
                            standard_path = None
                    
                    if standard_path and os.path.exists(standard_path):
                        found_executables.append(standard_path)
                    else:
                        found_executables.append(None)

            # Return found executables if we have at least ffmpeg and ffprobe
            if len(found_executables) >= 2 and found_executables[0] and found_executables[1]:
                ffplay_path = found_executables[2] if len(found_executables) > 2 else None
                return found_executables[0], found_executables[1], ffplay_path
            
            return (None, None, None)
            
        except Exception as e:
            logging.error(f"Error checking existing binaries: {e}")
            return (None, None, None)

    def _download_file(self, url: str, destination: str) -> bool:
        """
        Download a file from URL.

        Parameters:
            url (str): The URL to download the file from. Should be a direct download link.
            destination (str): Local file path where the downloaded file will be saved.

        Returns:
            bool: True if download was successful, False otherwise.
        """
        try:
            response = requests.get(url, stream=True)
            response.raise_for_status()
            
            with open(destination, 'wb') as file:
                for chunk in response.iter_content(chunk_size=8192):
                    file.write(chunk)
            return True
        
        except Exception as e:
            logging.error(f"Download error: {e}")
            return False

    def _extract_file(self, gz_path: str, final_path: str) -> bool:
        """
        Extract a gzipped file and set proper permissions.

        Parameters:
            gz_path (str): Path to the gzipped file
            final_path (str): Path where the extracted file should be saved

        Returns:
            bool: True if extraction was successful, False otherwise
        """
        try:
            logging.info(f"Attempting to extract {gz_path} to {final_path}")
            
            # Check if source file exists and is readable
            if not os.path.exists(gz_path):
                logging.error(f"Source file {gz_path} does not exist")
                return False
                
            if not os.access(gz_path, os.R_OK):
                logging.error(f"Source file {gz_path} is not readable")
                return False

            # Extract the file
            with gzip.open(gz_path, 'rb') as f_in:
                # Test if the gzip file is valid
                try:
                    f_in.read(1)
                    f_in.seek(0)
                except Exception as e:
                    logging.error(f"Invalid gzip file {gz_path}: {e}")
                    return False

                # Extract the file
                with open(final_path, 'wb') as f_out:
                    shutil.copyfileobj(f_in, f_out)

            # Set executable permissions
            os.chmod(final_path, 0o755)
            logging.info(f"Successfully extracted {gz_path} to {final_path}")
            
            # Remove the gzip file
            os.remove(gz_path)
            return True

        except Exception as e:
            logging.error(f"Extraction error for {gz_path}: {e}")
            return False

    def download(self) -> Tuple[Optional[str], Optional[str], Optional[str]]:
        """
        Main method to download and set up FFmpeg executables.

        Returns:
            Tuple[Optional[str], Optional[str], Optional[str]]: Paths to ffmpeg, ffprobe, and ffplay executables.
        """
        if self.os_name == 'linux':
            try:
                # Attempt to install FFmpeg using apt
                console.print("[blue]Trying to install FFmpeg using 'sudo apt install ffmpeg'")
                result = subprocess.run(
                    ['sudo', 'apt', 'install', '-y', 'ffmpeg'],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True
                )
                if result.returncode == 0:
                    ffmpeg_path = shutil.which('ffmpeg')
                    ffprobe_path = shutil.which('ffprobe')

                    if ffmpeg_path and ffprobe_path:
                        return ffmpeg_path, ffprobe_path, None
                else:
                    console.print("[yellow]Failed to install FFmpeg via apt. Proceeding with static download.")
                    
            except Exception as e:
                logging.error(f"Error during 'sudo apt install ffmpeg': {e}")
                console.print("[red]Error during 'sudo apt install ffmpeg'. Proceeding with static download.")

        # Proceed with static download if apt installation fails or is not applicable
        config = FFMPEG_CONFIGURATION[self.os_name]
        executables = [exe.format(arch=self.arch) for exe in config['executables']]
        successful_extractions = []

        for executable in executables:
            try:
                download_url = f"https://github.com/eugeneware/ffmpeg-static/releases/latest/download/{executable}.gz"
                download_path = os.path.join(self.base_dir, f"{executable}.gz")
                final_path = os.path.join(self.base_dir, executable)
                
                # Log the current operation
                logging.info(f"Processing {executable}")
                console.print(f"[blue]Downloading {executable} from GitHub")
                
                # Download the file
                if not self._download_file(download_url, download_path):
                    console.print(f"[red]Failed to download {executable}")
                    continue

                # Extract the file
                if self._extract_file(download_path, final_path):
                    successful_extractions.append(final_path)
                else:
                    console.print(f"[red]Failed to extract {executable}")

            except Exception as e:
                logging.error(f"Error processing {executable}: {e}")
                console.print(f"[red]Error processing {executable}: {str(e)}")
                continue

        # Return the results based on successful extractions
        return (
            successful_extractions[0] if len(successful_extractions) > 0 else None,
            successful_extractions[1] if len(successful_extractions) > 1 else None,
            None  # ffplay is not included in the current implementation
        )

def check_ffmpeg() -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """
    Check for FFmpeg executables in the system and download them if not found.
    Order: system PATH (where/which) -> binary directory -> download
    
    Returns:
        Tuple[Optional[str], Optional[str], Optional[str]]: Paths to ffmpeg, ffprobe, and ffplay
    """
    try:
        # Create downloader instance to use its existing check method
        downloader = FFMPEGDownloader()
        
        # STEP 1 & 2: Check existing binaries (system PATH + binary directory)
        ffmpeg_path, ffprobe_path, ffplay_path = downloader._check_existing_binaries()
        
        # If found, return them
        if ffmpeg_path and ffprobe_path:
            return ffmpeg_path, ffprobe_path, ffplay_path

        # STEP 3: Download if not found
        return downloader.download()

    except Exception as e:
        logging.error(f"Error checking or downloading FFmpeg executables: {e}")
        return None, None, None