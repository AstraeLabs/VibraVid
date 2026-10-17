# 29.01.26

import logging
import threading
from typing import Any

from VibraVid.core.decryptor import KeysManager
from VibraVid.core.ui.bar_manager import console
from VibraVid.core.ui.tracker import context_tracker
from VibraVid.setup import binary_paths, get_prd_path, get_wvd_path, resolve_service_cdm
from VibraVid.utils import config_manager
from VibraVid.utils.vault import DEFAULT_VAULT_KEYS, build_named_vault, build_universal_vault, default_vault
from VibraVid.utils.vault._url_utils import clean_license_url
from VibraVid.utils.vault.universal import CUSTOM_PROTOCOL, resolve_vault_protocol

from .playready import get_playready_keys
from .system import normalize_kid
from .widevine import get_widevine_keys

logger = logging.getLogger(__name__)
USE_CDM = config_manager.config.get_bool("DRM", "use_cdm")
BYPASS_VAULT_CACHE = config_manager.config.get_bool("DRM", "bypass_vault_cache")
BYPASS_TAG_SUFFIX = " (BYPASS)"


def _anonymize(value: str) -> str:
    """Mask every other character with '?' (position 0 real, 1 masked, 2 real, ...)."""
    return "".join(c if i % 2 == 0 else "?" for i, c in enumerate(value))


class DRMManager:
    @staticmethod
    def _build_vaults() -> list[object]:
        """Assemble the active vault list in DRM.vault declaration order; each vault instance carries its own `.name`.

        - "default" (or its old name "vault_1"): the built-in default vault singleton.
        - protocol "custom": your own vault speaking the default vault's REST contract (read + write).
        - protocol "api" / "json" / "query": a third-party vault (read-only unless "save_keys": true).
        An entry without "protocol" gets it auto-detected once and cached (see utils.vault.universal).
        """
        vaults: list[object] = []
        entries = {name: cfg for name, cfg in config_manager.config.get_dict("DRM", "vault", default={}).items() if isinstance(cfg, dict)}
        protocols = {name: CUSTOM_PROTOCOL if name in DEFAULT_VAULT_KEYS else resolve_vault_protocol(name, cfg) for name, cfg in entries.items()}
        rest_urls = {str(cfg.get("url") or "") for name, cfg in entries.items() if protocols[name] == CUSTOM_PROTOCOL}

        for position, (name, cfg) in enumerate(entries.items(), start=1):
            if name in DEFAULT_VAULT_KEYS:
                vdb = default_vault
            elif protocols[name] == CUSTOM_PROTOCOL:
                vdb = build_named_vault(name, cfg)
            else:
                vdb = build_universal_vault(name, cfg, known_urls=rest_urls)
            if vdb is not None and vdb.is_connected and vdb not in vaults:  # "default" + "vault_1" both map to one singleton
                vdb.position = position  # 1-based place in DRM.vault, shown next to the vault name
                vaults.append(vdb)

        return vaults

    def vault_label(self, name: str | None) -> str | None:
        """*name* followed by its vault's position in DRM.vault, e.g. "claudio [1]"; unchanged for a non-vault source such as "cdm"."""
        vdb = next((v for v in self._vaults if v.name == name), None)
        position = getattr(vdb, "position", None)
        return f"{name} [{position}]" if position else name

    def __init__(
        self,
        widevine_device_path: str = None,
        playready_device_path: str = None,
        widevine_remote_cdm_api: list[str] = None,
        playready_remote_cdm_api: list[str] = None,
        prefer_remote_cdm: bool = True,
        prefer_remote_widevine: bool | None = None,
        prefer_remote_playready: bool | None = None,
    ):
        """Initialize DRM Manager with CDM paths and database connections."""
        self.widevine_device_path = widevine_device_path
        self.playready_device_path = playready_device_path
        self.widevine_remote_cdm_api = widevine_remote_cdm_api
        self.playready_remote_cdm_api = playready_remote_cdm_api
        self.prefer_remote_cdm = prefer_remote_cdm
        self.prefer_remote_widevine = prefer_remote_widevine
        self.prefer_remote_playready = prefer_remote_playready
        self._vaults: list[object] = self._build_vaults()

    @classmethod
    def for_site(cls, site_name: str | None) -> "DRMManager":
        """
        Build the manager for a service, honouring the "cdm" entry of that service in login.toml.
        """
        chosen = resolve_service_cdm(site_name)
        drm = config_manager.config

        def _prefer(remote: dict | None, local_path: str | None) -> bool | None:
            if remote:
                return True
            if local_path:
                return False
            return None

        return cls(
            chosen.wvd_path or get_wvd_path(),
            chosen.prd_path or get_prd_path(),
            chosen.widevine_remote or drm.get_dict("DRM", "widevine", default={}),
            chosen.playready_remote or drm.get_dict("DRM", "playready", default={}),
            drm.get_bool("DRM", "prefer_remote_cdm"),
            prefer_remote_widevine=_prefer(chosen.widevine_remote, chosen.wvd_path),
            prefer_remote_playready=_prefer(chosen.playready_remote, chosen.prd_path),
        )

    def _display_keys(
        self,
        resolved: list[str],
        vault_keys: list[str],
        drm_type: str,
        pssh_val: str | None,
        source: str | None,
        header: bool,
        default_label: str | None = None,
        required_kids: set | None = None,
        failed: list[str] | None = None,
        tag_suffix: str | None = None,
        kid_flags: dict[str, str] | None = None,
    ) -> None:
        """Display resolved keys in the console, indicating which came from vaults and which were newly extracted."""
        if not resolved and not failed:
            return

        if header:
            pssh_disp = f"{pssh_val[:30]}..." if pssh_val and len(pssh_val) > 30 else (pssh_val or "...")
            console.print(f"\n[red]{drm_type} [cyan](PSSH: [yellow]{pssh_disp}[cyan])")

        label = source
        vault_kids = {k.split(":")[0].strip().lower() for k in vault_keys}
        plain = [k for k in resolved if k.split(":")[0].strip().lower() not in vault_kids]
        tagged = [k for k in resolved if k.split(":")[0].strip().lower() in vault_kids]

        anonymize = getattr(context_tracker, "anonymize_keys", False)
        for k in plain + tagged:
            kid_val, key_val = k.split(":", 1)
            if anonymize:
                kid_val, key_val = _anonymize(kid_val), _anonymize(key_val)
            if k in tagged:
                tag = self.vault_label(label)
            elif default_label:
                tag = default_label
            else:
                tag = None

            if tag and tag_suffix:
                tag = f"{tag}{tag_suffix}"

            flag = kid_flags.get(kid_val.strip().lower()) if kid_flags else None
            if tag and flag:
                tag = f"{tag} ({flag})"

            if tag:
                marker = "*" if required_kids and kid_val.strip().lower() in required_kids else ""
                suffix = f" [cyan]| [#a855f7]{marker}{tag}"
                log_suffix = f" | {marker}{tag}"
            else:
                suffix = ""
                log_suffix = ""

            console.print(f"    - [red]{kid_val}[white]:[green]{key_val}{suffix}")
            if "*" in log_suffix:
                logger.info(f"Using {kid_val}:{key_val}")

        for k in failed or []:
            kid_val, _, key_val = k.partition(":")
            if anonymize:
                kid_val, key_val = _anonymize(kid_val), _anonymize(key_val)
            console.print(f"    - [red]{kid_val}[white]:[green]{key_val} [cyan]| [red]failed")

    def _bypass_cache(self) -> bool:
        """Effective bypass-vault-cache flag: per-run CLI override wins over config default."""
        override = getattr(context_tracker, "bypass_vault_cache", None)
        return BYPASS_VAULT_CACHE if override is None else bool(override)

    def _announce_bypass(self, all_kids: list[str], base_license_url: str, pssh_val: str, drm_type: str) -> dict[str, list[str]]:
        """When the cache is bypassed, query every configured vault only to report which cached keys would have been used, without actually using them."""
        bypassed: dict[str, list[str]] = {}
        if not all_kids or not self._vaults:
            return bypassed

        for vdb in self._vaults:
            name = vdb.name
            try:
                keys = list(vdb.get_keys_by_kids(base_license_url or None, all_kids, pssh_val) or [])
            except Exception as e:
                logger.debug(f"Bypass announce lookup failed for {name} vault (non-fatal): {e}")
                continue

            for k in keys:
                kid_val, _, key_val = k.partition(":")
                logger.info(f"Bypassing cached {drm_type} key {kid_val}:{key_val} from {name}")
                bypassed.setdefault(name, []).append(k)

        return bypassed

    def _missing_kids(self, all_kids: list[str], found_keys: list[str]) -> list[str]:
        """Return list of KIDs that are in all_kids but not yet covered by found_keys."""
        found = {k.split(":")[0].strip().lower() for k in found_keys}
        return [kid for kid in all_kids if kid not in found]

    def _db_lookup(
        self, all_kids: list[str], base_license_url: str, drm_type: str, pssh_val: str = None
    ) -> tuple[list[str], str, dict[str, str]]:
        """Query vaults in priority order, stopping as soon as all KIDs are covered.
        The third return value is a kid -> tag-string map (e.g. {"abc123": "LICENSE_MISMATCH"})
        for display, collected from each vault's last_key_flags right after querying it."""
        found_keys: list[str] = []
        source = None
        kid_flags: dict[str, str] = {}

        if not all_kids or not base_license_url or not self._vaults:
            return found_keys, source, kid_flags

        for vdb in self._vaults:
            name = vdb.name
            missing = self._missing_kids(all_kids, found_keys)
            if not missing:
                break

            logger.info(f"Querying {name} DB for {len(missing)} {drm_type} KID(s) | PSSH={pssh_val}" if pssh_val else f"Querying {name} DB for {len(missing)} {drm_type} KID(s)")
            keys = list(vdb.get_keys_by_kids(base_license_url, missing, pssh_val) or [])
            kid_flags.update(getattr(vdb, "last_key_flags", None) or {})

            # Dropped keys that are all-zero (unusable)
            bad = [k for k in keys if set(k.split(":", 1)[1] if ":" in k else "") <= {"0"}]
            if bad:
                logger.warning(f"{name} DB returned {len(bad)} all-zero (unusable) {drm_type} key(s) — ignoring, will re-resolve via CDM")
                keys = [k for k in keys if k not in bad]

            if keys:
                found_keys.extend(keys)
                source = name

        return found_keys, source, kid_flags

    def _db_lookup_all(self, kids: list[str], base_license_url: str, pssh_val: str | None = None) -> list[tuple[str, str]]:
        """Like _db_lookup, but query every vault (no early stop) and return each "kid:key" with the vault it came from."""
        found: list[tuple[str, str]] = []
        if not kids or not base_license_url:
            return found

        for vdb in self._vaults:
            try:
                keys = list(vdb.get_keys_by_kids(base_license_url, kids, pssh_val) or [])
            except Exception as e:
                logger.debug(f"Fallback lookup failed for {vdb.name} vault (non-fatal): {e}")
                continue
            for k in keys:
                if set(k.split(":", 1)[1] if ":" in k else "") <= {"0"}:
                    continue
                found.append((k, vdb.name))
        return found

    def _cdm_extract(self, pssh_list: list[dict], license_url: str, kids: list[str], cdm_fn, cdm_kwargs: dict, drm_type: str) -> list[str]:
        """Call the CDM backend to extract keys for the given KID(s) from the PSSH(s)."""
        missing_pssh_list = [item for item in pssh_list if normalize_kid(item.get("kid", "")) in kids] or pssh_list  # safety: if filtering gives empty, use full list
        logger.info(f"{drm_type} CDM extraction for {len(kids)} missing KID(s): {kids}")
        cdm_result = cdm_fn(missing_pssh_list, license_url, **cdm_kwargs)
        if not cdm_result:
            return []

        # Keep only the KID(s) we actually asked the CDM to resolve for this PSSH.
        cdm_keys = cdm_result.get_keys_list()
        requested_kids = set(kids)
        filtered_cdm_keys = [k for k in cdm_keys if k.split(":", 1)[0].strip().lower() in requested_kids]
        dropped = len(cdm_keys) - len(filtered_cdm_keys)
        if dropped:
            logger.warning(f"{drm_type} CDM returned {dropped} extra key(s) not matching the requested KID(s) for this PSSH; discarding before store")
        return filtered_cdm_keys

    def report_wrong_keys(self, pairs: list[str], license_url: str | None, pssh: str | None = None) -> threading.Thread | None:
        """Fire-and-forget: tell each connected vault that these "kid:key" pairs are proven wrong. Returns the worker thread."""
        base_license_url = clean_license_url(license_url) if license_url else None
        if not pairs or not base_license_url:
            return None

        def _run():
            for pair in pairs:
                kid, _, key = pair.partition(":")
                for vdb in self._vaults:
                    try:
                        vdb.report_wrong_key(kid, key, base_license_url, pssh)
                    except Exception as e:
                        logger.debug(f"report_wrong_key failed for {kid} on {vdb.name} (non-fatal): {e}")

        thread = threading.Thread(target=_run, daemon=True, name="report-wrong-key")
        thread.start()
        return thread

    def replace_wrong_key(
        self,
        kid: str,
        old_key: str,
        new_key: str,
        source: str | None,
        license_url: str | None,
        pssh: str | None = None,
        wait_for: threading.Thread | None = None,
    ) -> threading.Thread:
        """Fire-and-forget: tell each connected vault to replace a wrong key with a new one. Returns the worker thread."""
        def _run():
            if wait_for is not None:
                wait_for.join(timeout=10)
            for vdb in self._vaults:
                if vdb.name == source or not vdb.is_writable:
                    continue
                try:
                    logger.info(f"Replacing wrong key for KID {kid} on {vdb.name} database")
                    if not vdb.replace_key(kid, new_key, old_key, license_url, pssh):
                        logger.warning(f"{vdb.name} did not take the corrected key for KID {kid}")
                except Exception as e:
                    logger.error(f"Failed to replace wrong key on {vdb.name} (will continue): {e}")

        thread = threading.Thread(target=_run, daemon=False, name="replace-wrong-key")
        thread.start()
        return thread

    def _store_keys(
        self,
        keys_list: list[str],
        drm_type: str = "manual",
        base_license_url: str = "generic",
        pssh_val: str = None,
        kid_to_label: dict | None = None,
        source: str = None,
    ) -> None:
        """Store keys in all connected vaults, skipping the one they were sourced from."""
        keys_list = KeysManager(keys_list).get_keys_list()
        if not keys_list:
            logger.warning(f"_store_keys: no valid {drm_type} keys to store after validation")
            return

        for vdb in self._vaults:
            name = vdb.name
            if name == source or not vdb.is_writable:
                continue  # avoid writing back to the vault we just read from, or to a read-only (universal) one

            # Idempotent: skip keys already present in this vault so re-resolving
            # the same key (e.g. preflight probe then post-download decrypt) does
            # not produce a duplicate store or a redundant "Storing …" log line.
            kids = [k.split(":", 1)[0] for k in keys_list]
            try:
                already = set(vdb.get_keys_by_kids(base_license_url, kids, pssh_val) or [])
            except Exception:
                already = set()
            to_store = [k for k in keys_list if k not in already]
            if not to_store:
                continue

            logger.info(f"Storing {len(to_store)} {drm_type} key(s) to {name} database")
            try:
                # local vault does not accept kid_to_label — call with base signature
                if name == "local":
                    vdb.set_keys(to_store, base_license_url, pssh_val)
                else:
                    vdb.set_keys(to_store, base_license_url, pssh_val, kid_to_label)
            except Exception as e:
                logger.error(f"Failed to sync to {name} (will continue): {e}")
                console.print(f"[yellow]Warning: Could not sync to {name}: {e}")

    @staticmethod
    def _merge_manual(manual_keys: list[str], other_keys: list[str]) -> list[str]:
        """Combine manually-provided keys with vault/CDM-resolved ones, manual keys winning on KID conflicts."""
        if not manual_keys:
            return other_keys

        manual_kids = {k.split(":")[0].strip().lower() for k in manual_keys}
        rest = [k for k in other_keys if k.split(":")[0].strip().lower() not in manual_kids]
        return manual_keys + rest

    def _resolve_keys(
        self,
        pssh_list: list[dict],
        license_url: str,
        drm_type: str,
        cdm_fn,
        cdm_kwargs: dict,
        key: str | list[str] = None,
    ) -> KeysManager | None:
        """
        Shared key resolution logic for both Widevine and PlayReady.
        Step 1: Manual key override. Step 2: vault lookup (by license_url or generic). Step 3: CDM extraction as fallback.
        """
        all_kids = [normalize_kid(item["kid"]) for item in pssh_list if item.get("kid") and item["kid"] != "N/A"]

        kid_to_label = {
            normalize_kid(item["kid"]): item["label"]
            for item in pssh_list
            if item.get("kid") and item["kid"] != "N/A" and item.get("label")
        } or None

        manual_keys: list[str] = []
        resolve_kids = all_kids

        if key:
            manual = KeysManager(key)
            manual_keys = manual.get_keys_list()
            manual_rejected = manual.get_rejected_list()

            if manual_keys or manual_rejected:
                missing = self._missing_kids(all_kids, manual_keys)
                m_base_license_url = clean_license_url(license_url) or "generic"
                m_pssh_val = next((i.get("pssh") for i in pssh_list if i.get("pssh")), None)

                if manual_keys:
                    self._store_keys(manual_keys, drm_type, m_base_license_url, m_pssh_val, kid_to_label, source=None)
                self._display_keys(
                    manual_keys,
                    [],
                    drm_type,
                    m_pssh_val,
                    None,
                    header=True,
                    default_label="manual",
                    required_kids=set(all_kids),
                    failed=manual_rejected,
                )

                if manual_keys and not missing:
                    return KeysManager(manual_keys)

                if not license_url:
                    logger.info(f"{len(missing)} manifest-declared {drm_type} KID(s) not covered by manual key(s) ({', '.join(missing)}); no license_url — falling back to generic vault lookup")
                else:
                    logger.info(f"{len(missing)} manifest-declared {drm_type} KID(s) not covered by manual key(s) ({', '.join(missing)}); resolving the rest via vault/CDM")
                resolve_kids = missing

        base_license_url = clean_license_url(license_url)

        pssh_val = next((i.get("pssh") for i in pssh_list if i.get("pssh")), None)

        bypass = self._bypass_cache()
        bypassed: dict[str, list[str]] = {}
        if bypass:
            logger.info(f"Vault cache bypassed by config/CLI; forcing fresh CDM extraction for {drm_type}")
            bypassed = self._announce_bypass(resolve_kids, base_license_url, pssh_val, drm_type)
            if not USE_CDM:
                msg = "bypass_vault_cache is enabled but use_cdm is disabled — no keys can be produced (vault reads skipped, CDM extraction off)."
                logger.warning(msg)
                console.print(f"[bold yellow]WARNING: {msg}[/bold yellow]")

        # Step 1: vault lookup with license_url
        vault_keys: list[str] = []
        vault_source = None

        if self._vaults and base_license_url and resolve_kids and not bypass:
            found_keys, vault_source, kid_flags = self._db_lookup(resolve_kids, base_license_url, drm_type, pssh_val)
            vault_keys = list(set(found_keys))

            if vault_keys:
                self._store_keys(vault_keys, drm_type, base_license_url, pssh_val, kid_to_label, source=vault_source)

            if set(resolve_kids).issubset({k.split(":")[0].strip().lower() for k in vault_keys}):
                logger.info(f"{drm_type} keys found in vault(s): {len(vault_keys)} key(s)")
                self._display_keys(
                    vault_keys,
                    vault_keys,
                    drm_type,
                    pssh_val,
                    vault_source,
                    header=not manual_keys,
                    required_kids=set(all_kids),
                    kid_flags=kid_flags,
                )
                return KeysManager(self._merge_manual(manual_keys, vault_keys))

        # Step 2: If no license_url but DRM detected → try generic lookup in database
        if not license_url and resolve_kids and self._vaults and not bypass:
            logger.warning(f"DRM detected but missing license_url. Searching database for {len(resolve_kids)} {drm_type} KID(s) using 'generic' lookup")
            found_keys, vault_source, kid_flags = self._db_lookup(resolve_kids, "generic", drm_type, pssh_val)
            vault_keys = list(set(found_keys))

            if vault_keys and set(resolve_kids).issubset({k.split(":")[0].strip().lower() for k in vault_keys}):
                logger.info(f"{drm_type} keys found in vault(s) via generic lookup: {len(vault_keys)} key(s)")
                self._display_keys(
                    vault_keys,
                    vault_keys,
                    drm_type,
                    pssh_val,
                    vault_source,
                    header=not manual_keys,
                    required_kids=set(all_kids),
                    kid_flags=kid_flags,
                )
                return KeysManager(self._merge_manual(manual_keys, vault_keys))

            elif vault_keys:
                logger.warning(f"Found {len(vault_keys)} {drm_type} key(s) but not all KIDs covered. Partial match: {vault_keys}")

        # Step 3: CDM extraction — only for KIDs not already covered by vault
        if USE_CDM:
            try:
                vault_covered = {k.split(":")[0].strip().lower() for k in vault_keys}
                missing_kids = [kid for kid in resolve_kids if kid not in vault_covered]

                cdm_keys: list[str] = []
                if missing_kids:
                    cdm_kwargs = {**cdm_kwargs, "key": None} if manual_keys else cdm_kwargs
                    cdm_keys = self._cdm_extract(pssh_list, license_url, missing_kids, cdm_fn, cdm_kwargs, drm_type)

                if cdm_keys:
                    # Merge: vault keys + CDM keys
                    all_keys = list({k.split(":")[0]: k for k in vault_keys + cdm_keys}.values())
                    logger.info(f"{drm_type} CDM extraction successful: {len(cdm_keys)} new key(s), {len(all_keys)} total")
                    self._store_keys(all_keys, drm_type, base_license_url, pssh_val, kid_to_label, source=None)
                    self._display_keys(
                        all_keys,
                        vault_keys,
                        drm_type,
                        pssh_val,
                        vault_source,
                        header=False,
                        default_label="cdm",
                        required_kids=set(all_kids),
                    )

                    for vault_name, vault_bypass_keys in bypassed.items():
                        self._display_keys(
                            vault_bypass_keys,
                            vault_bypass_keys,
                            drm_type,
                            pssh_val,
                            vault_name,
                            header=False,
                            required_kids=set(all_kids),
                            tag_suffix=BYPASS_TAG_SUFFIX,
                        )
                    
                    return KeysManager(self._merge_manual(manual_keys, all_keys))

                elif vault_keys:
                    logger.warning(f"{drm_type} CDM returned no new keys; returning {len(vault_keys)} vault key(s)")
                    self._display_keys(
                        vault_keys,
                        vault_keys,
                        drm_type,
                        pssh_val,
                        vault_source,
                        header=False,
                        required_kids=set(all_kids),
                    )
                    return KeysManager(self._merge_manual(manual_keys, vault_keys))

                elif manual_keys:
                    logger.warning(f"{drm_type} CDM extraction returned no keys; returning manual key(s) only ({len(manual_keys)})")
                    return KeysManager(manual_keys)

                logger.error(f"{drm_type} CDM extraction returned no keys")
                console.print("[yellow]CDM extraction returned no keys")

            except Exception as e:
                logger.error(f"{drm_type} CDM error: {e}")
                console.print(f"[red]CDM error: {e}")

            if manual_keys:
                logger.warning(f"All automatic {drm_type} extraction methods failed; returning manual key(s) only ({len(manual_keys)})")
                return KeysManager(manual_keys)

            logger.error(f"All {drm_type} extraction methods failed")
            console.print(f"\n[red]All extraction methods failed for {drm_type}")
            console.print(f"[yellow]Please place CDM files (.wvd for Widevine in wvd, .prd for PlayReady in prd) in: {binary_paths.get_cdm_directory()}[/yellow]")
        else:
            if manual_keys:
                return KeysManager(manual_keys)
            console.print("[yellow]CDM extraction disabled by config.")

    def resolve_flat_key(
        self, kid: str, pssh: str | None, manual_key: Any, drm_type: str = "mp4"
    ) -> tuple[str, str] | None:
        """Vault-backed key resolution for flat/no-license-URL streams"""
        kid_norm = normalize_kid(kid)

        if manual_key:
            norm = KeysManager.resolve_placeholder_kid(kid_norm, KeysManager.normalize(manual_key))
            keys_list = KeysManager(norm).get_keys_list()
            if keys_list:
                self._store_keys(keys_list, drm_type, "generic", pssh, source=None)
                matching = next((k for k in keys_list if k.split(":", 1)[0] == kid_norm), keys_list[0])
                return matching, "manual"

        if not self._vaults:
            return None
        
        if self._bypass_cache():
            logger.info(f"Vault cache bypassed by config/CLI; skipping generic vault lookup for {drm_type} KID {kid_norm}")
            return None
        
        found_keys, source, _kid_flags = self._db_lookup([kid_norm], "generic", drm_type, pssh)
        return (found_keys[0], source) if found_keys else None

    def get_wv_keys(
        self,
        pssh_list: list[dict],
        license_url: str,
        license_data: dict = None,
        license_certificate: str = None,
        headers: dict = None,
        key: str = None,
        license_request_fn=None,
    ):
        """Get Widevine keys."""
        cdm_fn, cdm_kwargs = self.cdm_spec("widevine", headers=headers, key=key, license_data=license_data, license_certificate=license_certificate, license_request_fn=license_request_fn)
        return self._resolve_keys(pssh_list, license_url, "widevine", cdm_fn=cdm_fn, cdm_kwargs=cdm_kwargs, key=key)

    def cdm_spec(
        self,
        drm_type: str,
        headers: dict = None,
        key: str = None,
        license_data: dict = None,
        license_certificate: str = None,
        license_request_fn=None,
    ):
        """(cdm_fn, cdm_kwargs) for *drm_type*; FairPlay has no CDM backend, so its cdm_fn returns nothing."""
        if drm_type == "widevine":
            return get_widevine_keys, dict(
                cdm_device_path=self.widevine_device_path,
                cdm_remote_api=self.widevine_remote_cdm_api,
                headers=headers,
                key=key,
                license_data=license_data,
                license_certificate=license_certificate,
                prefer_remote_cdm=self.prefer_remote_cdm if self.prefer_remote_widevine is None else self.prefer_remote_widevine,
                license_request_fn=license_request_fn,
            )
        if drm_type == "playready":
            return get_playready_keys, dict(
                cdm_device_path=self.playready_device_path,
                cdm_remote_api=self.playready_remote_cdm_api,
                headers=headers,
                key=key,
                license_data=license_data,
                prefer_remote_cdm=self.prefer_remote_cdm if self.prefer_remote_playready is None else self.prefer_remote_playready,
                license_request_fn=license_request_fn,
            )
        return (lambda *args, **kwargs: None), {}

    def get_fp_keys(
        self,
        pssh_list: list[dict],
        license_url: str,
        key: str = None,
    ):
        """Get FairPlay keys — vault lookup only, no CDM (there is no FairPlay CDM backend)."""
        cdm_fn, cdm_kwargs = self.cdm_spec("fairplay")
        return self._resolve_keys(pssh_list, license_url, "fairplay", cdm_fn=cdm_fn, cdm_kwargs=cdm_kwargs, key=key)

    def get_pr_keys(
        self,
        pssh_list: list[dict],
        license_url: str,
        headers: dict = None,
        key: str = None,
        license_data: dict = None,
        license_request_fn=None,
    ):
        """Get PlayReady keys."""
        cdm_fn, cdm_kwargs = self.cdm_spec("playready", headers=headers, key=key, license_data=license_data, license_request_fn=license_request_fn)
        return self._resolve_keys(pssh_list, license_url, "playready", cdm_fn=cdm_fn, cdm_kwargs=cdm_kwargs, key=key)

    def add_keys(
        self, keys: list[str], license_url: str, pssh: str = None, kid_to_label: dict | None = None
    ) -> dict[str, int]:
        """Manually push one or more keys to all connected vaults."""
        if not keys:
            logger.warning("add_keys called with empty keys list — nothing to store.")
            return {}

        # Validate format
        valid_keys = []
        for entry in keys:
            normalized = KeysManager(entry).get_keys_list()
            if not normalized:
                logger.warning(f"Skipping malformed/invalid key entry (expected 32-hex 'kid:key'): {entry!r}")
                console.print(f"[yellow]Skipping malformed key: {entry!r}")
                continue
            valid_keys.extend(normalized)

        if not valid_keys:
            console.print("[red]No valid keys to store.")
            return {}

        base_license_url = clean_license_url(license_url) if license_url else "generic"
        results: dict[str, int] = {}

        for vdb in self._vaults:
            name = vdb.name
            if not vdb.is_writable:
                continue
            
            logger.info(f"[add_keys] Storing {len(valid_keys)} key(s) to {name}")
            try:
                added = vdb.set_keys(valid_keys, base_license_url, pssh, kid_to_label)
                results[name] = added
            except Exception as e:
                logger.error(f"[add_keys] Failed to store to {name}: {e}")
                console.print(f"[red]x {name}: {e}")
                results[name] = 0

        return results
