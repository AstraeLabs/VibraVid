# 11.02.25

import os
import inspect


# Internal utilities
from StreamingCommunity.Util.config_json import config_manager
from StreamingCommunity.Api.Template.loader import folder_name as lazy_loader_folder


def get_site_name_from_stack():
    for frame_info in inspect.stack():
        file_path = frame_info.filename
        
        if "__init__" in file_path:
            parts = file_path.split(f"{lazy_loader_folder}{os.sep}")
            
            if len(parts) > 1:
                site_name = parts[1].split(os.sep)[0]
                return site_name
    
    return None


class SiteConstant:
    @property
    def SITE_NAME(self):
        return get_site_name_from_stack()
    
    @property
    def ROOT_PATH(self):
        return config_manager.get('OUT_FOLDER', 'root_path')
    
    @property
    def FULL_URL(self):
        return config_manager.get_site(self.SITE_NAME, 'full_url').rstrip('/')
    
    @property
    def SERIES_FOLDER(self):
        base_path = self.ROOT_PATH
        if config_manager.get_bool("OUT_FOLDER", "add_siteName"):
            base_path = os.path.join(base_path, self.SITE_NAME)
        return os.path.join(base_path, config_manager.get('OUT_FOLDER', 'serie_folder_name'))
    
    @property
    def MOVIE_FOLDER(self):
        base_path = self.ROOT_PATH
        if config_manager.get_bool("OUT_FOLDER", "add_siteName"):
            base_path = os.path.join(base_path, self.SITE_NAME)
        return os.path.join(base_path, config_manager.get('OUT_FOLDER', 'movie_folder_name'))
    
    @property
    def ANIME_FOLDER(self):
        base_path = self.ROOT_PATH
        if config_manager.get_bool("OUT_FOLDER", "add_siteName"):
            base_path = os.path.join(base_path, self.SITE_NAME)
        return os.path.join(base_path, config_manager.get('OUT_FOLDER', 'anime_folder_name'))

site_constants = SiteConstant()