# 22.02.25

import logging
import os
from collections.abc import Callable
from typing import Any

from VibraVid.core.decryptor import Decryptor, KeysManager
from VibraVid.core.decryptor._models import detect_encryption_info
from VibraVid.core.ui.bar_manager import console
from VibraVid.core.ui.tracker import download_tracker

logger = logging.getLogger(__name__)


class PostDownloadDecryptor:
    @staticmethod
    def has_keys(key: Any) -> bool:
        """Return ``True`` when *key* contains at least one usable pair."""
        if key is None:
            return False
        if isinstance(key, KeysManager):
            return bool(key.get_keys_list())
        if isinstance(key, str):
            return bool(key.strip())
        if isinstance(key, (list, tuple)):
            return bool(key)
        return False

    def run(
        self,
        path: str,
        key: Any,
        download_id: str | None = None,
        progress_cb: Callable[[dict[str, Any]], None] | None = None,
    ) -> bool:
        """Decrypt *path* in-place.  Logs and prints errors but never raises."""
        logger.info(f"PostDownloadDecryptor: probing {os.path.basename(path)} ...")
        if download_id:
            download_tracker.update_status(download_id, "Decrypting ...")

        dec_path = path + ".dec"
        try:
            decryptor = Decryptor()
            detected_info = detect_encryption_info(path)
            encrypted, kid, pssh, scheme = (
                detected_info.encrypted,
                detected_info.kid,
                detected_info.pssh_b64,
                detected_info.scheme,
            )

            if not encrypted:
                logger.info("PostDownloadDecryptor: file is not encrypted — skipping.")
                console.print("[dim]Keys provided but file is not encrypted — skipping decryption.")
                return True

            logger.info(f"PostDownloadDecryptor: encryption found (scheme={scheme}, kid={kid}) — starting decryption.")

            if kid:
                if not KeysManager.is_zero_kid(kid):
                    from VibraVid.core.drm.manager import DRMManager

                    resolved = DRMManager().resolve_flat_key(kid, pssh, key, drm_type=scheme or "mp4")
                    if resolved:
                        key = resolved[0]
                self._warn_if_kid_missing(kid, key)

            ok = decryptor.decrypt(
                encrypted_path=path,
                keys=key,
                output_path=dec_path,
                stream_type="video",
                progress_cb=progress_cb,
                detected=detected_info,
            )

            if ok and os.path.exists(dec_path) and os.path.getsize(dec_path) > 0:
                try:
                    os.replace(dec_path, path)
                    logger.info(f"PostDownloadDecryptor: success -> {os.path.basename(path)}")
                    return True
                except Exception as exc:
                    logger.error(f"PostDownloadDecryptor: rename failed — {exc}")
                    console.print(f"[red]Decryption rename failed: {exc}")
                    self._remove(dec_path)
                    return False

            else:
                logger.error(f"PostDownloadDecryptor: decryption failed for {os.path.basename(path)}")
                console.print(f"[red]Decryption failed for {os.path.basename(path)}")
                self._remove(dec_path)
                return False

        except Exception as exc:
            logger.error(f"PostDownloadDecryptor: unexpected error — {exc}")
            console.print(f"[red]Decryption error: {exc}")
            self._remove(dec_path)
            return False

    @staticmethod
    def _remove(path: str) -> None:
        """Remove *path* if it exists, ignoring errors."""
        try:
            if os.path.exists(path):
                os.remove(path)
        except Exception:
            pass

    @staticmethod
    def _warn_if_kid_missing(kid: str, key: Any) -> None:
        """Emit a warning when the probed KID is not among the provided keys."""
        kid_norm = kid.replace("-", "").lower()
        found = False

        if isinstance(key, KeysManager):
            pairs = key.get_keys_list() or []
            found = len(pairs) == 1 and pairs[0].split(":")[0] == "1"
            if not found:
                found = any(k.kid.replace("-", "").lower() == kid_norm for k in pairs if hasattr(k, "kid"))

        elif isinstance(key, str):
            # Accepts "kid:key" or "kid:key|kid:key|..." formats
            pairs = [pair for pair in key.strip().split("|") if ":" in pair]
            found = len(pairs) == 1 and pairs[0].split(":")[0] == "1"
            if not found:
                found = any(pair.split(":")[0].replace("-", "").lower() == kid_norm for pair in pairs)

        elif isinstance(key, (list, tuple)):
            raws = [pair if isinstance(pair, str) else str(pair) for pair in key]
            found = len(raws) == 1 and raws[0].split(":")[0] == "1"
            if not found:
                found = any(raw.split(":")[0].replace("-", "").lower() == kid_norm for raw in raws)

        if not found:
            logger.warning(f"PostDownloadDecryptor: KID [{kid}] from probe not found among provided keys — decryption may fail.")
            console.print(f"[yellow]Warning:[/yellow] KID [cyan]{kid}[/cyan] missing — decryption may fail.")
