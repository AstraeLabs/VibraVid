# 14.10.26

import logging
import threading
from collections.abc import Callable, Iterable, Iterator
from typing import Any

from VibraVid.core.decryptor import KeysManager
from VibraVid.core.ui.bar_manager import console
from VibraVid.utils.vault._url_utils import clean_license_url

from . import manager as _manager
from .system import normalize_kid

logger = logging.getLogger(__name__)
TryKeys = Callable[[list[str]], tuple[bool, str | None]]


class KeyFallback:
    """Recover from a key that flux's key-sanity check proved wrong: try every vault's key for the KID, then the CDM."""
    def __init__(
        self,
        drm_manager,
        drm_type: str,
        pssh_list: list[dict] | None = None,
        *,
        license_url: str | None = None,
        license_headers: dict | None = None,
        license_data: dict | None = None,
        license_certificate: str | None = None,
        license_request_fn=None,
    ) -> None:
        self.drm_manager = drm_manager
        self.drm_type = drm_type
        self.pssh_list = list(pssh_list or [])
        self.license_url = license_url
        self.license_request_fn = license_request_fn
        self.base_license_url = clean_license_url(license_url) if license_url else "generic"
        self.pssh = next((i.get("pssh") for i in self.pssh_list if i.get("pssh")), None)
        self.cdm_fn, self.cdm_kwargs = drm_manager.cdm_spec(
            drm_type,
            headers=license_headers,
            license_data=license_data,
            license_certificate=license_certificate,
            license_request_fn=license_request_fn,
        )

        self._resolved: dict[str, str] = {}         # kid -> key proven good
        self._tried: dict[str, set[str]] = {}       # kid -> "kid:key" pairs already tested
        self._exhausted: set[str] = set()
        self._kid_locks: dict[str, threading.Lock] = {}
        self._guard = threading.Lock()

    @classmethod
    def for_flat(cls, kid: str, pssh: str | None, drm_type: str = "mp4", drm_manager=None) -> "KeyFallback":
        """Vault-only fallback for flat/no-license-URL streams (direct MP4): same "generic" lookup as resolve_flat_key."""
        return cls(drm_manager or _manager.DRMManager(), drm_type, [{"kid": kid, "pssh": pssh}])

    @staticmethod
    def _norm_pair(pair: str) -> str:
        kid, _, key = pair.partition(":")
        return f"{normalize_kid(kid)}:{normalize_kid(key)}"

    def _kid_lock(self, kid: str) -> threading.Lock:
        with self._guard:
            return self._kid_locks.setdefault(kid, threading.Lock())

    def is_cdm_usable(self) -> bool:
        """CDM fallback needs the CDM enabled, a real DRM type and a way to request a license."""
        return bool(
            _manager.USE_CDM
            and self.drm_type in ("widevine", "playready")
            and self.pssh_list
            and (self.license_url or self.license_request_fn)
        )

    def apply(self, keys: Any) -> Any:
        """*keys* with every KID already recovered swapped to its good key (unchanged when nothing applies)."""
        with self._guard:
            resolved = dict(self._resolved)
        if not resolved:
            return keys

        pairs = KeysManager.normalize(keys)
        if not any(k in resolved for k, _ in pairs):
            return keys
        return [f"{k}:{resolved.get(k, v)}" for k, v in pairs]

    def _candidates(self, kid: str, tried: set[str]) -> Iterator[tuple[str, str]]:
        """Lazily yield ("kid:key", source): every vault first, the CDM only once they are used up."""
        for pair, source in self.drm_manager._db_lookup_all([kid], self.base_license_url, self.pssh):
            pair = self._norm_pair(pair)
            if pair not in tried:
                yield pair, source

        if not self.is_cdm_usable():
            logger.info(f"Key fallback for KID {kid}: CDM not usable here (disabled, no license or no PSSH) -- vaults only")
            return

        try:
            cdm_keys = self.drm_manager._cdm_extract(self.pssh_list, self.license_url, [kid], self.cdm_fn, self.cdm_kwargs, self.drm_type)
        except Exception as e:
            logger.warning(f"Key fallback for KID {kid}: CDM extraction failed: {e}")
            return

        for pair in cdm_keys:
            pair = self._norm_pair(pair)
            if pair not in tried:
                yield pair, "cdm"

    def _try(self, kid: str, key: str, current: dict[str, str], try_keys: TryKeys) -> tuple[bool, str | None]:
        trial = [f"{k}:{v}" for k, v in {**current, kid: key}.items()]
        try:
            return try_keys(trial)
        except Exception as e:
            return False, str(e)

    def recover(self, kids: Iterable[str], keys: Any, try_keys: TryKeys) -> bool:
        """Try every vault's key for each KID, then the CDM, until one works (return True) or all fail (return False)."""
        current = dict(KeysManager.normalize(keys))
        kid_list = sorted({normalize_kid(k) for k in kids if k})
        if not kid_list:
            if len(current) != 1:
                logger.warning("Key fallback skipped: the failing track's KID is unknown and several keys are in play")
                return False
            kid_list = list(current)

        for kid in kid_list:
            with self._kid_lock(kid):
                if kid in self._exhausted:
                    continue

                good = self._resolved.get(kid)
                if good is not None:
                    if good != current.get(kid) and self._try(kid, good, current, try_keys)[0]:
                        return True
                    continue

                tried = self._tried.setdefault(kid, set())
                bad = current.get(kid)
                if bad:
                    bad_pair = f"{kid}:{bad}"
                    if bad_pair not in tried:
                        tried.add(bad_pair)
                        self.drm_manager.report_wrong_keys([bad_pair], self.license_url, self.pssh)

                logger.warning(f"Key fallback: key for KID {kid} proven wrong -- trying the other vaults' keys, then the CDM")
                console.print(f"[yellow]Wrong key for KID [red]{kid}[/red] -- trying fallback keys (vaults, then CDM)")

                for pair, source in self._candidates(kid, tried):
                    tried.add(pair)
                    key = pair.split(":", 1)[1]
                    ok, message = self._try(kid, key, current, try_keys)
                    if ok:
                        with self._guard:
                            self._resolved[kid] = key
                        logger.info(f"Key fallback: KID {kid} recovered with the key from {source}")
                        console.print(f"[green]Fallback key OK[/green] for KID [red]{kid}[/red] [cyan]| [#a855f7]{self.drm_manager.vault_label(source)}")
                        self.drm_manager._store_keys([pair], self.drm_type, self.base_license_url, self.pssh, source=None if source == "cdm" else source)
                        return True

                    logger.warning(f"Key fallback: key for KID {kid} from {source} rejected: {message}")
                    if message and "key is wrong" in message:
                        self.drm_manager.report_wrong_keys([pair], self.license_url, self.pssh)

                self._exhausted.add(kid)
                logger.error(f"Key fallback: no working key for KID {kid} in any vault or the CDM")
                console.print(f"[red]No working fallback key for KID {kid}")

        return False
