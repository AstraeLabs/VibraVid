import os

from VibraVid.core.ui.tracker import context_tracker

from .site_costant import site_constants


def _resolve_folder(base_folder: str, *path_components: str) -> str:
    forced_output = context_tracker.output_path
    if forced_output:
        return forced_output
    return os.path.join(base_folder, *path_components) if path_components else base_folder


def movie_folder(*path_components: str) -> str:
    return _resolve_folder(site_constants.MOVIE_FOLDER, *path_components)


def series_folder(*path_components: str) -> str:
    return _resolve_folder(site_constants.SERIES_FOLDER, *path_components)


def anime_folder(*path_components: str) -> str:
    return _resolve_folder(site_constants.ANIME_FOLDER, *path_components)


def music_folder(*path_components: str) -> str:
    return _resolve_folder(site_constants.MUSIC_FOLDER, *path_components)


def live_folder(*path_components: str) -> str:
    return _resolve_folder(site_constants.LIVE_FOLDER, *path_components)
