# 29.01.26

import base64
import json
import logging

from rich.console import Console

from VibraVid.core.decryptor import KeysManager
from VibraVid.core.drm.system import accumulate_content_key, normalize_kid
from VibraVid.setup import binary_paths, get_info_prd
from VibraVid.utils.http_client import create_client

console = Console()
logger = logging.getLogger(__name__)


def get_playready_keys(
    pssh_list: list[dict],
    license_url: str,
    cdm_device_path: str = None,
    cdm_remote_api: list[str] = None,
    headers: dict = None,
    key: str = None,
    license_data: dict = None,
    prefer_remote_cdm: bool = True,
    license_request_fn=None,
):
    """
    Extract PlayReady CONTENT keys (KID/KEY) from a license.

    Args:
        pssh_list (list[dict]): List of dicts {'pssh': ..., 'kid': ..., 'type': ...}
        license_url (str): PlayReady license URL (may include query params).
        cdm_device_path (str): Path to local .prd CDM file. Optional if using remote.
        cdm_remote_api (list): Remote CDM API config. Optional if using local device.
        headers (dict): HTTP headers for the license request.
        key (str): Pre-existing KID:KEY — bypasses CDM entirely.
        license_data (dict):    Extra fields merged into the license request body BEFORE the challenge is added.
        prefer_remote_cdm (bool): Prefer remote CDM over local. If True and remote config missing, raises error instead of fallback.
        license_request_fn (Callable[[bytes, dict], str]): Optional callback that takes the raw
          challenge bytes and the current pssh_list item ({'pssh', 'kid', 'type'}) being licensed,
          and returns the decoded license payload (XML string)

    Returns:
        KeysManager | None
    """
    if key:
        manual = KeysManager(key)
        return manual if manual else None

    # Check if we have either local or remote CDM
    cdm_remote_api = cdm_remote_api if cdm_remote_api else None

    if prefer_remote_cdm and cdm_remote_api is None:
        logger.error("PlayReady: prefer_remote_cdm=true but no remote CDM config found")
        console.print(
            "[red]Error: prefer_remote_cdm=true but no remote CDM config found. Database lookup will continue."
            f"\n[yellow]If no database key exists, place device.prd in:[/] [white]{binary_paths.get_binary_directory()}[/white]"
        )

        # Return None here to skip CDM extraction but allow database lookup in manager._resolve_keys
        return None

    if not prefer_remote_cdm and cdm_device_path is None:
        logger.error("PlayReady: prefer_remote_cdm=false but no local CDM device found")
        console.print(
            "[red]Error: prefer_remote_cdm=false but no local CDM device found. Database lookup will continue."
            f"\n[yellow]If no database key exists, place device.prd in:[/] [white]{binary_paths.get_binary_directory()}[/white]"
        )

        # Return None here to skip CDM extraction but allow database lookup in manager._resolve_keys
        return None

    if cdm_device_path is None and cdm_remote_api is None:
        logger.error("Must provide either cdm_device_path or cdm_remote_api")
        console.print(
            "[red]Error: Must provide either cdm_device_path or cdm_remote_api."
            f"\n[yellow]Place device.prd in:[/] [white]{binary_paths.get_binary_directory()}[/white]"
        )
        return None

    return _get_playready_keys_local_cdm(
        pssh_list, license_url, cdm_device_path, cdm_remote_api, headers, license_data, license_request_fn
    )


def _get_playready_keys_local_cdm(
    pssh_list: list[dict],
    license_url: str,
    cdm_device_path: str,
    cdm_remote_api: list[str],
    headers: dict = None,
    license_data: dict = None,
    license_request_fn=None,
):
    """Extract PlayReady keys using local or remote CDM device."""
    if license_url is None and license_request_fn is None:
        logger.error("PlayReady: no license_url and no license_request_fn — cannot request a license, skipping CDM")
        console.print("[red]No license URL and no custom license request function — skipping CDM extraction.")
        return None

    from pyplayready.cdm import Cdm
    from pyplayready.device import Device
    from pyplayready.remote.remotecdm import RemoteCdm
    from pyplayready.system.pssh import PSSH

    cdm = None
    if cdm_device_path is not None:
        console.print(f"\n{get_info_prd(cdm_device_path)}")
        try:
            device = Device.load(cdm_device_path)
            cdm = Cdm.from_device(device)
        except Exception as e:
            logger.error(f"Error loading local CDM device: {e}")
            console.print(f"[red]Error loading local CDM device: {e}")
            return None
    else:
        console.print("[green]Using remote CDM.")
        try:
            cdm = RemoteCdm(**cdm_remote_api)
        except Exception as e:
            logger.error(f"Error initializing remote CDM: {e}")
            console.print(f"[red]Error initializing remote CDM: {e}")
            return None

    # Open CDM session
    session_id = cdm.open()
    all_content_keys = []
    extracted_kids = set()

    try:
        for item in pssh_list:
            pssh = item["pssh"]
            kid_info = normalize_kid(item.get("kid", "N/A"))
            type_info = item.get("type", "unknown")

            # Skip extra PSSH variants once this KID's key is already extracted (a manifest may
            # offer several PSSH boxes for the same KID where only one is valid for the server).
            if kid_info and kid_info != "n/a" and kid_info in extracted_kids:
                continue

            console.print(f"[red]{type_info} [cyan](PSSH: [yellow]{pssh[:30]}...[cyan] KID: [red]{kid_info})")

            # Parse PSSH
            pssh_obj = PSSH(pssh)
            if not pssh_obj.wrm_headers:
                logger.error(f"No WRM headers found in PSSH for {kid_info}")
                console.print("[red]No WRM headers found in PSSH")
                continue

            # Create license challenge
            challenge = cdm.get_license_challenge(session_id, pssh_obj.wrm_headers[0])
            challenge_bytes = challenge if isinstance(challenge, bytes) else challenge.encode("utf-8")

            # Custom license request (service-supplied): bypass the built-in HTTP POST.
            if license_request_fn is not None:
                try:
                    license_payload = license_request_fn(challenge_bytes, item)
                except Exception as e:
                    logger.error(f"Custom license request error for {kid_info}: {e}")
                    console.print(f"[red]License request error for PSSH {pssh[:30]}...: {e}")
                    continue

                if not license_payload:
                    console.print(f"[red]License data is empty for PSSH {pssh[:30]}...]")
                    continue

                try:
                    cdm.parse_license(session_id, license_payload)
                    for key_obj in cdm.get_keys(session_id):
                        accumulate_content_key(all_content_keys, extracted_kids, key_obj.key_id.hex, key_obj.key.hex())
                except Exception as e:
                    logger.error(f"Error extracting keys for {kid_info}: {e}")
                    console.print(f"[red]Error extracting keys for PSSH {pssh[:30]}...: {e}")
                continue

            # Build request body and headers
            req_headers = (headers or {}).copy()

            if license_data:
                encoded_challenge = base64.b64encode(challenge_bytes).decode("utf-8")
                body = json.dumps(
                    {**license_data, "licenseChallenge": encoded_challenge}, separators=(",", ":")
                ).encode("utf-8")
                req_headers.pop("Content-Type", None)
                req_headers.pop("content-type", None)
                req_headers["Content-Type"] = "text/plain"
            else:
                body = challenge_bytes
                req_headers.setdefault("Content-Type", "text/xml; charset=utf-8")

            if license_url is None:
                console.print("\n[red]License URL is None.")
                continue

            logger.debug(f"License challenge for {kid_info}: {challenge_bytes}, type: {type(challenge_bytes)}")
            try:
                with create_client(headers=req_headers) as client:
                    response = client.post(license_url, data=body)
            except Exception as e:
                logger.error(f"License request error for {kid_info}: {e}")
                console.print(f"[red]License request error for pssh {pssh[:30]}...: {e}")
                continue

            if response.status_code != 200:
                logger.error(f"License error for {kid_info}: HTTP {response.status_code}")
                console.print(f"[red]Status error for {pssh}: {response.status_code}\nResponse text: {response.text}\nLicense url: {license_url}\nHeaders: {req_headers}\n")
                continue

            # Parse license response
            license_payload = response.text
            logger.debug(f"License response [{response.status_code}]: {response.text[:200]}")

            if license_data:
                try:
                    rj = response.json()
                    b64_license = rj.get("playReadyLicense", {}).get("license")
                    license_payload = base64.b64decode(b64_license).decode("utf-8")
                    logger.debug(f"Decoded PR license): {license_payload}")
                except Exception as e:
                    console.print(f"[red]Failed to parse license response: {e}\nRaw: {response.text[:200]}")
                    continue

            # Extract CONTENT keys
            try:
                cdm.parse_license(session_id, license_payload)
                for key_obj in cdm.get_keys(session_id):
                    accumulate_content_key(all_content_keys, extracted_kids, key_obj.key_id.hex, key_obj.key.hex())
            except Exception as e:
                logger.error(f"Error extracting keys: {e}")
                console.print(f"[red]Error extracting keys: {e}")
                continue

        if not all_content_keys:
            console.print("[yellow]No keys extracted")

        return KeysManager(all_content_keys) if all_content_keys else None

    except Exception as e:
        logger.error(f"Unexpected error during PlayReady key extraction: {e}")
        console.print(f"[red]Unexpected error during key extraction: {e}")
        return None

    finally:
        cdm.close(session_id)
