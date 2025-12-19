# 06-06-2025 By @FrancescoGrazioso -> "https://github.com/FrancescoGrazioso"


from typing import Dict, List
from .base import BaseStreamingAPI


# Import APi
from .streamingcommunity import StreamingCommunityAPI
from .animeunity import AnimeUnityAPI
from .altadefinizione import AltaDefinizioneAPI
from .raiplay import RaiPlayAPI
from .mediasetinfinity import MediasetInfinityAPI
from .tubitv import TubiTVAPI

_API_REGISTRY: Dict[str, type] = {
    'streamingcommunity': StreamingCommunityAPI,
    'animeunity': AnimeUnityAPI,
    'altadefinizione': AltaDefinizioneAPI,
    'raiplay': RaiPlayAPI,
    'mediasetinfinity': MediasetInfinityAPI,
    'tubitv': TubiTVAPI
}


def get_available_sites() -> List[str]:
    """
    Get list of all available streaming sites.
    
    Returns:
        List of site identifiers
    """
    return list(_API_REGISTRY.keys())


def get_api(site: str) -> BaseStreamingAPI:
    """
    Get API instance for specified site.
    
    Args:
        site: Site identifier (e.g., 'streamingcommunity', 'animeunity', 'altadefinizione')
        
    Returns:
        API instance
        
    Raises:
        ValueError: If site is not supported
    """
    site_lower = site.lower().strip()
    
    if site_lower not in _API_REGISTRY:
        available = ', '.join(_API_REGISTRY.keys())
        raise ValueError(f"Site '{site}' not supported. Available sites: {available}")
    
    api_class = _API_REGISTRY[site_lower]
    return api_class()


def is_site_available(site: str) -> bool:
    """
    Check if a site is available.
    
    Args:
        site: Site identifier
        
    Returns:
        True if site is available
    """
    return site.lower().strip() in _API_REGISTRY


__all__ = [
    'get_available_sites',
    'get_api',
    'is_site_available',
]