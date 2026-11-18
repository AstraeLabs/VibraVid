# 11.02.25

import logging
import os
import sys

from VibraVid.utils import config_manager

from .site_loader import current_site_var
from .site_loader import folder_name as lazy_loader_folder

logger = logging.getLogger(__name__)


_last_logged_site: str | None = None
_init_dirs: set[str] = set()


def _has_init(dir_name: str) -> bool:
    if dir_name in _init_dirs:
        return True
    if os.path.exists(os.path.join(dir_name, "__init__.py")):
        _init_dirs.add(dir_name)
        return True
    return False


def get_site_name_from_stack():
    """
    Resolve the current service name.

    Returns:
        str: Site name, or None if not found
    """
    global _last_logged_site

    ctx_name = current_site_var.get()
    if ctx_name:
        if ctx_name != _last_logged_site:
            _last_logged_site = ctx_name
            logger.debug(f"Extracted site_name from context variable: {ctx_name}")
        return ctx_name

    frame = sys._getframe(1)
    while frame is not None:
        file_path = frame.f_code.co_filename
        frame = frame.f_back

        if f"{lazy_loader_folder}{os.sep}" in file_path:
            parts = file_path.split(f"{lazy_loader_folder}{os.sep}")
            if len(parts) > 1:
                site_name = parts[1].split(os.sep)[0]
                if site_name not in ("_base", "site_loader", "__pycache__"):
                    return site_name

        # Try to extract from any path with __init__.py or module files
        dir_name = os.path.dirname(file_path)
        potential_site = os.path.basename(dir_name)

        # Check if this directory looks like a service module
        if (
            potential_site
            and potential_site not in ("_base", "__pycache__", "VibraVid")
            and not potential_site.startswith(".")
            and _has_init(dir_name)
        ):
            return potential_site

    logger.error("Could not extract site_name from call stack - returning None")
    return None


class SiteConstant:
    @property
    def SITE_NAME(self) -> str:
        return get_site_name_from_stack()

    @property
    def ROOT_PATH(self) -> str:
        return config_manager.config.get("OUTPUT", "root_path")

    @property
    def FULL_URL(self) -> str:
        return config_manager.domain.get(self.SITE_NAME, "full_url").rstrip("/")

    @property
    def SERIES_FOLDER(self):
        base_path = self.ROOT_PATH
        serie_folder = config_manager.config.get("OUTPUT", "serie_folder_name")
        if "%{site_name}" in serie_folder:
            serie_folder = serie_folder.replace("%{site_name}", self.SITE_NAME)

        return os.path.join(base_path, serie_folder)

    @property
    def MOVIE_FOLDER(self):
        base_path = self.ROOT_PATH
        movie_folder = config_manager.config.get("OUTPUT", "movie_folder_name")
        if "%{site_name}" in movie_folder:
            movie_folder = movie_folder.replace("%{site_name}", self.SITE_NAME)

        return os.path.join(base_path, movie_folder)

    @property
    def ANIME_FOLDER(self):
        base_path = self.ROOT_PATH
        anime_folder = config_manager.config.get("OUTPUT", "anime_folder_name")
        if "%{site_name}" in anime_folder:
            anime_folder = anime_folder.replace("%{site_name}", self.SITE_NAME)

        return os.path.join(base_path, anime_folder)

    @property
    def MUSIC_FOLDER(self):
        base_path = self.ROOT_PATH
        music_folder = config_manager.config.get("OUTPUT", "music_folder_name")
        if "%{site_name}" in music_folder:
            music_folder = music_folder.replace("%{site_name}", self.SITE_NAME)

        return os.path.join(base_path, music_folder)

    @property
    def LIVE_FOLDER(self):
        base_path = self.ROOT_PATH
        live_folder = config_manager.config.get("OUTPUT", "live_folder_name", default="Live")
        if "%{site_name}" in live_folder:
            live_folder = live_folder.replace("%{site_name}", self.SITE_NAME)

        return os.path.join(base_path, live_folder)


site_constants = SiteConstant()
