# 16.03.25

import json
import logging

from VibraVid.utils.http_client import create_client, get_headers

logger = logging.getLogger(__name__)


def generate_license_url(content_id: str):
    """
    Resolve the Widevine license URL for a RaiPlay content id via the relinker.

    Args:
        content_id (str): The relinker content id (the ``cont`` value).

    Returns:
        str | None: The license URL, or None when the id is missing/invalid or the content is clear
    """
    if not content_id:
        return None

    params = {
        "cont": content_id,
        "output": "62",
    }

    with create_client(headers=get_headers()) as client:
        response = client.get("https://mediapolisvod.rai.it/relinker/relinkerServlet.htm", params=params)
    response.raise_for_status()

    try:
        json_data = json.loads(response.content.decode("latin-1"))
    except json.JSONDecodeError:
        logger.warning(f"RaiPlay relinker returned non-JSON for cont={content_id}: {response.content[:80]!r}")
        return None

    drm_values = (json_data.get("licence_server_map") or {}).get("drmLicenseUrlValues") or []
    if not drm_values:
        return None  # clear / non-DRM content

    return drm_values[0].get("licenceUrl")
