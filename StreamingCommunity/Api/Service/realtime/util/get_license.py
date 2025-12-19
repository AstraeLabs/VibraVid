# 26.11.2025

# External library
from rich.console import Console


# Internal utilities
from StreamingCommunity.Util.http_client import create_client, get_userAgent, get_headers


# Variable
console = Console()


def get_playback_url(video_id: str, bearer_token: str, get_dash: bool, channel: str = "") -> str:
    """
    Get the playback URL (HLS or DASH) for a given video ID.

    Parameters:
        - video_id (str): ID of the video.
    """
    headers = {
        'authorization': f"Bearer {bearer_token[channel]['key']}",
        'user-agent': get_userAgent()
    }

    json_data = {
        'deviceInfo': {
            "adBlocker": False,
            "drmSupported": True
        },
        'videoId': video_id,
    }
    response = create_client().post(bearer_token[channel]['endpoint'], headers=headers, json=json_data)
    response.raise_for_status()

    if response.status_code == 403:
        console.print("[red]Set vpn to IT to download this content.")

    if not get_dash:
        return response.json()['data']['attributes']['streaming'][0]['url']
    else:
        return response.json()['data']['attributes']['streaming'][1]['url']


def get_bearer_token():
    """
    Get the Bearer token required for authentication.

    Returns:
        str: Token Bearer
    """
    response = create_client(headers=get_headers()).get('https://public.aurora.enhanced.live/site/page/homepage/?include=default&filter[environment]=realtime&v=2')
    return {
        'X-REALM-IT': {
            'endpoint': 'https://public.aurora.enhanced.live/playback/v3/videoPlaybackInfo',
            'key': response.json()['userMeta']['realm']['X-REALM-IT']
        }, 
        'X-REALM-DPLAY': {
            'endpoint': 'https://eu1-prod.disco-api.com/playback/v3/videoPlaybackInfo',
            'key': response.json()['userMeta']['realm']['X-REALM-DPLAY']
        }
    }
