# 16.03.25


# Internal utilities
from StreamingCommunity.Util.http_client import create_client
from StreamingCommunity.Util.headers import get_headers


def generate_license_url(mpd_id: str):
    """
    Generates the URL to obtain the Widevine license.

    Args:
        mpd_id (str): The ID of the MPD (Media Presentation Description) file.

    Returns:
        str: The full license URL.
    """
    params = {
        'cont': mpd_id,
        'output': '62',
    }
    
    response = create_client(headers=get_headers()).get('https://mediapolisvod.rai.it/relinker/relinkerServlet.htm', params=params)
    response.raise_for_status() 

    # Extract the license URL from the response in two lines
    json_data = response.json()
    license_url = json_data.get('licence_server_map').get('drmLicenseUrlValues')[0].get('licenceUrl')

    return license_url