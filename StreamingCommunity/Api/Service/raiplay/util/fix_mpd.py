# 18.12.25

import re


def fix_manifest_url(manifest_url: str) -> str:
    """
    Fixes RaiPlay manifest URLs to include all available quality levels.
    
    Args:
        manifest_url (str): Original manifest URL from RaiPlay
    """
    STANDARD_QUALITIES = "1200,1800,2400,3600,5000"
    pattern = r'(_,[\d,]+)(/playlist\.m3u8)'
    
    # Check if URL contains quality specification
    match = re.search(pattern, manifest_url)
    
    if match:
        fixed_url = re.sub(
            pattern, 
            f'_,{STANDARD_QUALITIES}\\2', 
            manifest_url
        )
        return fixed_url
    
    return manifest_url