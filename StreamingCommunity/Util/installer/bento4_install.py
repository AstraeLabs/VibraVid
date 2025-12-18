# 18.07.25

import os
import shutil
import zipfile
import logging
from typing import Optional


# External library
import requests
from rich.console import Console


# Internal utilities
from .binary_paths import binary_paths


# Variable
console = Console()

BENTO4_CONFIGURATION = {
    'windows': {
        'download_url': 'https://www.bok.net/Bento4/binaries/Bento4-SDK-{version}.{platform}.zip',
        'versions': {
            'x64': 'x86_64-microsoft-win32',
            'x86': 'x86-microsoft-win32-vs2010',
        },
        'executables': ['mp4decrypt.exe']
    },
    'darwin': {
        'download_url': 'https://www.bok.net/Bento4/binaries/Bento4-SDK-{version}.{platform}.zip',
        'versions': {
            'x64': 'universal-apple-macosx',
            'arm64': 'universal-apple-macosx'
        },
        'executables': ['mp4decrypt']
    },
    'linux': {
        'download_url': 'https://www.bok.net/Bento4/binaries/Bento4-SDK-{version}.{platform}.zip',
        'versions': {
            'x64': 'x86_64-unknown-linux',
            'x86': 'x86-unknown-linux',
            'arm64': 'x86_64-unknown-linux'
        },
        'executables': ['mp4decrypt']
    }
}


class Bento4Downloader:
    def __init__(self):
        self.os_name = binary_paths.system
        self.arch = binary_paths.arch
        self.home_dir = binary_paths.home_dir
        self.base_dir = binary_paths.ensure_binary_directory()
        self.version = "1-6-0-641"  # Latest stable version as of Nov 2023

    def _download_file(self, url: str, destination: str) -> bool:
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

    def _extract_executables(self, zip_path: str) -> list:
        try:
            extracted_files = []
            config = BENTO4_CONFIGURATION[self.os_name]
            executables = config['executables']

            with zipfile.ZipFile(zip_path, 'r') as zip_ref:
                for zip_info in zip_ref.filelist:
                    for executable in executables:
                        if zip_info.filename.endswith(executable):

                            # Extract to base directory
                            zip_ref.extract(zip_info, self.base_dir)
                            src_path = os.path.join(self.base_dir, zip_info.filename)
                            dst_path = os.path.join(self.base_dir, executable)
                            
                            # Move to final location
                            shutil.move(src_path, dst_path)
                            os.chmod(dst_path, 0o755)
                            extracted_files.append(dst_path)
                            
                            # Clean up intermediate directories
                            parts = zip_info.filename.split('/')
                            if len(parts) > 1:
                                shutil.rmtree(os.path.join(self.base_dir, parts[0]))

            return extracted_files

        except Exception as e:
            logging.error(f"Extraction error: {e}")
            return []

    def download(self) -> list:
        try:
            config = BENTO4_CONFIGURATION[self.os_name]
            platform_str = config['versions'].get(self.arch)
            
            if not platform_str:
                raise ValueError(f"Unsupported architecture: {self.arch}")

            download_url = config['download_url'].format(
                version=self.version,
                platform=platform_str
            )
            
            zip_path = os.path.join(self.base_dir, "bento4.zip")
            console.print(f"[blue]Downloading Bento4 from {download_url}")

            if self._download_file(download_url, zip_path):
                extracted_files = self._extract_executables(zip_path)
                os.remove(zip_path)
                
                if extracted_files:
                    return extracted_files
                    
            raise Exception("Failed to install Bento4")

        except Exception as e:
            logging.error(f"Error downloading Bento4: {e}")
            console.print(f"[red]Error downloading Bento4: {str(e)}")
            return []


def check_mp4decrypt() -> Optional[str]:
    """
    Check for mp4decrypt in the system and download if not found.
    Order: binary directory -> system PATH -> download
    
    Returns:
        Optional[str]: Path to mp4decrypt executable or None if not found/downloaded
    """
    try:
        system_platform = binary_paths.system
        mp4decrypt_name = "mp4decrypt.exe" if system_platform == "windows" else "mp4decrypt"
        
        # STEP 1: Check binary directory FIRST (fastest - single file check)
        binary_dir = binary_paths.get_binary_directory()
        local_path = os.path.join(binary_dir, mp4decrypt_name)
        
        if os.path.isfile(local_path):
            
            # Only check execution permissions on Unix systems
            if system_platform != 'windows' and not os.access(local_path, os.X_OK):
                try:
                    os.chmod(local_path, 0o755)
                except Exception:
                    pass  # Ignore chmod errors
            
            logging.info("mp4decrypt found in binary directory")
            return local_path

        # STEP 2: Check system PATH (slower - searches multiple directories)
        mp4decrypt_path = shutil.which(mp4decrypt_name)
        
        if mp4decrypt_path:
            logging.info("mp4decrypt found in system PATH")
            return mp4decrypt_path

        # STEP 3: Download if not found anywhere
        console.print("[cyan]mp4decrypt not found. Downloading...")
        downloader = Bento4Downloader()
        extracted_files = downloader.download()
        
        return extracted_files[0] if extracted_files else None

    except Exception as e:
        logging.error(f"Error checking or downloading mp4decrypt: {e}")
        return None