# 15.12.2025

import os
import shutil
import tarfile
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


MEGATOOLS_CONFIGURATION = {
    'windows': {
        'download_url': 'https://xff.cz/megatools/builds/builds/megatools-{version}-{platform}.zip',
        'versions': {
            'x64': 'win64',
            'x86': 'win32',
        },
        'executables': ['megatools.exe']
    },
    'darwin': {
        'download_url': 'https://xff.cz/megatools/builds/builds/megatools-{version}-{platform}.tar.gz',
        'versions': {
            'x64': 'linux-x86_64',
            'arm64': 'linux-aarch64'
        },
        'executables': ['megatools']
    },
    'linux': {
        'download_url': 'https://xff.cz/megatools/builds/builds/megatools-{version}-{platform}.tar.gz',
        'versions': {
            'x64': 'linux-x86_64',
            'x86': 'linux-i686',
            'arm64': 'linux-aarch64'
        },
        'executables': ['megatools']
    }
}


class MegatoolsDownloader:
    def __init__(self):
        self.os_name = binary_paths.system
        self.arch = binary_paths.arch
        self.home_dir = binary_paths.home_dir
        self.base_dir = binary_paths.ensure_binary_directory()
        self.version = "1.11.5.20250706"

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

    def _extract_executables(self, archive_path: str) -> list:
        try:
            extracted_files = []
            config = MEGATOOLS_CONFIGURATION[self.os_name]
            executables = config['executables']
            
            # Determine if it's a zip or tar.gz
            is_zip = archive_path.endswith('.zip')

            if is_zip:
                with zipfile.ZipFile(archive_path, 'r') as archive:

                    # Extract all contents to a temporary location
                    temp_extract_dir = os.path.join(self.base_dir, 'temp_megatools')
                    archive.extractall(temp_extract_dir)
                    
                    # Find executables in the extracted folder (search recursively)
                    for executable in executables:
                        found = False
                        for root, dirs, files in os.walk(temp_extract_dir):
                            if executable in files:
                                src_path = os.path.join(root, executable)
                                dst_path = os.path.join(self.base_dir, executable)
                                
                                shutil.copy2(src_path, dst_path)
                                extracted_files.append(dst_path)
                                found = True
                                break
                        
                        if not found:
                            logging.warning(f"Executable {executable} not found in archive")
                    
                    # Clean up temporary extraction directory
                    if os.path.exists(temp_extract_dir):
                        shutil.rmtree(temp_extract_dir)

            else:
                with tarfile.open(archive_path, 'r:gz') as archive:

                    # Extract all contents to a temporary location
                    temp_extract_dir = os.path.join(self.base_dir, 'temp_megatools')
                    archive.extractall(temp_extract_dir)
                    
                    # Find executables in the extracted folder (search recursively)
                    for executable in executables:
                        found = False
                        for root, dirs, files in os.walk(temp_extract_dir):
                            if executable in files:
                                src_path = os.path.join(root, executable)
                                dst_path = os.path.join(self.base_dir, executable)
                                
                                shutil.copy2(src_path, dst_path)
                                os.chmod(dst_path, 0o755)
                                extracted_files.append(dst_path)
                                found = True
                                break
                        
                        if not found:
                            logging.warning(f"Executable {executable} not found in archive")
                    
                    # Clean up temporary extraction directory
                    if os.path.exists(temp_extract_dir):
                        shutil.rmtree(temp_extract_dir)

            return extracted_files

        except Exception as e:
            logging.error(f"Extraction error: {e}")
            return []

    def _verify_executable(self, executable_path: str) -> bool:
        """Verify that the executable works by running --version."""
        try:
            import subprocess
            
            result = subprocess.run(
                [executable_path, '--version'],
                capture_output=True,
                text=True,
                timeout=5
            )
            
            # megatools returns exit code 1 when showing version/help, but still outputs correctly
            if result.returncode in [0, 1] and ('megatools' in result.stdout.lower() or 'megatools' in result.stderr.lower()):
                version_output = result.stdout or result.stderr
                logging.info(f"Megatools executable verified: {version_output.splitlines()[0] if version_output else 'OK'}")
                return True
            
            else:
                logging.error(f"Executable verification failed with code: {result.returncode}")
                return False
                
        except Exception as e:
            logging.error(f"Failed to verify executable: {e}")
            return False

    def download(self) -> list:
        try:
            config = MEGATOOLS_CONFIGURATION[self.os_name]
            platform_str = config['versions'].get(self.arch)
            
            if not platform_str:
                raise ValueError(f"Unsupported architecture: {self.arch}")

            download_url = config['download_url'].format(
                version=self.version,
                platform=platform_str
            )
            
            # Determine file extension
            extension = '.zip' if self.os_name == 'windows' else '.tar.gz'
            archive_path = os.path.join(self.base_dir, f"megatools{extension}")
            
            console.print(f"[blue]Downloading Megatools {self.version}")

            if self._download_file(download_url, archive_path):
                extracted_files = self._extract_executables(archive_path)
                
                # Verify each extracted executable
                if extracted_files:
                    verified_files = []
                    
                    for exe_path in extracted_files:
                        if self._verify_executable(exe_path):
                            verified_files.append(exe_path)
                    
                    if verified_files:
                        console.print("[green]Successfully installed Megatools")
                        os.remove(archive_path)
                        return verified_files
                    else:
                        logging.error("No executables were verified successfully")
                else:
                    logging.error("No executables were extracted")
                
                # Clean up archive
                if os.path.exists(archive_path):
                    os.remove(archive_path)
                    
            raise Exception("Failed to install Megatools")

        except Exception as e:
            logging.error(f"Error downloading Megatools: {e}")
            console.print(f"[red]Error downloading Megatools: {str(e)}")
            return []


def check_megatools() -> Optional[str]:
    """
    Check for megatools in the system and download if not found.
    Order: binary directory -> system PATH -> download
    
    Returns:
        Optional[str]: Path to megatools executable or None if not found/downloaded
    """
    try:
        system_platform = binary_paths.system
        megatools_name = "megatools.exe" if system_platform == "windows" else "megatools"
        
        # STEP 1: Check binary directory FIRST
        binary_dir = binary_paths.get_binary_directory()
        local_path = os.path.join(binary_dir, megatools_name)
        
        if os.path.isfile(local_path):
            
            # Only check execution permissions on Unix systems
            if system_platform != 'windows' and not os.access(local_path, os.X_OK):
                try:
                    os.chmod(local_path, 0o755)
                except Exception:
                    pass
            
            logging.info("megatools found in binary directory")
            return local_path

        # STEP 2: Check system PATH
        megatools_path = shutil.which(megatools_name)
        
        if megatools_path:
            logging.info("megatools found in system PATH")
            return megatools_path

        # STEP 3: Download if not found anywhere
        console.print("[cyan]megatools not found. Downloading...")
        downloader = MegatoolsDownloader()
        extracted_files = downloader.download()
        
        return extracted_files[0] if extracted_files else None

    except Exception as e:
        logging.error(f"Error checking or downloading megatools: {e}")
        return None