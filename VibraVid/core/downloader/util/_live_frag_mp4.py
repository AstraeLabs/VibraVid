# 17.08.26

import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any, BinaryIO

from VibraVid.core.decryptor import Decryptor, KeysManager
from VibraVid.core.velora.util._cenc_init import strip_cenc_signaling

from ._frag_box_splitter import FragBoxSplitter

logger = logging.getLogger(__name__)
_BATCH_TARGET_BYTES = 4 * 1024 * 1024


class LiveFragMp4Decryptor:
    def __init__(
        self, out_fh: BinaryIO, key: Any, tmp_dir: Path, on_chunk: Callable[[bytes], None] | None = None
    ) -> None:
        self._out_fh = out_fh
        self._key = key
        self._tmp_dir = tmp_dir
        self._on_chunk = on_chunk
        self._splitter = FragBoxSplitter()
        self._pending: list[bytes] = []
        self._raw_accum = bytearray()
        self._decided: str | None = None  # None | "decrypt" | "abandon"
        self._decryptor: Decryptor | None = None
        self._resolved_key: Any = None
        self._protected_init_path: Path | None = None
        self._frag_raw_path = tmp_dir / "live_frag_raw.bin"
        self._frag_dec_path = tmp_dir / "live_frag_dec.bin"
        self._batch_buf = bytearray()
        self.failed_reason: str | None = None
        self._key_sanity_pending = True

    def _write(self, data: bytes) -> None:
        self._out_fh.write(data)
        if self._on_chunk is not None and data:
            try:
                self._on_chunk(data)
            except Exception:
                logger.debug("on_chunk callback failed (non-fatal)", exc_info=True)

    @property
    def abandoned(self) -> bool:
        return self._decided == "abandon"

    @property
    def active(self) -> bool:
        return self._decided == "decrypt"

    def feed(self, chunk: bytes) -> bool:
        """Returns False once/if this stream turned out ineligible"""
        if self._decided == "abandon":
            return False

        self._raw_accum += chunk
        self._pending.extend(self._splitter.feed(chunk))

        if self._decided is None and self._splitter.eligible is False:
            self._decided = "abandon"
            return False

        if self._decided is None and self._splitter.eligible is True:
            self._decide()
            if self._decided == "abandon":
                return False
            self._raw_accum = bytearray()  # "decrypt" committed -- no longer needed

        if self._decided == "decrypt":
            self._flush_pending()

        return True

    def _decide(self) -> None:
        """Called once the splitter has enough data to determine eligibility"""
        init_bytes = self._splitter.init_bytes or b""
        protected_init_path = self._tmp_dir / "live_init_protected.mp4"
        try:
            protected_init_path.write_bytes(init_bytes)
            decryptor = Decryptor()
            encrypted, kid, pssh, scheme = decryptor.detect_encryption(str(protected_init_path))
        except Exception as exc:
            self.failed_reason = f"init probe failed: {exc}"
            self._decided = "abandon"
            return

        if not encrypted:
            # Not encrypted -- MP4 direct download has no separate merge pass
            # to skip by going live, so there's nothing to gain here.
            self._decided = "abandon"
            return

        resolved_key = None
        if kid and not KeysManager.is_zero_kid(kid):
            from VibraVid.core.drm.manager import DRMManager

            resolved = DRMManager().resolve_flat_key(kid, pssh, self._key, drm_type=scheme or "mp4")
            if resolved:
                resolved_key = resolved[0]

        if resolved_key is None and self._key:
            resolved_key = self._key

        if not resolved_key:
            self.failed_reason = f"no usable key for KID {kid}"
            self._decided = "abandon"
            return

        try:
            self._write(strip_cenc_signaling(init_bytes))
        except Exception as exc:
            self.failed_reason = f"init write failed: {exc}"
            self._decided = "abandon"
            return

        self._protected_init_path = protected_init_path
        self._decryptor = decryptor
        self._resolved_key = resolved_key
        self._decided = "decrypt"
        logger.debug(f"Live fMP4 decrypt engaged (kid={kid}, scheme={scheme})")

    def _flush_pending(self) -> None:
        if not self._pending:
            return
        frags, self._pending = self._pending, []
        for frag in frags:
            self._batch_buf += frag
            if len(self._batch_buf) >= _BATCH_TARGET_BYTES:
                self._flush_batch()

    def _flush_batch(self) -> None:
        if not self._batch_buf:
            return
        batch, self._batch_buf = bytes(self._batch_buf), bytearray()
        self._decrypt_and_write(batch)

    def _decrypt_and_write(self, raw: bytes) -> None:
        assert self._decryptor is not None
        self._frag_raw_path.write_bytes(raw)
        run_key_sanity = self._key_sanity_pending
        self._key_sanity_pending = False

        ok, message, _data = self._decryptor.decrypt_segment_live(
            encrypted_path=str(self._frag_raw_path),
            decrypted_path=str(self._frag_dec_path),
            raw_keys=self._resolved_key,
            init_path=str(self._protected_init_path),
            key_sanity=run_key_sanity,
        )

        if not ok or not self._frag_dec_path.exists():
            raise RuntimeError(f"live fragment decrypt failed: {message}")

        self._write(self._frag_dec_path.read_bytes())

    def finish(self) -> bytes:
        """Call once after the last `feed()`. Returns raw bytes the caller still has to write itself: the un-decided/abandoned leftover, or (on a successful live-decrypt run) empty """
        if self._decided != "decrypt":
            return bytes(self._raw_accum)

        self._flush_batch()

        trailing = self._splitter.finish()
        if trailing:
            self._write(trailing)
        return b""

    def cleanup(self, remove_init: bool = True) -> None:
        """Remove scratch files and shut down the flux daemon this session started (if any)"""
        if self._decryptor is not None:
            self._decryptor.close_flux_daemon()

        paths = [self._frag_raw_path, self._frag_dec_path]
        if remove_init:
            # Constructed here (not just `self._protected_init_path`, which
            # stays None on the "not encrypted"/"no key" abandon outcomes)
            # since `_decide()` writes this file before either outcome is known.
            paths.append(self._tmp_dir / "live_init_protected.mp4")
        
        for p in paths:
            try:
                if p.exists():
                    p.unlink()
            except Exception:
                pass
