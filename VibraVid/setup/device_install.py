# 18.07.25

import logging
import os

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


def resolve_service_cdm_paths(site_name: str | None) -> tuple[str | None, str | None]:
    """Resolve per-service .wvd/.prd overrides from the service's "cdm" entry in login.json."""
    if not site_name:
        return None, None

    from VibraVid.utils import config_manager

    section = config_manager.login.get_section(site_name) or config_manager.login.get_section(site_name.lower())
    cdm_value = section.get("cdm") if section else None
    if not cdm_value:
        return None, None

    filenames = [cdm_value] if isinstance(cdm_value, str) else list(cdm_value)

    searcher = DeviceSearcher()
    wvd_path = prd_path = None
    for filename in filenames:
        if not filename:
            continue

        resolved = searcher.search(filename=filename)
        if not resolved:
            console.print(f"[red]Error: [red]cdm[/red] file '{filename}' configured for '{site_name}' in login.json was not found in the binary directory.")
            raise FileNotFoundError(
                f"cdm file '{filename}' configured for '{site_name}' in login.json was not found in the binary directory."
            )

        if filename.lower().endswith(".wvd"):
            wvd_path = resolved
        elif filename.lower().endswith(".prd"):
            prd_path = resolved
        else:
            console.print(f"[yellow]Warning: [red]cdm[/red] file '{filename}' configured for '{site_name}' in login.json has an unrecognized extension (expected .wvd or .prd) - ignoring.")
            logger.warning(f"Unrecognized cdm file extension for '{filename}' (site '{site_name}'), ignoring.")

    return wvd_path, prd_path
