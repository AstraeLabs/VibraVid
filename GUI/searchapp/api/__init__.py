# 06-06-2025 By @FrancescoGrazioso -> "https://github.com/FrancescoGrazioso"


from typing import Dict, Type


# Internal utilities
from .base import BaseStreamingAPI
from .streamingcommunity import StreamingCommunityAPI
from .animeunity import AnimeUnityAPI


_API_REGISTRY: Dict[str, Type[BaseStreamingAPI]] = {
    'streamingcommunity': StreamingCommunityAPI,
    'animeunity': AnimeUnityAPI,
}


def get_api(site_name: str) -> BaseStreamingAPI:
    """
    Get API instance for a specific site.
    
    Args:
        site_name: Name of the streaming site
        
    Returns:
        Instance of the appropriate API class
    """
    site_key = site_name.lower().split('_')[0]
    
    if site_key not in _API_REGISTRY:
        raise ValueError(
            f"Unsupported site: {site_name}. "
            f"Available sites: {', '.join(_API_REGISTRY.keys())}"
        )
    
    api_class = _API_REGISTRY[site_key]
    return api_class()


def get_available_sites() -> list:
    """
    Get list of available streaming sites.
    
    Returns:
        List of site names
    """
    return list(_API_REGISTRY.keys())


def register_api(site_name: str, api_class: Type[BaseStreamingAPI]):
    """
    Register a new API class.
    
    Args:
        site_name: Name of the site
        api_class: API class that inherits from BaseStreamingAPI
    """
    if not issubclass(api_class, BaseStreamingAPI):
        raise ValueError(f"{api_class} must inherit from BaseStreamingAPI")
    
    _API_REGISTRY[site_name.lower()] = api_class


__all__ = [
    'BaseStreamingAPI',
    'StreamingCommunityAPI', 
    'AnimeUnityAPI',
    'get_api',
    'get_available_sites',
    'register_api'
]