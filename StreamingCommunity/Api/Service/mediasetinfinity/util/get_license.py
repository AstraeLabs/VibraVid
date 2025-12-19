# 16.03.25

import re
import uuid
import xml.etree.ElementTree as ET


# External library
from bs4 import BeautifulSoup
from rich.console import Console


# Internal utilities
from StreamingCommunity.Util.http_client import create_client, get_headers, get_userAgent


# Variable
console = Console()
network_data = []
class_mediaset_api = None


class MediasetAPI:
    def __init__(self):
        self.client_id = str(uuid.uuid4())
        self.headers = get_headers()
        self.app_name = self.get_app_name()
        self.beToken = self.generate_betoken()
        self.sha256Hash = self.getHash2c()
        
    def get_app_name(self):
        html = self.fetch_html()
        soup = BeautifulSoup(html, "html.parser")
        meta_tag = soup.find('meta', attrs={'name': 'app-name'})
        
        if meta_tag:
            return meta_tag.get('content')
        
    def getHash256(self):
        return self.sha256Hash
    
    def getBearerToken(self):
        return self.beToken

    def generate_betoken(self):
        json_data = {
            'appName': self.app_name,
            'client_id': self.client_id,
        }
        response = create_client(headers=self.headers).post('https://api-ott-prod-fe.mediaset.net/PROD/play/idm/anonymous/login/v2.0', json=json_data)
        return response.json()['response']['beToken']

    def fetch_html(self, timeout=10):
        response = create_client(headers=self.headers).get("https://mediasetinfinity.mediaset.it/")
        response.raise_for_status()
        return response.text

    def find_relevant_script(self, html):
        soup = BeautifulSoup(html, "html.parser")
        return [s.get_text() for s in soup.find_all("script") if "imageEngines" in s.get_text()]

    def extract_pairs_from_scripts(self, scripts):
        # Chi ha inventato questo metodo di offuscare le chiavi merita di essere fustigato in piazza.
        relevant_part = scripts[0].replace('\\"', '').split('...Option')[1].split('imageEngines')[0]
        pairs = {}
        for match in re.finditer(r'([a-f0-9]{64}):\$(\w+)', relevant_part):
            pairs[match.group(1)] = f"${match.group(2)}"
        return pairs

    def getHash2c(self):
        html = self.fetch_html()
        scripts = self.find_relevant_script(html)[0:1]
        pairs = self.extract_pairs_from_scripts(scripts)
        return list(pairs.keys())[-5]

    def generate_request_headers(self):
        return {
            'authorization': self.beToken,
            'user-agent': self.headers['user-agent'],
            'x-m-device-id': self.client_id,
            'x-m-platform': 'WEB',
            'x-m-property': 'MPLAY',
            'x-m-sid': self.client_id
        }


def get_bearer_token():
    """
    Gets the BEARER_TOKEN for authentication.
    Anche i manifestanti per strada dio bellissimo.
    """
    global class_mediaset_api
    if class_mediaset_api is None:
        class_mediaset_api = MediasetAPI()
    return class_mediaset_api
            


def get_playback_url(CONTENT_ID):
    """
    Gets the playback URL for the specified content.

    Args:
        BEARER_TOKEN (str): The authentication token.
        CONTENT_ID (str): The content identifier.

    Returns:
        dict: The playback JSON object.
    """
    headers = get_headers()
    headers['authorization'] = f'Bearer {class_mediaset_api.getBearerToken()}'
    
    json_data = {
        'contentId': CONTENT_ID,
        'streamType': 'VOD'
    }

    try:
        response = create_client(headers=headers).post('https://api-ott-prod-fe.mediaset.net/PROD/play/playback/check/v2.0', json=json_data)
        response.raise_for_status()
        resp_json = response.json()

        # Check for PL022 error (Infinity+ rights)
        if 'error' in resp_json and resp_json['error'].get('code') == 'PL022':
            raise RuntimeError("Infinity+ required for this content.")
        
        # Check for PL402 error (TVOD not purchased)
        if 'error' in resp_json and resp_json['error'].get('code') == 'PL402':
            raise RuntimeError("Content available for rental: you must rent it first.")

        playback_json = resp_json['response']['mediaSelector']
        return playback_json
    
    except Exception as e:
        raise RuntimeError(f"Failed to get playback URL: {e}, {resp_json if 'resp_json' in locals() else 'No response'}")

def parse_smil_for_media_info(smil_xml):
    """
    Extracts video streams with quality info and subtitle streams from SMIL.

    Args:
        smil_xml (str): The SMIL XML as a string.

    Returns:
        dict: {
            'videos': [{'url': str, 'quality': str, 'clipBegin': str, 'clipEnd': str, 'tracking_data': dict}, ...],
            'subtitles': [{'url': str, 'lang': str, 'type': str}, ...]
        }
    """   
    root = ET.fromstring(smil_xml)
    ns = {'smil': root.tag.split('}')[0].strip('{')}
    
    videos = []
    subtitles_raw = []
    
    # Process all <par> elements
    for par in root.findall('.//smil:par', ns):

        # Extract video information from <ref>
        ref_elem = par.find('.//smil:ref', ns)
        if ref_elem is not None:
            url = ref_elem.attrib.get('src')
            title = ref_elem.attrib.get('title', '')
            
            # Parse tracking data inline
            tracking_data = {}
            for param in ref_elem.findall('.//smil:param', ns):
                if param.attrib.get('name') == 'trackingData':
                    tracking_value = param.attrib.get('value', '')
                    tracking_data = dict(item.split('=', 1) for item in tracking_value.split('|') if '=' in item)
                    break
            
            if url and url.endswith('.mpd'):
                video_info = {
                    'url': url,
                    'title': title,
                    'tracking_data': tracking_data
                }
                videos.append(video_info)
    
        # Extract subtitle information from <textstream>
        for textstream in par.findall('.//smil:textstream', ns):
            sub_url = textstream.attrib.get('src')
            lang = textstream.attrib.get('lang', 'unknown')
            sub_type = textstream.attrib.get('type', 'unknown')
            
            if sub_url:
                subtitle_info = {
                    'url': sub_url,
                    'language': lang,
                    'type': sub_type
                }
                subtitles_raw.append(subtitle_info)
    
    # Filter subtitles: prefer VTT, fallback to SRT
    subtitles_by_lang = {}
    for sub in subtitles_raw:
        lang = sub['language']
        if lang not in subtitles_by_lang:
            subtitles_by_lang[lang] = []
        subtitles_by_lang[lang].append(sub)
    
    subtitles = []
    for lang, subs in subtitles_by_lang.items():
        vtt_subs = [s for s in subs if s['type'] == 'text/vtt']
        if vtt_subs:
            subtitles.append(vtt_subs[0])  # Take first VTT
            
        else:
            srt_subs = [s for s in subs if s['type'] == 'text/srt']
            if srt_subs:
                subtitles.append(srt_subs[0])  # Take first SRT
    
    return {
        'videos': videos,
        'subtitles': subtitles
    }

def get_tracking_info(PLAYBACK_JSON):
    """
    Retrieves media information including videos and subtitles from the playback JSON.

    Args:
        PLAYBACK_JSON (dict): The playback JSON object.

    Returns:
        dict or None: {'videos': [...], 'subtitles': [...]}, or None if request fails.
    """
    params = {
        "format": "SMIL",
        "auth": class_mediaset_api.getBearerToken(),
        "formats": "MPEG-DASH",
        "assetTypes": "HR,browser,widevine,geoIT|geoNo:HR,browser,geoIT|geoNo:SD,browser,widevine,geoIT|geoNo:SD,browser,geoIT|geoNo:SS,browser,widevine,geoIT|geoNo:SS,browser,geoIT|geoNo",
        "balance": "true",
        "auto": "true",
        "tracking": "true",
        "delivery": "Streaming"
    }

    if 'publicUrl' in PLAYBACK_JSON:
        params['publicUrl'] = PLAYBACK_JSON['publicUrl']

    try:
        response = create_client(headers={'user-agent': get_userAgent()}).get(PLAYBACK_JSON['url'], params=params)
        response.raise_for_status()

        results = parse_smil_for_media_info(response.text)
        return results
    
    except Exception as e:
        print(f"Error fetching tracking info: {e}")
        return None


def generate_license_url(tracking_info):
    """
    Generates the URL to obtain the Widevine license.

    Args:
        tracking_info (dict): The tracking info dictionary.

    Returns:
        str: The full license URL.
    """
    params = {
        'releasePid': tracking_info['tracking_data'].get('pid'),
        'account': f"http://access.auth.theplatform.com/data/Account/{tracking_info['tracking_data'].get('aid')}",
        'schema': '1.0',
        'token': class_mediaset_api.getBearerToken(),
    }
    
    return f"{'https://widevine.entitlement.theplatform.eu/wv/web/ModularDrm/getRawWidevineLicense'}", params