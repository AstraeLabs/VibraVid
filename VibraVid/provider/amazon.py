# 17.07.26

import base64
import datetime
import hashlib
import json
import logging
import secrets
import uuid
from typing import Optional
from urllib.parse import parse_qs, urlencode, urlparse

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPrivateKey
from rich.console import Console
from rich.prompt import Prompt

from VibraVid.utils import config_manager
from VibraVid.utils.http_client import create_client

console = Console()
logger = logging.getLogger(__name__)

_LOGIN_KEY = "amazon_music"
_HARLEY_VER = "3.12.0.78"
_APP_VER = "9.5.2.2478a"
_HOST = "https://music.amazon.com"

_MUSIC_DEVICE_TYPE = "A1DL2DVDQVK3Q"
_ASSOC_HANDLE = "amzn_tiburon_na"
_APP_VERSION = "22.15.12"

_REGIONS = {
    "US": ("NA", "ATVPDKIKX0DER", "United States", "en_US", "com"),
    "CA": ("NA", "A2EUQ1WTGCTBG2", "Canada", "en_CA", "ca"),
    "MX": ("NA", "A1AM78C64UM0Y8", "Mexico", "es_MX", "com.mx"),
    "BR": ("NA", "A2Q3Y263D00KWC", "Brazil", "es_BR", "com.br"),
    "AR": ("NA", "ATVPDKIKX0DER", "Argentina", "es_AR", "com"),
    "CL": ("NA", "ATVPDKIKX0DER", "Chile", "es_CL", "com"),
    "CO": ("NA", "ATVPDKIKX0DER", "Colombia", "es_CO", "com"),
    "NL": ("EU", "A1805IZSGTT6HS", "Netherlands", "nl_NL", "com"),
    "IN": ("EU", "A21TJRUUN4KGV", "India", "hi_IN", "in"),
    "GB": ("EU", "A1F83G8C2ARO7P", "United Kingdom", "en_GB", "co.uk"),
    "ES": ("EU", "A1RKKUPIHCS9HS", "Spain", "es_ES", "es"),
    "FR": ("EU", "A13V1IB3VIYZZH", "France", "fr_FR", "fr"),
    "IT": ("EU", "APJ6JRA9NG5V4", "Italy", "it_IT", "it"),
    "DE": ("EU", "A1PA6795UKMFR9", "Germany", "de_DE", "de"),
    "AT": ("EU", "A1PA6795UKMFR9", "Austria", "de_AT", "de"),
    "BE": ("EU", "ATVPDKIKX0DER", "Belgium", "fr_BE", "com"),
    "IE": ("EU", "ATVPDKIKX0DER", "Ireland", "ga_IE", "com"),
    "PL": ("EU", "A1C3SOZRARQ6R3", "Poland", "pl_PL", "com"),
    "PT": ("EU", "ATVPDKIKX0DER", "Portugal", "pt_PT", "com"),
    "SE": ("EU", "A2NODRKZP88ZB9", "Sweden", "sv_SE", "com"),
    "JP": ("FE", "A1VC38T7YXB528", "Japan", "ja_JP", "co.jp"),
    "AU": ("FE", "A39IBJ37TRP1C6", "Australia", "en_AU", "com.au"),
    "NZ": ("FE", "A39IBJ37TRP1C6", "New Zealand", "en_NZ", "com.au"),
}


def _region(country_code: str) -> dict:
    code = (country_code or "US").strip().upper()
    data = _REGIONS.get(code)
    if not data:
        raise ValueError(f"Unsupported country: {code!r}. Available: {', '.join(sorted(_REGIONS))}")
    continent, marketplace_id, pretty_name, locale, domain_tld = data
    return {
        "country": code, "continent": continent, "marketplace_id": marketplace_id,
        "pretty_name": pretty_name, "locale": locale, "domain_tld": domain_tld,
    }


def _code_verifier() -> bytes:
    return base64.urlsafe_b64encode(secrets.token_bytes(32)).rstrip(b"=")


def _code_challenge(verifier: bytes) -> str:
    digest = hashlib.sha256(verifier).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode()


def _device_serial() -> str:
    return "PIXEL5" + secrets.token_hex(16).upper()


def _login_client_id(serial: str) -> str:
    return (serial.encode() + f"#{_MUSIC_DEVICE_TYPE}".encode()).hex()


def _build_oauth_url(region: dict, code_verifier: bytes, serial: str) -> str:
    params = {
        "openid.pape.max_auth_age": "0",
        "openid.identity": "http://specs.openid.net/auth/2.0/identifier_select",
        "accountStatusPolicy": "P1",
        "language": region["locale"],
        "openid.return_to": "https://www.amazon.com/ap/maplanding",
        "openid.assoc_handle": _ASSOC_HANDLE,
        "openid.oa2.response_type": "code",
        "openid.mode": "checkid_setup",
        "openid.ns.pape": "http://specs.openid.net/extensions/pape/1.0",
        "openid.oa2.code_challenge_method": "S256",
        "openid.ns.oa2": "http://www.amazon.com/ap/ext/oauth/2",
        "openid.oa2.code_challenge": _code_challenge(code_verifier),
        "openid.oa2.scope": "device_auth_access",
        "openid.claimed_id": "http://specs.openid.net/auth/2.0/identifier_select",
        "openid.oa2.client_id": f"device:{_login_client_id(serial)}",
        "disableLoginPrepopulate": "0",
        "openid.ns": "http://specs.openid.net/auth/2.0",
        "forceMobileLayout": "true",
    }
    return f"https://www.amazon.com/ap/signin?{urlencode(params)}"


def _prompt_authorization_code(oauth_url: str, pretty_name: str) -> str:
    console.print(
        "\n[cyan]=== Amazon Music login (browser) ===[/cyan]\n\n"
        "1. Open this URL in your browser (Ctrl+click if your terminal supports it):\n"
        f"\n[underline]{oauth_url}[/underline]\n\n"
        f"2. Sign in with your Amazon account ({pretty_name}). Complete any CAPTCHA/2FA normally in the browser.\n\n"
        "3. After signing in you'll land on an error / 'not found' page (maplanding) — that is expected.\n\n"
        "4. Copy the FULL url from the address bar and paste it below.\n"
    )
    pasted = Prompt.ask("\nPaste the URL after logging in").strip()
    if not pasted:
        raise ValueError("Login cancelled: no URL pasted.")

    query = parse_qs(urlparse(pasted).query)
    codes = query.get("openid.oa2.authorization_code")
    if not codes:
        raise ValueError(
            "Pasted URL is not valid: missing 'openid.oa2.authorization_code'. "
            "Make sure you copied the URL of the page shown right after login."
        )
    return codes[0]


def _normalize_pem(dpk: Optional[str]) -> Optional[str]:
    if not dpk:
        return None
    if "BEGIN" in str(dpk):
        return str(dpk)
    try:
        from Cryptodome.PublicKey import RSA

        return RSA.import_key(base64.b64decode(dpk)).export_key("PEM").decode()
    except Exception as e:
        logger.debug(f"pem normalize failed: {e}")
        return None


def _register(region: dict, serial: str, authorization_code: str, code_verifier: bytes) -> dict:
    body = {
        "requested_token_type": ["bearer", "mac_dms", "website_cookies", "store_authentication_cookie"],
        "cookies": {"website_cookies": [], "domain": f".amazon.{region['domain_tld']}"},
        "registration_data": {
            "domain": "Device",
            "app_version": _APP_VERSION,
            "device_serial": serial,
            "device_type": _MUSIC_DEVICE_TYPE,
            "device_name": f"VibraVid {uuid.uuid4().hex[:8]} Android Device (MP3)",
            "os_version": "11",
            "software_version": "523160014",
            "device_model": "Pixel 5",
            "app_name": "Amazon Music",
        },
        "auth_data": {
            "client_id": _login_client_id(serial),
            "authorization_code": authorization_code,
            "code_verifier": code_verifier.decode(),
            "code_algorithm": "SHA-256",
            "client_domain": "DeviceLegacy",
        },
        "requested_extensions": ["device_info", "customer_info"],
    }

    session = create_client(headers={"Content-Type": "application/json; charset=UTF-8"})
    r = session.post(
        f"https://api.amazon.{region['domain_tld']}/auth/register",
        data=json.dumps(body).encode(),
        timeout=30,
    )
    data = r.json()
    if r.status_code != 200 or "success" not in data.get("response", {}):
        raise ValueError(f"Device registration failed ({r.status_code}): {data}")

    success = data["response"]["success"]
    tokens = success["tokens"]
    return {
        "adp_token": tokens["mac_dms"]["adp_token"],
        "device_private_key": _normalize_pem(tokens["mac_dms"]["device_private_key"]),
        "extensions": success.get("extensions", {}) or {},
    }


def _login_sign(path: str, body: str, adp_token: str, privkey: RSAPrivateKey) -> dict:
    date = datetime.datetime.now(datetime.timezone.utc).isoformat("T").replace("+00:00", "") + "Z"
    data = f"POST\n{path}\n{date}\n{body}\n{adp_token}"
    sig = privkey.sign(data.encode(), padding.PKCS1v15(), hashes.SHA256())
    return {
        "x-adp-token": adp_token,
        "x-adp-alg": "SHA256withRSA:1.0",
        "x-adp-signature": f"{base64.b64encode(sig).decode()}:{date}",
    }


def _authorize_device(region: dict, serial: str, adp_token: str, pem: str) -> Optional[str]:
    """Calls the stratus authorizeDevice endpoint to retrieve the customerId."""
    try:
        key = serialization.load_pem_private_key(pem.encode(), password=None)
    except Exception as e:
        logger.debug(f"authorize_device: invalid private key: {e}")
        return None
    if not isinstance(key, RSAPrivateKey):
        return None

    path = f"/{region['continent']}/api/stratus/"
    body_dict = {
        "capabilities": ["RETRIEVE_OWNED_CONTENT", "RETRIEVE_ROBIN_CONTENT"],
        "customerInfo": {"customerId": "", "deviceId": serial, "deviceType": _MUSIC_DEVICE_TYPE},
        "deviceId": serial, "deviceType": _MUSIC_DEVICE_TYPE,
        "targetDeviceId": serial, "targetDeviceType": _MUSIC_DEVICE_TYPE,
    }
    body = json.dumps(body_dict, separators=(",", ":"))
    headers = {
        "Accept": "application/json, text/javascript, */*",
        "Content-Type": "application/json; charset=UTF-8",
        "Content-Encoding": "amz-1.0",
        "X-Amz-Target": "com.amazon.stratus.StratusServiceExternal.authorizeDevice",
        "X-Amz-Requestid": str(uuid.uuid4()),
        **_login_sign(path, body, adp_token, key),
    }

    try:
        session = create_client(headers={"Accept": "application/json, text/javascript, */*"})
        r = session.post(f"https://music.amazon.com{path}", headers=headers, data=body.encode(), timeout=30)
        data = r.json()
    except Exception as e:
        logger.debug(f"authorize_device call failed: {e}")
        return None

    return ((data.get("device") or {}).get("customerId")) or None


def _has_full_credentials(login) -> bool:
    return bool(
        login.get(_LOGIN_KEY, "adp_token", default=None)
        and login.get(_LOGIN_KEY, "device_private_key", default=None)
        and login.get(_LOGIN_KEY, "customer_id", default=None)
    )


def login() -> bool:
    """Interactive login for Amazon Music. Saves device credentials to login.json."""
    login_cfg = config_manager.login

    if _has_full_credentials(login_cfg):
        console.print("[green]Amazon Music credentials already present in login.json — nothing to do.[/green]")
        return True

    country = Prompt.ask("Country code of your Amazon account (e.g. US, IT, GB, DE)", default="US").strip().upper()
    region = _region(country)

    code_verifier = _code_verifier()
    serial = _device_serial()
    oauth_url = _build_oauth_url(region, code_verifier, serial)

    authorization_code = _prompt_authorization_code(oauth_url, region["pretty_name"])

    console.print("[cyan]Registering device with Amazon...[/cyan]")
    reg = _register(region, serial, authorization_code, code_verifier)

    adp_token = reg["adp_token"]
    device_private_key = reg["device_private_key"]
    if not adp_token or not device_private_key:
        console.print("[red]Registration failed: adp_token / device_private_key missing from the response.[/red]")
        return False

    device_info = reg["extensions"].get("device_info") or {}
    device_id = device_info.get("device_serial_number") or serial
    device_type = device_info.get("device_type") or _MUSIC_DEVICE_TYPE

    customer_info = reg["extensions"].get("customer_info") or {}
    customer_id = customer_info.get("customerId") or customer_info.get("custId")

    if not customer_id:
        console.print("[cyan]Authorizing device to retrieve the customer id...[/cyan]")
        customer_id = _authorize_device(region, device_id, adp_token, device_private_key)

    if not customer_id:
        console.print(
            "[red]Login partially succeeded: could not retrieve the customer id "
            "(undocumented Amazon endpoint).[/red]\n"
            f"[yellow]adp_token and device_private_key were saved; you can set 'customer_id' manually "
            f"under the '{_LOGIN_KEY}' section of Conf/login.json if you know it.[/yellow]"
        )

    fields = {
        "device_id": device_id,
        "device_type": device_type,
        "marketplace_id": region["marketplace_id"],
        "territory": region["country"],
        "region_path": region["continent"],
        "locale": region["locale"],
        "adp_token": adp_token,
        "device_private_key": device_private_key,
    }
    if customer_id:
        fields["customer_id"] = customer_id

    for key, value in fields.items():
        login_cfg.set_key(_LOGIN_KEY, key, value)
    config_manager.save_login()

    console.print(f"[green]✓ Credentials saved to {config_manager.login_file_path}[/green]")
    return bool(customer_id)


def logout() -> bool:
    login_cfg = config_manager.login
    if login_cfg.get(_LOGIN_KEY, "adp_token", default=None) is None:
        console.print("[yellow]No Amazon Music credentials to remove.[/yellow]")
        return True
    login_cfg.set_key(_LOGIN_KEY, "adp_token", "")
    login_cfg.set_key(_LOGIN_KEY, "device_private_key", "")
    login_cfg.set_key(_LOGIN_KEY, "customer_id", "")
    config_manager.save_login()
    console.print(f"[green]✓[/green] Amazon Music credentials removed from {config_manager.login_file_path}")
    return True


class AmazonMusicClient:
    def __init__(self) -> None:
        login = config_manager.login
        self.device_id = login.get(_LOGIN_KEY, "device_id", default=None)
        self.device_type = login.get(_LOGIN_KEY, "device_type", default=None)
        self.customer_id = login.get(_LOGIN_KEY, "customer_id", default=None)
        self.marketplace = login.get(_LOGIN_KEY, "marketplace_id", default=None)
        self.territory = login.get(_LOGIN_KEY, "territory", default=None)
        self.region_path = login.get(_LOGIN_KEY, "region_path", default=None) or "EU"
        self.locale = login.get(_LOGIN_KEY, "locale", default=None) or "en_US"
        self.adp_token = login.get(_LOGIN_KEY, "adp_token", default=None)

        self._privkey: RSAPrivateKey | None = None
        pem = login.get(_LOGIN_KEY, "device_private_key", default=None) or ""
        if pem and self.adp_token:
            try:
                key = serialization.load_pem_private_key(pem.encode(), password=None)
                if isinstance(key, RSAPrivateKey):
                    self._privkey = key
                else:
                    logger.warning("Amazon Music: device_private_key is not an RSA key")
            except Exception:
                logger.exception("Amazon Music: failed to load device_private_key from login.json")

        self._session = create_client(headers={"Accept": "application/json, text/javascript, */*"})

    @property
    def is_available(self) -> bool:
        return self._privkey is not None

    def _sign(self, method: str, path: str, body: str) -> dict:
        date = datetime.datetime.now(datetime.timezone.utc).isoformat("T").replace("+00:00", "") + "Z"
        data = f"{method}\n{path}\n{date}\n{body}\n{self.adp_token}"
        sig = self._privkey.sign(data.encode(), padding.PKCS1v15(), hashes.SHA256())
        return {
            "x-adp-token": self.adp_token,
            "x-adp-alg": "SHA256withRSA:1.0",
            "x-adp-signature": f"{base64.b64encode(sig).decode()}:{date}",
        }

    def _post(self, path: str, target: str, body_dict: dict, timeout: int = 20) -> dict:
        body = json.dumps(body_dict, separators=(",", ":"))
        headers = {
            "Accept": "application/json, text/javascript, */*",
            "Content-Type": "application/json; charset=UTF-8",
            "Content-Encoding": "amz-1.0",
            "User-Agent": f"Harley/{_HARLEY_VER} {self.device_type}/{_APP_VER}",
            "X-Amz-Requestid": str(uuid.uuid4()),
            "X-Amz-Target": target,
            **self._sign("POST", path, body),
        }
        r = self._session.post(f"{_HOST}{path}", headers=headers, data=body.encode(), timeout=timeout)
        r.raise_for_status()
        return r.json()

    def _search_docs(self, query: str, limit: int, label: str) -> list[dict]:
        data = self._post(
            f"/{self.region_path}/api/textsearch/search/v1_1/",
            "com.amazon.tenzing.textsearch.v1_1.TenzingTextSearchServiceExternalV1_1.search",
            {
                "customerIdentity": {
                    "customerId": self.customer_id, "deviceId": self.device_id,
                    "deviceType": self.device_type, "sessionId": "",
                },
                "features": {"spellCorrection": {"allowCorrection": True}},
                "musicTerritory": self.territory, "locale": self.locale,
                "query": query,
                "resultSpecs": [{
                    "contentRestrictions": {
                        "eligibility": {"tier": "UNLIMITED"},
                        "allowedParentalControls": {"hasExplicitLanguage": True},
                        "assetQuality": {"quality": ["NOT_ASSIGNED", "HD", "UHD"]},
                    },
                    "label": label,
                    "documentSpecs": [{"type": label, "fields": ["__default", "contentEncoding", "artLarge", "artOriginal"]}],
                    "maxResults": limit,
                }],
            },
        )

        docs = []
        for category in data.get("results", []):
            for hit in category.get("hits", []):
                doc = hit.get("document") or {}
                if doc.get("asin"):
                    docs.append(doc)
        return docs

    @staticmethod
    def _doc_cover(doc: dict) -> str:
        for field in ("artLarge", "artOriginal"):
            art = doc.get(field)
            if isinstance(art, dict):
                url = art.get("URL") or art.get("artUrl")
                if url:
                    return url
        return doc.get("image", "")

    def search_songs(self, query: str, limit: int = 25) -> list[dict]:
        """Search tracks by free-text query."""
        if not self.is_available:
            logger.info("Amazon Music: search skipped, no device credentials in login.json['amazon_music']")
            return []
        try:
            docs = self._search_docs(query, limit, "catalog_track")
        except Exception as e:
            logger.warning(f"Amazon Music: songs search failed for {query!r}: {e}")
            return []

        return [
            {
                "id": d["asin"],
                "title": d.get("title") or "Unknown Title",
                "url": f"{_HOST}/tracks/{d['asin']}",
                "image": self._doc_cover(d),
                "duration": d.get("duration", 0),
                "artist": {"name": d.get("artistName") or "Unknown Artist"},
                "album": {"name": d.get("albumName") or ""},
            }
            for d in docs
        ]

    def search_albums(self, query: str, limit: int = 25) -> list[dict]:
        """Search albums by free-text query."""
        if not self.is_available:
            logger.info("Amazon Music: search skipped, no device credentials in login.json['amazon_music']")
            return []
        try:
            docs = self._search_docs(query, limit, "catalog_album")
        except Exception as e:
            logger.warning(f"Amazon Music: albums search failed for {query!r}: {e}")
            return []

        return [
            {
                "id": d["asin"],
                "name": d.get("title") or "Unknown Album",
                "url": f"{_HOST}/albums/{d['asin']}",
                "image": self._doc_cover(d),
                "artist": {"name": d.get("artistName") or "Unknown Artist"},
            }
            for d in docs
        ]

    def _lookup(self, asin: str, list_key: str) -> dict | None:
        data = self._post(
            f"/{self.region_path}/api/muse/",
            "com.amazon.musicensembleservice.MusicEnsembleService.lookup",
            {
                "asins": [asin], "features": ["expandTracklist", "ownership"],
                "deviceId": self.device_id, "deviceType": self.device_type,
                "musicTerritory": self.territory, "lang": self.locale,
                "requestedContent": "FULL_CATALOG",
            },
        )
        items = data.get(list_key) or []
        return items[0] if items else None

    def get_track(self, track_id: str) -> dict | None:
        """Get full metadata (title/duration/artist/album) for a single track by ASIN."""
        if not self.is_available:
            logger.info("Amazon Music: track lookup skipped, no device credentials in login.json['amazon_music']")
            return None
        try:
            t = self._lookup(track_id, "trackList")
        except Exception as e:
            logger.warning(f"Amazon Music: track lookup failed for '{track_id}': {e}")
            return None
        if not t:
            return None

        album = t.get("album") or {}
        return {
            "id": t.get("asin", track_id),
            "title": t.get("title") or "Unknown Title",
            "url": f"{_HOST}/tracks/{track_id}",
            "image": album.get("image", ""),
            "duration": t.get("duration", 0),
            "isrc": t.get("isrc"),
            "album": {"id": album.get("asin"), "name": album.get("title") or ""},
            "artist": {"id": (t.get("artist") or {}).get("asin"), "name": (t.get("artist") or {}).get("name") or ""},
        }

    def get_album(self, album_id: str) -> dict | None:
        """Get full album metadata + tracklist by ASIN."""
        if not self.is_available:
            logger.info("Amazon Music: album lookup skipped, no device credentials in login.json['amazon_music']")
            return None
        try:
            a = self._lookup(album_id, "albumList")
        except Exception as e:
            logger.warning(f"Amazon Music: album lookup failed for '{album_id}': {e}")
            return None
        if not a:
            return None

        artist = a.get("artist") or {}
        release_ms = a.get("originalReleaseDate")
        release_date = (
            datetime.datetime.fromtimestamp(release_ms / 1000, tz=datetime.timezone.utc).strftime("%Y-%m-%d")
            if release_ms else ""
        )

        songs = []
        for t in a.get("tracks") or []:
            t_artist = t.get("artist") or artist
            songs.append(
                {
                    "id": t.get("asin"),
                    "name": t.get("title") or "Unknown Track",
                    "url": f"{_HOST}/tracks/{t.get('asin')}" if t.get("asin") else None,
                    "image": a.get("image") or "",
                    "duration": t.get("duration", 0),
                    "isrc": t.get("isrc"),
                    "artist": {"id": t_artist.get("asin"), "name": t_artist.get("name") or ""},
                }
            )

        return {
            "id": a.get("asin", album_id),
            "name": a.get("title") or "Unknown Album",
            "url": f"{_HOST}/albums/{album_id}",
            "image": a.get("image") or "",
            "total_songs": a.get("trackCount"),
            "total_duration": a.get("duration"),
            "release_date": release_date,
            "artist": {"id": artist.get("asin"), "name": artist.get("name") or ""},
            "songs": songs,
        }


# Instance
amazon_music = AmazonMusicClient()
