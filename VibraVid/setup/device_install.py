# 18.07.25

import logging
import os
from dataclasses import dataclass

from rich.console import Console

from .binary_paths import binary_paths

console = Console()
logger = logging.getLogger(__name__)


class DeviceSearcher:
    def __init__(self):
        self.base_dir = binary_paths.ensure_binary_directory()

    def _check_existing(self, ext: str) -> str | None:
        """Check for existing files with given extension in binary directory."""
        try:
            for file in os.listdir(self.base_dir):
                if file.lower().endswith(ext):
                    path = os.path.join(self.base_dir, file)
                    logger.debug(f"Found {ext} file in binary directory: {path}")
                    return path

            return None

        except Exception as e:
            logger.exception(f"Error checking existing {ext} files")
            console.print(f"[red]Error checking existing {ext} files: {e}")
            return None

    def _find_recursively(self, ext: str = None, start_dir: str = ".", filename: str = None) -> str | None:
        """
        Find file recursively by extension or exact filename starting from start_dir.
        If filename is provided, search for that filename. Otherwise, search by extension.
        """
        try:
            for root, _dirs, files in os.walk(start_dir):
                for file in files:
                    if filename:
                        if file == filename:
                            path = os.path.join(root, file)
                            logger.info(f"Found {filename} at {path}")
                            return path

                    elif ext:
                        if file.lower().endswith(ext):
                            path = os.path.join(root, file)
                            logger.info(f"Found {ext} at {path}")
                            return path

            return None
        except Exception as e:
            logger.exception(f"Error during recursive search for filename {filename}")
            console.print(f"[red]Error during recursive search for filename {filename}: {e}")
            return None

    def search(self, ext: str = None, filename: str = None) -> str | None:
        """
        Search for file with given extension or exact filename in binary directory or recursively.
        If filename is provided, search for that filename. Otherwise, search by extension.
        """
        if filename:
            try:
                target_path = os.path.join(self.base_dir, filename)
                if os.path.exists(target_path) and os.path.getsize(target_path) > 0:
                    logger.debug(f"Found {filename} in binary directory: {target_path}")
                    return target_path
            except Exception as e:
                logger.exception(f"Error checking for existing file {filename}")
                console.print(f"[red]Error checking for existing file {filename}: {e}")
                return None

            return self._find_recursively(filename=filename, start_dir=self.base_dir)

        else:
            path = self._check_existing(ext)
            if path:
                return path
            return self._find_recursively(ext=ext, start_dir=self.base_dir)


def check_device_wvd_path() -> str | None:
    """Check for device.wvd file in binary directory and extract from PNG if not found."""
    try:
        searcher = DeviceSearcher()
        return searcher.search(".wvd")
    except Exception:
        return None


def check_device_prd_path() -> str | None:
    """Check for device.prd file in binary directory and search recursively if not found."""
    try:
        searcher = DeviceSearcher()
        return searcher.search(".prd")
    except Exception:
        return None


@dataclass
class ServiceCdm:
    wvd_path: str | None = None
    prd_path: str | None = None
    widevine_remote: dict | None = None
    playready_remote: dict | None = None


def get_remote_cdm_registry() -> dict:
    """Named remote CDMs from config.json (``DRM.remote_cdm``): ``{id: <same block as DRM.widevine / DRM.playready>}``."""
    from VibraVid.utils import config_manager

    registry = config_manager.config.get_dict("DRM", "remote_cdm", default={})
    return registry if isinstance(registry, dict) else {}


def remote_cdm_system(cfg: dict) -> str:
    """Which DRM system a remote CDM block serves: an explicit ``system`` key wins, otherwise it is inferred."""
    explicit = str(cfg.get("system", "")).lower()
    if explicit in ("widevine", "playready"):
        return explicit

    device_type = str(cfg.get("device_type", cfg.get("Device Type", ""))).upper()
    device_name = str(cfg.get("device_name", cfg.get("Device Name", "")))
    if device_type == "PLAYREADY" or device_name.upper().startswith("SL"):
        return "playready"
    
    if str(cfg.get("type", "")).lower() == "decrypt_labs" or device_type:
        return "widevine"  # decrypt_labs without an SL* device, or a pywidevine block (ANDROID / CHROME)
    
    return "playready"  # a stock pyplayready block has no device_type


def resolve_service_cdm(site_name: str | None) -> ServiceCdm:
    """
    Resolve the service's "cdm" entry in login.json (a string or a list).
    """
    result = ServiceCdm()
    if not site_name:
        return result

    from VibraVid.utils import config_manager

    section = config_manager.login.get_section(site_name) or config_manager.login.get_section(site_name.lower())
    cdm_value = section.get("cdm") if section else None
    if not cdm_value:
        return result

    entries = [cdm_value] if isinstance(cdm_value, str) else list(cdm_value)

    searcher = None
    for entry in entries:
        if not entry:
            continue

        lowered = str(entry).lower()
        if lowered.endswith((".wvd", ".prd")):
            searcher = searcher or DeviceSearcher()
            resolved = searcher.search(filename=entry)
            if not resolved:
                console.print(f"[red]Error: [red]cdm[/red] file '{entry}' configured for '{site_name}' in login.json was not found in the binary directory.")
                raise FileNotFoundError(
                    f"cdm file '{entry}' configured for '{site_name}' in login.json was not found in the binary directory."
                )
            
            if lowered.endswith(".wvd"):
                result.wvd_path = resolved
            else:
                result.prd_path = resolved
            continue

        registry = get_remote_cdm_registry()
        remote_cfg = registry.get(entry)
        if not isinstance(remote_cfg, dict) or not remote_cfg:
            known = ", ".join(sorted(registry)) or "none defined"
            message = (
                f"cdm '{entry}' configured for '{site_name}' in login.json is neither a .wvd/.prd file "
                f"nor a remote CDM id in config.json DRM.remote_cdm (known ids: {known})."
            )
            console.print(f"[red]Error: {message}")
            raise FileNotFoundError(message)

        remote_cfg = {k: v for k, v in remote_cfg.items() if k != "system"}
        if remote_cdm_system(registry[entry]) == "widevine":
            result.widevine_remote = remote_cfg
        else:
            result.playready_remote = remote_cfg

    return result


def resolve_service_cdm_paths(site_name: str | None) -> tuple[str | None, str | None]:
    """Resolve per-service .wvd/.prd overrides from the service's "cdm" entry in login.json (remote CDM ids are ignored here)."""
    resolved = resolve_service_cdm(site_name)
    return resolved.wvd_path, resolved.prd_path
