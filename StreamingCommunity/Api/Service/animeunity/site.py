# 10.12.23

import urllib.parse


# External libraries
from rich.console import Console


# Internal utilities
from StreamingCommunity.Util.http_client import create_client_curl, get_userAgent
from StreamingCommunity.Util.table import TVShowManager
from StreamingCommunity.Api.Template import site_constants, MediaManager


# Variable
console = Console()
media_search_manager = MediaManager()
table_show_manager = TVShowManager()


def get_token(user_agent: str) -> dict:
    """
    Retrieve session cookies from the site.
    """
    response = create_client_curl(headers={'user-agent': user_agent}).get(site_constants.FULL_URL)
    response.raise_for_status()
    all_cookies = {name: value for name, value in response.cookies.items()}

    return {k: urllib.parse.unquote(v) for k, v in all_cookies.items()}


def get_real_title(record: dict) -> str:
    """
    Return the most appropriate title from the record.
    """
    if record.get('title_eng'):
        return record['title_eng']
    elif record.get('title'):
        return record['title']
    else:
        return record.get('title_it', '')


def title_search(query: str) -> int:
    """
    Perform anime search on animeunity.so.
    """
    media_search_manager.clear()
    table_show_manager.clear()
    seen_titles = set()
    user_agent = get_userAgent()
    data = get_token(user_agent)

    cookies = {
        'XSRF-TOKEN': data.get('XSRF-TOKEN', ''),
        'animeunity_session': data.get('animeunity_session', ''),
    }

    headers = {
        'origin': site_constants.FULL_URL,
        'referer': f"{site_constants.FULL_URL}/",
        'user-agent': user_agent,
        'x-xsrf-token': data.get('XSRF-TOKEN', ''),
    }

    # First call: /livesearch
    try:
        response1 = create_client_curl(headers=headers).post(f'{site_constants.FULL_URL}/livesearch', cookies=cookies, data={'title': query})
        response1.raise_for_status()
        process_results(response1.json().get('records', []), seen_titles, media_search_manager)

    except Exception as e:
        console.print(f"[red]Site: {site_constants.SITE_NAME}, request search error: {e}")
        return 0

    # Second call: /archivio/get-animes
    try:
        json_data = {
            'title': query,
            'type': False,
            'year': False,
            'order': False,
            'status': False,
            'genres': False,
            'offset': 0,
            'dubbed': False,
            'season': False,
        }
        response2 = create_client_curl(headers=headers).post(f'{site_constants.FULL_URL}/archivio/get-animes', cookies=cookies, json=json_data)
        response2.raise_for_status()
        process_results(response2.json().get('records', []), seen_titles, media_search_manager)

    except Exception as e:
        console.print(f"Site: {site_constants.SITE_NAME}, archivio search error: {e}")

    result_count = media_search_manager.get_length()
    return result_count


def process_results(records: list, seen_titles: set, media_manager: MediaManager) -> None:
    """
    Add unique results to the media manager.
    """
    for dict_title in records:
        try:
            title_id = dict_title.get('id')
            if title_id in seen_titles:
                continue

            seen_titles.add(title_id)
            dict_title['name'] = get_real_title(dict_title)

            media_manager.add_media({
                'id': title_id,
                'slug': dict_title.get('slug'),
                'name': dict_title.get('name'),
                'type': dict_title.get('type'),
                'status': dict_title.get('status'),
                'episodes_count': dict_title.get('episodes_count'),
                'image': dict_title.get('imageurl')
            })
            
        except Exception as e:
            print(f"Error parsing a title entry: {e}")