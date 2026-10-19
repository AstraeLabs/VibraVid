# 18.07.25

import json
import logging
import os
from dataclasses import dataclass

from rich.console import Console

from .binary_paths import binary_paths

console = Console()
logger = logging.getLogger(__name__)

CDM_SEARCH = "search"
CDM_DEFAULT = "default"
_RESERVED_CDM_IDS = (CDM_SEARCH, CDM_DEFAULT)
CLI_ORIGIN = "--cdm"


class DeviceSearcher:
    def __init__(self):
        self.base_dir = binary_paths.ensure_binary_directory()
        self.tools_dir = binary_paths.get_tools_directory()
        self.cdm_dir = binary_paths.get_cdm_directory()
        self.prd_dir = binary_paths.get_prd_directory()
        self.wvd_dir = binary_paths.get_wvd_directory()
        self.previous_dir = binary_paths.get_legacy_binary_directory()

    def _fallback_dirs(self) -> list[str]:
        """Flat directories searched after the dedicated CDM directory: the binary root, then the pre-tools one."""
        return [d for d in (self.base_dir, self.previous_dir) if d and os.path.isdir(d)]

    def _cdm_dir_for_ext(self, ext: str) -> str | None:
        """Return the dedicated CDM subdirectory for *ext* (.wvd / .prd), else None."""
        ext = ext.lower()
        if ext == ".wvd":
            return self.wvd_dir
        if ext == ".prd":
            return self.prd_dir
        return None

    def _target_dir(self, filename: str | None) -> str | None:
        """Directory where a file named *filename* should live: cdm/wvd, cdm/prd or the binary root."""
        if not filename:
            return self.base_dir
        low = filename.lower()
        if low.endswith(".wvd"):
            return self.wvd_dir
        if low.endswith(".prd"):
            return self.prd_dir
        return self.base_dir

    def _check_existing(self, ext: str) -> str | None:
        """Check for existing files with given extension in their dedicated CDM directory (or binary root)."""
        try:
            dedicated = self._cdm_dir_for_ext(ext)
            scan_dirs = ([dedicated] if dedicated and os.path.isdir(dedicated) else []) + self._fallback_dirs()
            for scan_dir in scan_dirs:
                for file in os.listdir(scan_dir):
                    if file.lower().endswith(ext) and os.path.isfile(os.path.join(scan_dir, file)):
                        path = os.path.join(scan_dir, file)
                        logger.debug(f"Found {ext} file in {scan_dir}: {path}")
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

    def _search_roots(self) -> list[str]:
        """Folders searched recursively for a named device file: the whole binary folder (the managed one lives inside it) and the managed one."""
        roots = []
        for d in (self.previous_dir, self.base_dir):
            if d and os.path.isdir(d) and not any(os.path.commonpath([os.path.abspath(d), os.path.abspath(r)]) == os.path.abspath(r) for r in roots):
                roots.append(d)
        return roots

    def search(self, ext: str = None, filename: str = None) -> str | None:
        """
        Search for file with given extension or exact filename in binary directory or recursively.
        If filename is provided, search for that filename. Otherwise, search by extension.
        """
        if filename:
            try:
                target_dir = self._target_dir(filename)
                target_path = os.path.join(target_dir, filename)
                if os.path.exists(target_path) and os.path.getsize(target_path) > 0:
                    logger.debug(f"Found {filename} in {target_dir}: {target_path}")
                    return target_path
                
                for fallback_dir in self._fallback_dirs():
                    fallback_path = os.path.join(fallback_dir, filename)
                    if os.path.isfile(fallback_path) and os.path.getsize(fallback_path) > 0:
                        logger.debug(f"Found {filename} in {fallback_dir}: {fallback_path}")
                        return fallback_path
                    
            except Exception as e:
                logger.exception(f"Error checking for existing file {filename}")
                console.print(f"[red]Error checking for existing file {filename}: {e}")
                return None

            for root in self._search_roots():
                found = self._find_recursively(filename=filename, start_dir=root)
                if found:
                    return found
            return None

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
    widevine_origin: str | None = None
    playready_origin: str | None = None


def get_remote_cdm_registry() -> dict:
    """Named remote CDMs from config.toml (``DRM.remote_cdm``): ``{id: <remote CDM block>}``."""
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
    
    if str(cfg.get("type", "")).lower() == "custom_api_1" or device_type:
        return "widevine"
    
    return "playready"  # a stock pyplayready block has no device_type


def describe_remote_cdm(cfg: dict) -> dict:
    """Name, device type (CHROME / ANDROID, Widevine only) and security level of a remote CDM block, as far as the block tells."""
    name = str(cfg.get("device_name", cfg.get("Device Name", "")) or "")
    kind = str(cfg.get("device_type", cfg.get("Device Type", "")) or "").upper()
    level = cfg.get("security_level", cfg.get("Security Level"))

    if remote_cdm_system(cfg) == "playready":
        if level is None and name.upper().startswith("SL") and name[2:].isdigit():
            level = int(name[2:]) * 1000
        return {"name": name, "type": None, "security_level": int(level) if str(level).isdigit() else None}

    if kind not in ("CHROME", "ANDROID"):
        kind = "CHROME" if name.lower().startswith("chrome") else "ANDROID"
    if level is None and len(name) == 2 and name[0].upper() == "L" and name[1].isdigit():
        level = int(name[1])
    return {"name": name, "type": kind, "security_level": int(level) if str(level).isdigit() else None}


def parse_cdm_cli(values: list[str] | None) -> list | None:
    """``--cdm`` values: plain entries, or JSON for a list / quality rules (``--cdm '[{"min_height":1080,"cdm":"my_l1"}]'``)."""
    if not values:
        return None

    parsed: list = []
    for raw in values:
        raw = str(raw).strip()
        if raw[:1] in ("[", "{"):
            try:
                loaded = json.loads(raw)
            except ValueError as exc:
                raise ValueError(f"--cdm: invalid JSON ({exc})") from exc
            parsed.extend(loaded if isinstance(loaded, list) else [loaded])
        elif raw:
            parsed.append(raw)

    return parsed or None


def _rule_label(item: dict) -> str:
    return ", ".join(f"{key} {item[key]}" for key in ("min_height", "max_height") if item.get(key) is not None)


def _applicable_entries(value, height: int | None, rule: str = "") -> list[tuple[str, str]]:
    """
    Flatten a ``cdm`` value (string, list, or tables with min_height / max_height) into ``(entry, rule)`` pairs.

    *height* None keeps every entry whatever its limits (used to validate a setting); otherwise only the ones valid at that height.
    """
    if not value:
        return []
    if isinstance(value, str):
        return [(value, rule)]

    items = [value] if isinstance(value, dict) else list(value)
    entries: list[tuple[str, str]] = []
    for item in items:
        if not isinstance(item, dict):
            if item:
                entries.append((str(item), rule))
            continue

        min_height, max_height = item.get("min_height"), item.get("max_height")
        if height is not None:
            if min_height is not None and height < int(min_height):
                continue
            if max_height is not None and height > int(max_height):
                continue
        entries.extend(_applicable_entries(item.get("cdm"), height, _rule_label(item) or rule))

    return entries


def _apply_cdm_entry(entry: str, origin: str, short: str, registry: dict, result: ServiceCdm, filled: set[str]) -> None:
    """Resolve one ``cdm`` entry into *result* (*origin* names its setting in errors, *short* in the log); a system already filled by an earlier entry is left alone."""
    from .system import get_prd_path, get_wvd_path

    lowered = entry.strip().lower()
    if lowered in ("", CDM_DEFAULT):  # "default" = defer to the next level
        return

    def fill(system: str, field: str, value, why: str = short) -> None:
        filled.add(system)
        setattr(result, field, value)
        setattr(result, f"{system}_origin", why)

    if lowered == CDM_SEARCH:
        why = "search"
        if "widevine" not in filled:
            fill("widevine", "wvd_path", get_wvd_path(), why)
        if "playready" not in filled:
            fill("playready", "prd_path", get_prd_path(), why)
        return

    if lowered.endswith((".wvd", ".prd")):
        system = "widevine" if lowered.endswith(".wvd") else "playready"
        if system in filled:
            return

        resolved = DeviceSearcher().search(filename=entry)
        if not resolved:
            message = f"cdm file '{entry}' configured for {origin} was not found in the binary directory."
            console.print(f"[red]Error: {message}")
            raise FileNotFoundError(message)

        fill(system, "wvd_path" if system == "widevine" else "prd_path", resolved)
        return

    remote_cfg = registry.get(entry)
    if not isinstance(remote_cfg, dict) or not remote_cfg:
        known = ", ".join(sorted(registry)) or "none defined"
        message = (
            f"cdm '{entry}' configured for {origin} is neither a .wvd/.prd file, "
            f"'{CDM_SEARCH}', '{CDM_DEFAULT}' nor a remote CDM id in config.toml DRM.remote_cdm (known ids: {known})."
        )
        console.print(f"[red]Error: {message}")
        raise FileNotFoundError(message)

    system = remote_cdm_system(remote_cfg)
    if system in filled:
        return

    remote_cfg = {k: v for k, v in remote_cfg.items() if k != "system"}
    fill(system, "widevine_remote" if system == "widevine" else "playready_remote", remote_cfg)


def _cdm_levels(site_name: str | None) -> list[tuple[str, str, object]]:
    """The places a ``cdm`` can be set, as (name for errors, short name for the log, value), highest priority first: the ``--cdm`` option, the service's login.toml, ``[DRM]`` in config.toml."""
    from VibraVid.core.ui.tracker import context_tracker
    from VibraVid.utils import config_manager

    levels: list[tuple[str, str, object]] = []
    override = getattr(context_tracker, "cdm_override", None)
    if override:
        levels.append((CLI_ORIGIN, CLI_ORIGIN, override))

    if site_name:
        section = config_manager.login.get_section(site_name) or config_manager.login.get_section(site_name.lower())
        levels.append((f"'{site_name}' in login.toml", f"login.toml '{site_name}'", section.get("cdm") if section else None))

    drm_section = config_manager.config.get_section("DRM") or {}
    levels.append(("[DRM] in config.toml", "config.toml [DRM]", drm_section.get("cdm", CDM_SEARCH)))
    return levels


def _reserved_ids(registry: dict) -> list[str]:
    return [
        f"DRM.remote_cdm id '{name}' is reserved (it means a CDM choice): rename it in config.toml."
        for name in registry
        if name.lower() in _RESERVED_CDM_IDS
    ]


def resolve_cdm(site_name: str | None, height: int | None = None) -> ServiceCdm:
    """
    Pick the Widevine and PlayReady CDM for a service.
    """
    registry = get_remote_cdm_registry()
    reserved = _reserved_ids(registry)
    if reserved:
        console.print(f"[red]Error: {reserved[0]}")
        raise ValueError(reserved[0])

    effective_height = 0 if height is None else int(height)
    result = ServiceCdm()
    filled: set[str] = set()
    for origin, short, value in _cdm_levels(site_name):
        for entry, rule in _applicable_entries(value, effective_height):
            _apply_cdm_entry(entry, f"{origin}, {rule}" if rule else origin, f"{short}, {rule}" if rule else short, registry, result, filled)

    _apply_cdm_entry(CDM_SEARCH, "no cdm set", "search", registry, result, filled)  # whatever is still uncovered
    return result


def validate_cdm_settings(cli_values: list | None = None) -> list[str]:
    """
Check that the ``cdm`` settings in config.toml, login.toml and the command line are valid, and that any referenced files exist. Return a list of problems found (empty if none).
    """
    from VibraVid.utils import config_manager

    registry = get_remote_cdm_registry()
    problems = _reserved_ids(registry)

    levels: list[tuple[str, object]] = []
    if cli_values:
        levels.append((CLI_ORIGIN, cli_values))
    for section_name, section in (config_manager._login_data or {}).items():
        if isinstance(section, dict) and section.get("cdm"):
            levels.append((f"'{section_name}' in login.toml", section["cdm"]))
    levels.append(("[DRM] in config.toml", (config_manager.config.get_section("DRM") or {}).get("cdm", CDM_SEARCH)))

    searcher = None
    for origin, value in levels:
        items = [value] if isinstance(value, (str, dict)) else value
        if not isinstance(items, (list, tuple)):
            problems.append(f"cdm in {origin} must be a string or a list, not {type(value).__name__}.")
            continue

        for item in items:
            if isinstance(item, dict):
                for key in ("min_height", "max_height"):
                    if item.get(key) is not None and (isinstance(item[key], bool) or not isinstance(item[key], int)):
                        problems.append(f"cdm in {origin}: {key} must be a whole number of pixels, got {item[key]!r}.")
                if not item.get("cdm"):
                    problems.append(f"cdm in {origin}: a quality rule needs a 'cdm' entry.")
            elif not isinstance(item, str):
                problems.append(f"cdm in {origin}: unexpected entry {item!r}.")

        for entry, _rule in _applicable_entries([i for i in items if isinstance(i, (str, dict))], None):
            lowered = entry.strip().lower()
            if lowered in ("", CDM_DEFAULT, CDM_SEARCH):
                continue

            if lowered.endswith((".wvd", ".prd")):
                searcher = searcher or DeviceSearcher()
                if not searcher.search(filename=entry):
                    problems.append(f"cdm file '{entry}' in {origin} was not found in the binary directory.")
            elif entry not in registry:
                known = ", ".join(sorted(registry)) or "none defined"
                problems.append(f"cdm '{entry}' in {origin} is neither a .wvd/.prd file nor a remote CDM id (known ids: {known}).")

    return problems
