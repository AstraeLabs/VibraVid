"""Offline, explicit migration of legacy JSON configuration. No application imports."""

import argparse
import json
import os
import shutil
import sys
import tempfile
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path

import tomlkit

_SKIP = object()
_SECTION_ALIASES = {"cs_chrome": "netflix"}
_REMOVED = {("DEFAULT", "debug_track_json"), ("REQUESTS", "bypasser_url")}
_OPTIONS = {"args", "extra_args", "cdm", "process", "country", "region", "locale", "device_id"}
_COOKIE_SITES = {
    "amazon_music",
    "appletv",
    "crunchyroll",
    "discoveryplus",
    "hbomax",
    "netflix",
    "nowtvit",
    "paramountplus",
    "primevideo",
    "timvision",
}


class MigrationError(ValueError):
    """An actionable migration failure, safe to print without credential values."""


def default_conf_dir() -> Path:
    """Use the same base-path rules as ConfigManager, without constructing it."""
    if os.environ.get("VIBRAVID_BASE_PATH"):
        return Path(os.environ["VIBRAVID_BASE_PATH"]) / "Conf"
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent / "Conf"
    source = Path(__file__).resolve().parent.parent / "Conf"
    return source if source.is_dir() else Path.cwd() / "Conf"


def default_templates_dir() -> Path:
    # Docker keeps an immutable snapshot outside the mounted Conf volume.
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
    for directory in (base / "Conf.defaults", Path(__file__).parent / "config_defaults", base / "Conf"):
        if all((directory / f"{name}.toml").is_file() for name in ("config", "login")):
            return directory
    raise MigrationError(
        "Local default templates were not found; provide --defaults-dir DIR containing both TOML files."
    )


def _read_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (ValueError, UnicodeError) as exc:
        # JSON parser messages may contain pieces of a secret: report location only.
        location = f" at line {exc.lineno}, column {exc.colno}" if isinstance(exc, json.JSONDecodeError) else ""
        raise MigrationError(f"{path.name}: invalid JSON{location}; no files were changed.") from None
    if not isinstance(value, dict):
        raise MigrationError(f"{path.name}: expected a JSON object; no files were changed.")
    return value


def _clean(value, path: str, warnings: list[str]):
    if value is None:
        warnings.append(f"{path}: null has no TOML equivalent; omitted (default retained if available).")
        return _SKIP
    if isinstance(value, dict):
        result = {}
        for key, child in value.items():
            cleaned = _clean(child, f"{path}.{key}", warnings)
            if cleaned is not _SKIP:
                result[key] = cleaned
        return result
    if isinstance(value, list):
        # Do not silently remove list entries: that can alter hook arguments or CDM rules.
        if any(child is None for child in value):
            warnings.append(f"{path}: array contains null; setting omitted, review the JSON backup.")
            return _SKIP
        result = [_clean(child, f"{path}[{index}]", warnings) for index, child in enumerate(value)]
        if any(child is _SKIP for child in result):
            warnings.append(f"{path}: contains an unmigratable array; setting omitted, review the JSON backup.")
            return _SKIP
        return result
    return value


def _rename(data: dict, old: str, new: str, path: str, warnings: list[str]) -> None:
    if old not in data:
        return
    value = data.pop(old)
    if new in data and data[new] != value:
        warnings.append(f"{path}.{old}: conflicts with {new}; kept {new}, review the JSON backup.")
    else:
        data[new] = value


def _migrate_drm(data: dict, warnings: list[str]) -> None:
    drm = data.get("DRM")
    if not isinstance(drm, dict):
        return
    prefer = drm.pop("prefer_remote_cdm", False)
    choices = []
    for system in ("widevine", "playready"):
        remote = drm.pop(system, None)
        if not remote:
            continue
        if not isinstance(remote, dict):
            warnings.append(f"config.DRM.{system}: expected an object; omitted, review the JSON backup.")
            continue
        registry = drm.setdefault("remote_cdm", {})
        if not isinstance(registry, dict):
            raise MigrationError("config.DRM.remote_cdm: expected an object; no files were changed.")
        name = f"legacy_{system}"
        while name in registry:
            name += "_"
        registry[name] = {**remote, "system": system}
        choices.append(name)
    if choices:
        if "cdm" not in drm:
            drm["cdm"] = [*choices, "search"] if prefer else ["search", *choices]
        warnings.append(
            "config.DRM: inline remote CDMs moved to remote_cdm; review cdm selection. "
            "The former local/remote retry fallback cannot be reproduced exactly."
        )
    elif prefer:
        warnings.append("config.DRM.prefer_remote_cdm: removed; select a remote_cdm id with DRM.cdm manually.")


def _migrate_login(data: dict, defaults: dict, warnings: list[str]) -> dict:
    for old, new in _SECTION_ALIASES.items():
        _rename(data, old, new, "login", warnings)
    for site, section in data.items():
        path = f"login.{site}"
        template = defaults.get(site, {})
        # Some cookie exports were pasted as the entire service section.
        if isinstance(section, (list, str)) and site in _COOKIE_SITES:
            section = data[site] = {"cookies": section}
        if not isinstance(section, dict):
            warnings.append(f"{path}: expected an object; omitted, review the JSON backup.")
            data[site] = {}
            continue
        if site not in defaults and site != "Provider":
            warnings.append(f"{path}: service is not in the current template; preserved for manual review.")
        _rename(section, "extra_args", "args", path, warnings)
        if site == "nowtvit":
            for old in ("skyCEsidismesso01 ", "skyCEsidismesso01", "skyCEsidmesso01 "):
                _rename(section, old, "skyCEsidmesso01", path, warnings)
        if site == "disney":
            _rename(section, "access_token", "token", path, warnings)
        if "cookies" in template:
            cookies = {key: section.pop(key) for key in list(section) if key not in _OPTIONS and key != "cookies"}
            if cookies:
                existing = section.get("cookies")
                if existing and existing != "[paste here]":
                    # Keep flat fields as explicit overrides, matching ConfigAccessor's precedence.
                    section.update(cookies)
                    warnings.append(
                        f"{path}: both flat credentials and cookies exist; preserved both, review manually."
                    )
                else:
                    section["cookies"] = cookies
        for field in ("cookies", "rtilogin_acd", "token"):
            value = section.get(field)
            if isinstance(value, (dict, list)):
                section[field] = json.dumps(value, ensure_ascii=False)
        if "cookies" in section and section["cookies"] not in ("", "[paste here]"):
            try:
                cookies = json.loads(section["cookies"])
                if isinstance(cookies, dict) and isinstance(cookies.get("cookies"), list):
                    cookies = cookies["cookies"]
                valid = isinstance(cookies, dict) or (
                    isinstance(cookies, list)
                    and all(
                        isinstance(cookie, dict) and (("name" in cookie) == ("value" in cookie)) for cookie in cookies
                    )
                )
            except (ValueError, TypeError):
                valid = False
            if not valid:
                del section["cookies"]
                warnings.append(f"{path}.cookies: invalid cookie JSON; default retained, review the JSON backup.")
    return data


def _merge(document, data: dict, warnings: list[str], path: str, *, check_schema: bool = False) -> None:
    for key, value in data.items():
        setting = f"{path}.{key}"
        if check_schema and (path.removeprefix("config."), key) in _REMOVED:
            warnings.append(f"{setting}: removed option; omitted, review the JSON backup.")
            continue
        if check_schema and key not in document:
            warnings.append(f"{setting}: not in the current template; preserved for manual review.")
        if key in document:
            current = document[key].unwrap() if hasattr(document[key], "unwrap") else document[key]
            compatible = isinstance(value, dict) if isinstance(current, dict) else type(value) is type(current)
            # TOML numbers and the flexible cdm selector legitimately accept multiple types.
            compatible |= type(current) in (int, float) and type(value) in (int, float)
            compatible |= key == "cdm" and isinstance(value, (str, list, dict))
            if not compatible:
                warnings.append(f"{setting}: incompatible type; default retained, review the JSON backup.")
                continue
        if isinstance(value, dict):
            if key not in document or not isinstance(document[key], dict):
                document[key] = tomlkit.table()
            _merge(document[key], value, warnings, setting, check_schema=check_schema)
        else:
            document[key] = value


def migrate(
    conf_dir: Path, *, defaults_dir: Path | None = None, dry_run: bool = False, overwrite: bool = False
) -> dict:
    """Plan both files before writing; leave sources intact and back up every existing file."""
    conf_dir = Path(conf_dir)
    sources = [conf_dir / f"{name}.json" for name in ("config", "login") if (conf_dir / f"{name}.json").is_file()]
    if not sources:
        raise MigrationError(f"No config.json or login.json found in {conf_dir}.")
    defaults_dir = Path(defaults_dir) if defaults_dir is not None else default_templates_dir()
    warnings: list[str] = []
    planned = {}
    for source in sources:
        target = source.with_suffix(".toml")
        if target.exists() and not overwrite:
            raise MigrationError(
                f"{target.name} already exists; use --overwrite to replace it with a backed-up migration."
            )
        try:
            document = tomlkit.parse((defaults_dir / target.name).read_text(encoding="utf-8"))
        except (ValueError, UnicodeError) as exc:
            raise MigrationError(f"Invalid default template {target.name}; provide --defaults-dir DIR.") from exc
        data = _clean(_read_json(source), source.stem, warnings)
        if source.stem == "config":
            _migrate_drm(data, warnings)
        else:
            data = _migrate_login(deepcopy(data), document.unwrap(), warnings)
        _merge(document, data, warnings, source.stem, check_schema=source.stem == "config")
        content = tomlkit.dumps(document)
        tomlkit.parse(content)  # validate every output before making any changes
        planned[target] = content

    report = {"files": list(planned), "warnings": warnings, "backup_dir": None, "dry_run": dry_run}
    if dry_run:
        return report

    # A private directory also protects copies of previously world-readable login files.
    backup_dir = Path(
        tempfile.mkdtemp(prefix=datetime.now(timezone.utc).strftime("migration-%Y%m%dT%H%M%SZ-"), dir=conf_dir)
    )
    report["backup_dir"] = backup_dir
    staged: dict[Path, Path] = {}
    written = []
    try:
        for path in [*sources, *(target for target in planned if target.exists())]:
            shutil.copyfile(path, backup_dir / path.name)
            (backup_dir / path.name).chmod(0o600)
        # Stage both files on the same filesystem before replacing either destination.
        for target, content in planned.items():
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=conf_dir, suffix=".tmp", delete=False
            ) as stream:
                staged[target] = Path(stream.name)
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
        for target, temporary in staged.items():
            os.replace(temporary, target)
            written.append(target)
    except OSError:
        # Restore any destination already replaced if a later write fails.
        try:
            for target in reversed(written):
                backup = backup_dir / target.name
                if backup.exists():
                    with tempfile.NamedTemporaryFile(dir=conf_dir, suffix=".tmp", delete=False) as stream:
                        temporary = Path(stream.name)
                    try:
                        shutil.copyfile(backup, temporary)
                        os.replace(temporary, target)
                    finally:
                        temporary.unlink(missing_ok=True)
                else:
                    target.unlink()
        except OSError:
            raise MigrationError(
                f"Migration write and rollback failed; restore files manually from {backup_dir}."
            ) from None
        raise MigrationError(f"Migration write failed; check permissions and backups in {backup_dir}.") from None
    finally:
        for temporary in staged.values():
            temporary.unlink(missing_ok=True)
    return report


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Migrate legacy JSON settings locally, without starting VibraVid or making network requests."
    )
    parser.add_argument(
        "--migrate-config",
        nargs="?",
        type=Path,
        const=default_conf_dir(),
        metavar="DIR",
        required=True,
        help="Directory containing config.json and/or login.json (default: active Conf directory)",
    )
    parser.add_argument(
        "--defaults-dir",
        type=Path,
        metavar="DIR",
        help="Local directory containing default config.toml and login.toml templates",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report migration results without writing files or displaying credentials",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Back up and replace existing TOML files; JSON settings take precedence",
    )
    args = parser.parse_args(argv)
    try:
        report = migrate(
            args.migrate_config, defaults_dir=args.defaults_dir, dry_run=args.dry_run, overwrite=args.overwrite
        )
    except (MigrationError, OSError) as exc:
        print(f"Migration failed: {exc}", file=sys.stderr)
        raise SystemExit(1) from None
    print("Dry run: no files changed." if report["dry_run"] else "Migration complete; JSON sources were kept.")
    for path in report["files"]:
        print(f"{'Would write' if report['dry_run'] else 'Wrote'} {path}")
    if report["backup_dir"]:
        print(f"Backups: {report['backup_dir']}")
    for warning in report["warnings"]:
        print(f"Review: {warning}")


if __name__ == "__main__":
    main()
