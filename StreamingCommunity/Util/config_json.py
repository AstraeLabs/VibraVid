# 29.01.24

import os
import sys
import json
import logging
import requests
from typing import Any, List


# External library
from rich.console import Console


# Variable
console = Console()


class ConfigManager:
    def __init__(self, file_name: str = 'config.json') -> None:
        """
        Initialize the ConfigManager.
        
        Args:
            file_name (str, optional): Configuration file name. Default: 'config.json'.
        """
        # Determine the base path - use the current working directory
        if getattr(sys, 'frozen', False):
            # If the application is frozen (e.g., PyInstaller)
            base_path = os.path.dirname(sys.executable)

        else:
            # Use the current working directory where the script is executed
            base_path = os.getcwd()
            
        # Initialize file paths
        self.file_path = os.path.join(base_path, file_name)
        self.domains_path = os.path.join(base_path, 'domains.json')
        
        # Display the actual file path for debugging
        console.print(f"[cyan]Config path: [green]{self.file_path}")
        
        # Reference repository URL
        self.reference_config_url = 'https://raw.githubusercontent.com/Arrowar/StreamingCommunity/refs/heads/main/config.json'
        
        # Initialize data structures
        self.config = {}
        self.configSite = {}
        self.cache = {}

        # Load the configuration
        self.fetch_domain_online = True
        self.load_config()
        
    def load_config(self) -> None:
        """Load the configuration and initialize all settings."""
        if not os.path.exists(self.file_path):
            console.print(f"[red]WARNING: Configuration file not found: {self.file_path}")
            console.print("[yellow]Downloading from repository...")
            self._download_reference_config()
        
        # Load the configuration file
        try:
            with open(self.file_path, 'r') as f:
                self.config = json.load(f)
            
            # Update settings from the configuration
            self._update_settings_from_config()
                
            # Load site data based on fetch_domain_online setting
            self._load_site_data()
                
        except json.JSONDecodeError as e:
            console.print(f"[red]Error parsing JSON: {str(e)}")
            self._handle_config_error()

        except Exception as e:
            console.print(f"[red]Error loading configuration: {str(e)}")
            self._handle_config_error()
    
    def _handle_config_error(self) -> None:
        """Handle configuration errors by downloading the reference version."""
        console.print("[yellow]Attempting to retrieve reference configuration...")
        self._download_reference_config()
        
        # Reload the configuration
        try:
            with open(self.file_path, 'r') as f:
                self.config = json.load(f)
            self._update_settings_from_config()
            console.print("[green]Reference configuration loaded successfully")
        except Exception as e:
            console.print(f"[red]Critical configuration error: {str(e)}")
            console.print("[red]Unable to proceed. The application will terminate.")
            sys.exit(1)
    
    def _update_settings_from_config(self) -> None:
        """Update internal settings from loaded configurations."""
        default_section = self.config.get('DEFAULT', {})
        
        # Get fetch_domain_online setting (True by default)
        self.fetch_domain_online = default_section.get('fetch_domain_online', True)
        
    def _download_reference_config(self) -> None:
        """Download the reference configuration from GitHub."""
        console.print(f"[cyan]Downloading configuration: [green]{self.reference_config_url}")

        try:
            response = requests.get(self.reference_config_url, timeout=8, headers={'User-Agent': "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
            
            if response.status_code == 200:
                with open(self.file_path, 'wb') as f:
                    f.write(response.content)
                file_size = len(response.content) / 1024
                console.print(f"[green]Download complete: {os.path.basename(self.file_path)} ({file_size:.2f} KB)")
            else:
                error_msg = f"HTTP Error: {response.status_code}, Response: {response.text[:100]}"
                console.print(f"[red]Download failed: {error_msg}")
                raise Exception(error_msg)
            
        except Exception as e:
            console.print(f"[red]Download error: {str(e)}")
            raise
    
    def _load_site_data(self) -> None:
        """Load site data based on fetch_domain_online setting."""
        if self.fetch_domain_online:
            self._load_site_data_online()
        else:
            self._load_site_data_from_file()
    
    def _load_site_data_online(self) -> None:
        """Load site data from GitHub and update local domains.json file."""
        domains_github_url = "https://raw.githubusercontent.com/Arrowar/StreamingCommunity/refs/heads/main/.github/.domain/domains.json"
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
        }
        
        try:
            response = requests.get(domains_github_url, timeout=8, headers=headers)

            if response.ok:
                self.configSite = response.json()
                
                # Determine which file to save to
                self._save_domains_to_appropriate_location()
                
            else:
                console.print(f"[red]GitHub request failed: HTTP {response.status_code}, {response.text[:100]}")
                self._handle_site_data_fallback()
        
        except json.JSONDecodeError as e:
            console.print(f"[red]Error parsing JSON from GitHub: {str(e)}")
            self._handle_site_data_fallback()
            
        except Exception as e:
            console.print(f"[red]GitHub connection error: {str(e)}")
            self._handle_site_data_fallback()
    
    def _save_domains_to_appropriate_location(self) -> None:
        """Save domains to the appropriate location based on existing files."""
        if getattr(sys, 'frozen', False):
            # If the application is frozen (e.g., PyInstaller)
            base_path = os.path.dirname(sys.executable)
        else:
            # Use the current working directory where the script is executed
            base_path = os.getcwd()
            
        # Check for GitHub structure first
        github_domains_path = os.path.join(base_path, '.github', '.domain', 'domains.json')
        console.print(f"[cyan]Domain path: [green]{github_domains_path}")
        
        try:
            if os.path.exists(github_domains_path):
                
                # Update existing GitHub structure file
                with open(github_domains_path, 'w', encoding='utf-8') as f:
                    json.dump(self.configSite, f, indent=4, ensure_ascii=False)
                
            elif not os.path.exists(self.domains_path):
                # Save to root only if it doesn't exist and GitHub structure doesn't exist
                with open(self.domains_path, 'w', encoding='utf-8') as f:
                    json.dump(self.configSite, f, indent=4, ensure_ascii=False)
                console.print(f"[green]Domains saved to: {self.domains_path}")
                
            else:
                # Root file exists, don't overwrite it
                console.print(f"[yellow]Local domains.json already exists, not overwriting: {self.domains_path}")
                console.print("[yellow]Tip: Delete the file if you want to recreate it from GitHub")
                
        except Exception as save_error:
            console.print(f"[yellow]Warning: Could not save domains to file: {str(save_error)}")

            # Try to save to root as fallback only if it doesn't exist
            if not os.path.exists(self.domains_path):
                try:
                    with open(self.domains_path, 'w', encoding='utf-8') as f:
                        json.dump(self.configSite, f, indent=4, ensure_ascii=False)
                    console.print(f"[green]Domains saved to fallback location: {self.domains_path}")
                except Exception as fallback_error:
                    console.print(f"[red]Failed to save to fallback location: {str(fallback_error)}")
    
    def _load_site_data_from_file(self) -> None:
        """Load site data from local domains.json file."""
        try:
            # Determine the base path
            if getattr(sys, 'frozen', False):

                # If the application is frozen (e.g., PyInstaller)
                base_path = os.path.dirname(sys.executable)
            else:

                # Use the current working directory where the script is executed
                base_path = os.getcwd()
            
            # Check for GitHub structure first
            github_domains_path = os.path.join(base_path, '.github', '.domain', 'domains.json')
            
            if os.path.exists(github_domains_path):
                console.print(f"[cyan]Domain path: [green]{github_domains_path}")
                with open(github_domains_path, 'r', encoding='utf-8') as f:
                    self.configSite = json.load(f)
                
                site_count = len(self.configSite) if isinstance(self.configSite, dict) else 0
                
            elif os.path.exists(self.domains_path):
                console.print(f"[cyan]Reading domains from root: {self.domains_path}")
                with open(self.domains_path, 'r', encoding='utf-8') as f:
                    self.configSite = json.load(f)
                
                site_count = len(self.configSite) if isinstance(self.configSite, dict) else 0
                console.print(f"[green]Domains loaded from root file: {site_count} streaming services")

            else:
                error_msg = f"domains.json not found in GitHub structure ({github_domains_path}) or root ({self.domains_path}) and fetch_domain_online is disabled"
                console.print(f"[red]Configuration error: {error_msg}")
                console.print("[yellow]Tip: Set 'fetch_domain_online' to true to download domains from GitHub")
                self.configSite = {}
        
        except Exception as e:
            console.print(f"[red]Local domain file error: {str(e)}")
            self.configSite = {}
    
    def _handle_site_data_fallback(self) -> None:
        """Handle site data fallback in case of error."""
        # Determine the base path
        if getattr(sys, 'frozen', False):
            
            # If the application is frozen (e.g., PyInstaller)
            base_path = os.path.dirname(sys.executable)
        else:
            # Use the current working directory where the script is executed
            base_path = os.getcwd()
        
        # Check for GitHub structure first
        github_domains_path = os.path.join(base_path, '.github', '.domain', 'domains.json')
        
        if os.path.exists(github_domains_path):
            console.print("[yellow]Attempting fallback to GitHub structure domains.json file...")
            try:
                with open(github_domains_path, 'r', encoding='utf-8') as f:
                    self.configSite = json.load(f)
                console.print("[green]Fallback to GitHub structure successful")
                return
            except Exception as fallback_error:
                console.print(f"[red]GitHub structure fallback failed: {str(fallback_error)}")
        
        if os.path.exists(self.domains_path):
            console.print("[yellow]Attempting fallback to root domains.json file...")
            try:
                with open(self.domains_path, 'r', encoding='utf-8') as f:
                    self.configSite = json.load(f)
                console.print("[green]Fallback to root domains successful")
                return
            except Exception as fallback_error:
                console.print(f"[red]Root domains fallback failed: {str(fallback_error)}")
        
        console.print("[red]No local domains.json file available for fallback")
        self.configSite = {}
    
    def get(self, section: str, key: str, data_type: type = str, from_site: bool = False, default: Any = None) -> Any:
        """
        Read a value from the configuration.
        
        Args:
            section (str): Section in the configuration
            key (str): Key to read
            data_type (type, optional): Expected data type. Default: str
            from_site (bool, optional): Whether to read from the site configuration. Default: False
            default (Any, optional): Default value if key is not found. Default: None
            
        Returns:
            Any: The key value converted to the specified data type, or default if not found
        """
        cache_key = f"{'site' if from_site else 'config'}.{section}.{key}"
        logging.info(f"Reading key: {cache_key}")
        
        # Check if the value is in the cache
        if cache_key in self.cache:
            return self.cache[cache_key]
        
        # Choose the appropriate source
        config_source = self.configSite if from_site else self.config
        
        # Check if the section and key exist
        if section not in config_source:
            if default is not None:
                logging.info(f"Section '{section}' not found. Returning default value.")
                return default
            raise ValueError(f"Section '{section}' not found in {'site' if from_site else 'main'} configuration")
        
        if key not in config_source[section]:
            if default is not None:
                logging.info(f"Key '{key}' not found in section '{section}'. Returning default value.")
                return default
            raise ValueError(f"Key '{key}' not found in section '{section}' of {'site' if from_site else 'main'} configuration")
        
        # Get and convert the value
        value = config_source[section][key]
        converted_value = self._convert_to_data_type(value, data_type)
        
        # Save in cache
        self.cache[cache_key] = converted_value
        
        return converted_value

    def _convert_to_data_type(self, value: Any, data_type: type) -> Any:
        """
        Convert the value to the specified data type.
        
        Args:
            value (Any): Value to convert
            data_type (type): Target data type
            
        Returns:
            Any: Converted value
        """
        try:
            if data_type is int:
                return int(value)
            
            elif data_type is float:
                return float(value)
            
            elif data_type is bool:
                if isinstance(value, str):
                    return value.lower() in ("yes", "true", "t", "1")
                return bool(value)
            
            elif data_type is list:
                if isinstance(value, list):
                    return value
                if isinstance(value, str):
                    return [item.strip() for item in value.split(',')]
                return [value]

            elif data_type is dict:
                if isinstance(value, dict):
                    return value
                
                raise ValueError(f"Cannot convert {type(value).__name__} to dict")
            else:
                return value
        except Exception as e:
            logging.error(f"Error converting: {data_type.__name__} to value '{value}' with error: {e}")
            raise ValueError(f"Error converting: {data_type.__name__} to value '{value}' with error: {e}")
    
    # Getters for main configuration
    def get_int(self, section: str, key: str, default: int = None) -> int:
        """Read an integer from the main configuration."""
        return self.get(section, key, int, default=default)

    def get_float(self, section: str, key: str, default: float = None) -> float:
        """Read a float from the main configuration."""
        return self.get(section, key, float, default=default)

    def get_bool(self, section: str, key: str, default: bool = None) -> bool:
        """Read a boolean from the main configuration."""
        return self.get(section, key, bool, default=default)

    def get_list(self, section: str, key: str, default: List[str] = None) -> List[str]:
        """Read a list from the main configuration."""
        return self.get(section, key, list, default=default)

    def get_dict(self, section: str, key: str, default: dict = None) -> dict:
        """Read a dictionary from the main configuration."""
        return self.get(section, key, dict, default=default)

    # Getters for site configuration
    def get_site(self, section: str, key: str) -> Any:
        """Read a value from the site configuration."""
        return self.get(section, key, str, True)
    
    def set_key(self, section: str, key: str, value: Any, to_site: bool = False) -> None:
        """
        Set a key in the configuration.
        
        Args:
            section (str): Section in the configuration
            key (str): Key to set
            value (Any): Value to associate with the key
            to_site (bool, optional): Whether to set in the site configuration. Default: False
        """
        try:
            config_target = self.configSite if to_site else self.config
            
            if section not in config_target:
                config_target[section] = {}
            
            config_target[section][key] = value
            
            # Update the cache
            cache_key = f"{'site' if to_site else 'config'}.{section}.{key}"
            self.cache[cache_key] = value
            
            logging.info(f"Key '{key}' set in section '{section}' of {'site' if to_site else 'main'} configuration")
        
        except Exception as e:
            error_msg = f"Error setting key '{key}' in section '{section}' of {'site' if to_site else 'main'} configuration: {e}"
            logging.error(error_msg)
            console.print(f"[red]{error_msg}")
    
    def save_config(self) -> None:
        """Save the main configuration to file."""
        try:
            with open(self.file_path, 'w') as f:
                json.dump(self.config, f, indent=4)

            logging.info(f"Configuration saved to: {self.file_path}")

        except Exception as e:
            error_msg = f"Error saving configuration: {e}"
            console.print(f"[red]{error_msg}")
            logging.error(error_msg)


# Initialize the ConfigManager when the module is imported
config_manager = ConfigManager()