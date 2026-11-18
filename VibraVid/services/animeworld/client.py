# 21.03.25

from bs4 import BeautifulSoup

from VibraVid.services._base import site_constants
from VibraVid.utils.http_client import create_client, get_headers


def get_session_and_csrf() -> dict:
    """
    Get the session ID and CSRF token from the website's cookies and HTML meta data.
    """
    # Send an initial GET request to the website
    with create_client(headers=get_headers()) as client:
        response = client.get(site_constants.FULL_URL)
    session_id = response.cookies.get("sessionId")
    soup = BeautifulSoup(response.text, "html.parser")

    csrf_token = None
    meta_tag = soup.find("meta", {"name": "csrf-token"})
    if meta_tag:
        csrf_token = meta_tag.get("content")

    if not csrf_token:
        input_tag = soup.find("input", {"name": "_csrf"})
        if input_tag:
            csrf_token = input_tag.get("value")

    return session_id, csrf_token
