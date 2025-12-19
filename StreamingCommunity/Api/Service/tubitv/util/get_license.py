# 16.12.25

import uuid
from typing import Tuple, Optional


# Internal utilities
from StreamingCommunity.Util.config_json import config_manager
from StreamingCommunity.Util.http_client import create_client, get_userAgent, get_headers


# Variable
config = config_manager.get_dict("SITE_LOGIN", "tubi")


def generate_device_id():
    """Generate a unique device ID"""
    return str(uuid.uuid4())


def get_bearer_token():
    """
    Get the Bearer token required for Tubi TV authentication.

    Returns:
        str: Bearer token
    """
    if not config.get('email') or not config.get('password'):
        raise Exception("Email or Password not set in configuration.")

    json_data = {
        'type': 'email',
        'platform': 'web',
        'device_id': generate_device_id(),
        'credentials': {
            'email': str(config.get('email')).strip(),
            'password': str(config.get('password')).strip()
        },
    }

    response = create_client(headers=get_headers()).post(
        'https://account.production-public.tubi.io/user/login',
        json=json_data
    )
    
    if response.status_code == 503:
        raise Exception("Service Unavailable: Set VPN to America.")

    return response.json()['access_token']


def get_playback_url(content_id: str, bearer_token: str) -> Tuple[str, Optional[str]]:
    """
    Get the playback URL (HLS) and license URL for a given content ID.

    Parameters:
        - content_id (str): ID of the video content
        - bearer_token (str): Bearer token for authentication

    Returns:
        - Tuple[str, Optional[str]]: (master_playlist_url, license_url)
    """
    headers = {
        'authorization': f"Bearer {bearer_token}",
        'user-agent': get_userAgent(),
    }

    params = {
        'content_id': content_id,
        'limit_resolutions[]': [
            'h264_1080p',
            'h265_1080p',
        ],
        'video_resources[]': [
            'hlsv6_widevine_nonclearlead',
            'hlsv6_playready_psshv0',
            'hlsv6',
        ]
    }

    response = create_client(headers=headers).get(
        'https://content-cdn.production-public.tubi.io/api/v2/content',
        params=params
    )
    
    json_data = response.json()
    
    # Get master playlist URL
    master_playlist_url = json_data['video_resources'][0]['manifest']['url']
    
    # Get license URL if available
    license_url = None
    if 'license_server' in json_data['video_resources'][0]:
        license_url = json_data['video_resources'][0]['license_server']['url']
    
    return master_playlist_url, license_url