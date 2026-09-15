# 29.01.26

import base64
import json
import logging
from collections.abc import Callable

from rich.console import Console

from VibraVid.core.decryptor import KeysManager
from VibraVid.core.drm.system import accumulate_content_key, normalize_kid
from VibraVid.setup import binary_paths, get_info_wvd
from VibraVid.utils.http_client import create_client

console = Console()
logger = logging.getLogger(__name__)


def get_widevine_keys(
    pssh_list: list[dict],
    license_url: str,
    cdm_device_path: str = None,
    cdm_remote_api: list[str] = None,
    headers: dict = None,
    key: str = None,
    license_data: dict = None,
    license_certificate: str = None,
    prefer_remote_cdm: bool = True,
    license_request_fn: Callable[[bytes, dict], bytes] | None = None,
):
    """
    Extract Widevine CONTENT keys (KID/KEY) from a license.

    Args:
        pssh_list (list[dict]): List of dicts {'pssh': ..., 'kid': ..., 'type': ...}
        license_url (str): Widevine license URL.
        cdm_device_path (str): Path to local CDM file (device.wvd). Optional if using remote.
        cdm_remote_api (list[str]): Remote CDM API config. Optional if using local device.
        headers (dict): Optional HTTP headers for the license request (from fetch).
        key (str): Optional raw license data to bypass HTTP request.
        license_data (dict): Optional pre-fetched license data.
        license_certificate (str): Optional base64-encoded SignedMessage for CDM Privacy Mode. If None or empty, set_service_certificate is not called.
        prefer_remote_cdm (bool): Prefer remote CDM over local. If True and remote config missing, raises error instead of fallback.
        license_request_fn (Callable[[bytes, dict], bytes]): Optional callback that takes the raw
          challenge bytes and the current pssh_list item ({'pssh', 'kid', 'type'}) being licensed,
          and returns the raw license bytes. When provided, the built-in HTTP POST to license_url
          is bypassed — used by services whose license endpoint is a custom signed API call rather
          than a plain POST, and/or needs per-KID request data.

    Returns:
        list: List of strings "KID:KEY" (only CONTENT keys) or None if error.
    """
    if key:
        manual = KeysManager(key)
        return manual if manual else None

    # Check if we have either local or remote CDM
    cdm_remote_api = cdm_remote_api if cdm_remote_api else None
    if prefer_remote_cdm and cdm_remote_api is None:
        logger.error("Widevine: prefer_remote_cdm=true but no remote CDM config found")
        console.print(
            "[red]Error: prefer_remote_cdm=true but no remote CDM config found. Database lookup will continue."
            f"\n[yellow]If no database key exists, place device.wvd in:[/] [white]{binary_paths.get_binary_directory()}[/white]"
        )

        # Return None here to skip CDM extraction but allow database lookup in manager._resolve_keys
        return None

    if not prefer_remote_cdm and cdm_device_path is None:
        logger.error("Widevine: prefer_remote_cdm=false but no local CDM device found")
        console.print(
            "[red]Error: prefer_remote_cdm=false but no local CDM device found. Database lookup will continue."
            f"\n[yellow]If no database key exists, place device.wvd in:[/] [white]{binary_paths.get_binary_directory()}[/white]"
        )

        # Return None here to skip CDM extraction but allow database lookup in manager._resolve_keys
        return None

    if cdm_device_path is None and cdm_remote_api is None:
        logger.error("Must provide either cdm_device_path or cdm_remote_api")
        console.print(
            "[red]Error: Must provide either cdm_device_path or cdm_remote_api."
            f"\n[yellow]Place device.wvd in:[/] [white]{binary_paths.get_binary_directory()}[/white]"
        )
        return None

    return _get_widevine_keys(
        pssh_list,
        license_url,
        cdm_device_path,
        cdm_remote_api,
        headers,
        license_data,
        license_certificate,
        license_request_fn,
    )


def _get_widevine_keys(
    pssh_list: list[dict],
    license_url: str,
    cdm_device_path: str,
    cdm_remote_api: list[str],
    headers: dict = None,
    license_data: dict = None,
    license_certificate: str = None,
    license_request_fn: Callable[[bytes, dict], bytes] | None = None,
):
    """Extract Widevine keys using local or remote CDM device."""
    if license_url is None and license_request_fn is None:
        logger.error("Widevine: no license_url and no license_request_fn — cannot request a license, skipping CDM")
        console.print("[red]No license URL and no custom license request function — skipping CDM extraction.")
        return None

    from pywidevine.cdm import Cdm
    from pywidevine.device import Device, DeviceTypes
    from pywidevine.pssh import PSSH
    from pywidevine.remotecdm import RemoteCdm

    device = None
    cdm = None

    # Initialize device
    if cdm_device_path is not None:
        console.print(f"\n{get_info_wvd(cdm_device_path)}")
        try:
            device = Device.load(cdm_device_path)
            cdm = Cdm.from_device(device)
        except Exception as e:
            logger.error(f"Error loading local CDM device: {e}")
            console.print(f"[red]Error loading local CDM device: {e}")
            return None
    else:
        console.print("[cyan]Using remote CDM.")
        try:
            if cdm_remote_api["device_type"] == "ANDROID":
                cdm_remote_api["device_type"] = DeviceTypes.ANDROID
            elif cdm_remote_api["device_type"] == "CHROME":
                cdm_remote_api["device_type"] = DeviceTypes.CHROME
            else:
                logger.error(f"Unsupported remote CDM device type: {cdm_remote_api['device_type']}")
                console.print(f"[red]Unsupported remote CDM device type: {cdm_remote_api['device_type']}")
                return None
            cdm = RemoteCdm(**cdm_remote_api)
        except Exception as e:
            logger.error(f"Error initializing remote CDM: {e}")
            console.print(f"[red]Error initializing remote CDM: {e}")
            return None

    # Open CDM session
    session_id = cdm.open()

    if license_certificate:
        try:
            cert = license_certificate.strip().replace("\n", "").replace(" ", "")
            cdm.set_service_certificate(session_id, cert)
            console.print("[dim]Service certificate set (Privacy Mode enabled).")
        except Exception as e:
            logger.error(f"Failed to set service certificate: {e}")
            console.print(f"[yellow]Warning: Failed to set service certificate: {e}")

    all_content_keys = []
    extracted_kids = set()

    try:
        for _i, item in enumerate(pssh_list):
            pssh = item["pssh"]
            kid_info = normalize_kid(item.get("kid", "N/A"))
            type_info = item.get("type", "unknown")

            # Skip extra PSSH variants once this KID's key is already extracted (a manifest may
            # offer several PSSH boxes for the same KID where only one is valid for the server).
            if kid_info and kid_info != "n/a" and kid_info in extracted_kids:
                continue

            console.print(f"[red]{type_info} [cyan](PSSH: [yellow]{pssh[:30]}...[cyan] KID: [red]{kid_info})")

            # Create license challenge
            challenge = cdm.get_license_challenge(session_id, PSSH(pssh))

            # Custom license request (service-supplied): bypass the built-in HTTP POST.
            if license_request_fn is not None:
                try:
                    license_bytes = license_request_fn(challenge, item)
                except Exception as e:
                    logger.error(f"Custom license request error for {kid_info}: {e}")
                    console.print(f"[red]License request error for PSSH {pssh[:30]}...: {e}")
                    continue

                if not license_bytes:
                    console.print(f"[red]License data is empty for PSSH {pssh[:30]}...]")
                    continue

                try:
                    cdm.parse_license(session_id, license_bytes)
                    for key_obj in cdm.get_keys(session_id):
                        if key_obj.type != "CONTENT":
                            continue

                        accumulate_content_key(all_content_keys, extracted_kids, key_obj.kid.hex, key_obj.key.hex())

                except Exception as e:
                    console.print(f"[red]Error extracting keys for PSSH {pssh[:30]}...: {e}")
                continue

            # Prepare headers (use original headers from fetch)
            req_headers = headers.copy() if headers else {}
            if "Content-Type" not in req_headers:
                req_headers["Content-Type"] = "application/octet-stream"

            if license_url is None:
                logger.error("License URL is None")
                console.print("\n[red]License URL is None.")
                continue

            if license_data:
                body = {
                    **license_data,
                    "licenseChallenge": base64.b64encode(challenge).decode("utf-8"),
                }
                req_headers["Content-Type"] = "text/plain"
                post_kwargs = {"data": json.dumps(body, separators=(",", ":"))}
            else:
                if "Content-Type" not in req_headers:
                    req_headers["Content-Type"] = "application/octet-stream"
                post_kwargs = {"data": challenge}

            # Make license request
            try:
                with create_client(headers=req_headers) as client:
                    response = client.post(license_url, **post_kwargs)
            except Exception as e:
                logger.error(f"License request error for {kid_info}: {e}")
                console.print(f"[red]License request error for PSSH {pssh[:30]}...: {e}")
                continue

            if response.status_code != 200:
                logger.error(f"License error for {kid_info}: HTTP {response.status_code}")
                console.print(f"[red]Status error for {pssh}: {response.status_code}\nResponse text: {response.content.decode('latin-1')}\nLicense url: {license_url}\nHeaders: {req_headers}\n")
                continue

            # Parse license response
            content_type = response.headers.get("content-type", "").lower()
            license_bytes = response.content

            if "application/json" in content_type:
                logger.debug(f"Parsing JSON license response for {kid_info}")
                try:
                    resp_json = response.json()
                    if "widevineLicense" in resp_json:
                        license_bytes = base64.b64decode(resp_json["widevineLicense"]["license"])
                    elif "license" in resp_json:
                        license_bytes = base64.b64decode(resp_json["license"])
                    else:
                        logger.error(f"No license field in JSON response for {kid_info}: {list(resp_json.keys())}")
                        console.print(f"[red]No license field in JSON response for PSSH {pssh[:30]}...] — keys: {list(resp_json.keys())}")
                        continue

                except Exception as e:
                    logger.error(f"Error parsing JSON license response for {kid_info}: {e}")
                    console.print(f"[red]Error parsing JSON license response for PSSH {pssh[:30]}...: {e}")
                    pass  # SKIP JSON parsing error and try raw content
            else:
                logger.debug(f"Received non-JSON license response for {kid_info} (content-type: {content_type})")

            if not license_bytes:
                console.print(f"[red]License data is empty for PSSH {pssh[:30]}...]")
                continue

            # Extract CONTENT keys
            try:
                cdm.parse_license(session_id, license_bytes)
                for key_obj in cdm.get_keys(session_id):
                    if key_obj.type != "CONTENT":
                        continue

                    accumulate_content_key(all_content_keys, extracted_kids, key_obj.kid.hex, key_obj.key.hex())
            except Exception as e:
                console.print(f"[red]Error extracting keys for PSSH {pssh[:30]}...: {e}")
                continue

        if not all_content_keys:
            console.print("[yellow]No keys extracted")

        return KeysManager(all_content_keys) if all_content_keys else None

    except Exception as e:
        console.print(f"[red]Unexpected error during key extraction: {e}")
        return None

    finally:
        cdm.close(session_id)
