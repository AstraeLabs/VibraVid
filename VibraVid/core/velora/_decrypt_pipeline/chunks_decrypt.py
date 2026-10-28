# 01.04.25

import re
import time
from pathlib import Path

from VibraVid.core.decryptor import Decryptor
from VibraVid.core.decryptor.decryptor import KeysManager, flux_supports_tfdt_rebase

from .constants import logger
from .sniff import _reads_as_self_initializing_mp4, _skip_post_decrypt


class ChunksDecryptMixin:
    """Chunk-based decryption via ``flux --chunks``."""
    def _resolve_norm_keys(
        self,
        first_chunk: str,
        key,
        key_fallback=None,
    ):
        """Resolve and normalize decryption keys for the first chunk."""
        encrypted, kid, _pssh, scheme = Decryptor().detect_encryption(first_chunk)
        norm_keys = KeysManager.normalize(key)
        if not encrypted or not norm_keys:
            logger.info(
                f"seg_00000.mp4 did not surface CENC encryption "
                f"(encrypted={encrypted}, kid={(kid or '-')[:16]}...); skipping chunks path"
            )
            return None

        norm_keys = KeysManager.resolve_fixed_key(first_chunk, kid, norm_keys)
        if not norm_keys:
            logger.info(f"no usable key after resolve_fixed_key for KID {(kid or '-')[:16]}...")
            return None

        norm_keys = KeysManager.unique_by_kid(KeysManager.resolve_placeholder_kid(kid, norm_keys))
        if key_fallback is not None:
            norm_keys = KeysManager.normalize(key_fallback.apply([f"{k}:{v}" for k, v in norm_keys]))

        if kid and not KeysManager.is_zero_kid(kid):
            available_kids = {k.lower() for k, _ in norm_keys}
            if kid.lower() not in available_kids:
                logger.error(f"No key matches required KID {kid}; refusing chunks decrypt")
                return None

        return norm_keys

    def _chunks_decrypt_applicable(self, ctx, paths: list[Path], out_path: Path, live_merge_ok: bool) -> bool:
        """Whether this track is one encrypted fMP4 file split in ``seg_<n>.mp4``
        chunks that flux can merge + decrypt in a single pass."""
        stream = ctx.stream
        drm = getattr(stream, "drm", None)
        if (
            live_merge_ok
            or ctx.live_decryption
            or ctx.protocol_lower != "dash"
            or not self.key
            or _skip_post_decrypt()
            or getattr(stream, "type", "") == "subtitle"
            or drm is None
            or not (drm.method is not None or drm.is_encrypted())
            or out_path.suffix.lower() not in (".mp4", ".m4a")
            or len(paths) < 1
        ):
            return False

        stream_dir = ctx.stream_dir

        def _reject(reason: str) -> bool:
            logger.info(f"flux --chunks skipped for {out_path.name}: {reason}")
            return False

        try:
            # Accept any consecutive seg_*.mp4 set present in the directory,
            # not just seg_00000..seg_N with strict numbering.
            names = {p.name for p in paths}
            expected_pattern = re.compile(r"^seg_\d+\.mp4$")
            if not all(expected_pattern.match(n) for n in names):
                return _reject(f"downloaded seg names do not match seg_<n>.mp4 pattern (first: {sorted(names)[:3]})")

            if (bad := next((p for p in paths if p.parent != stream_dir), None)) is not None:
                return _reject(f"{bad.name} lives in {bad.parent} instead of {stream_dir}")

            if (empty := next((p for p in paths if not p.is_file() or p.stat().st_size <= 0), None)) is not None:
                return _reject(f"{empty.name} is missing or empty")

            # flux reads every seg_<n>.mp4 of the directory: anything else
            # (stale resume files) means we are not sure what it would take.
            on_dir = {entry.name for entry in stream_dir.iterdir()} - names
            if on_dir:
                return _reject(f"stream_dir holds extra file(s): {sorted(on_dir)[:3]}")

            if missing := sorted({f"seg_{n:05d}.mp4" for n in range(len(paths))} - names):
                return _reject(f"stream_dir is missing chunk(s): {missing[:3]}")
        except OSError as exc:
            return _reject(f"filesystem error: {exc}")

        # Only the first chunk carries ftyp+moov (8 MiB slices of one file);
        # per-segment self-initializing MP4s (HLS BYTERANGE) go the old way.
        # Decide positions by name, never by list position.
        by_name = {p.name: p for p in paths}
        self_init = sorted(name for name, p in by_name.items() if _reads_as_self_initializing_mp4(p))
        if self_init[:1] != ["seg_00000.mp4"]:
            return _reject(f"no self-initializing chunk at seg_00000.mp4 (ftyp-bearing chunks: {self_init[:3]})")
        if len(self_init) > 1:
            return _reject(f"{self_init[1]} is itself self-initializing (per-segment MP4s, not one sliced file)")
        return True

    def _try_chunks_decrypt(self, ctx, paths: list[Path], out_path: Path, live_merge_ok: bool) -> bool:
        """Merge + decrypt the chunk directory in one ``flux --chunks`` pass.

        Returns ``True`` when the final output was produced; ``False`` means
        nothing was consumed and the regular merge + decrypt path must run.
        """
        if not self._chunks_decrypt_applicable(ctx, paths, out_path, live_merge_ok):
            return False

        stream = ctx.stream

        # The chunks pipe muxes straight into the final MKV, so only the video track takes it;
        # audio tracks (and every pipe failure) go to the file mode below.
        if getattr(stream, "type", "") == "video" and self._chunks_pipe_eligible():
            try:
                if self._try_chunks_pipe_mux(ctx, paths, out_path):
                    return True
            except Exception:
                logger.warning("chunks-pipe: unexpected error; falling back to file mode", exc_info=True)
                self._discard_partial_pipe_output()

        dec_path = out_path.with_suffix(out_path.suffix + ".dec")
        dec_path.unlink(missing_ok=True)

        logger.info(f"Merge + decrypt in one pass (flux --chunks) -> {out_path.name} ({len(paths)} segs)")

        decryptor = Decryptor()
        rebase_in_flux = flux_supports_tfdt_rebase(decryptor.flux_path)
        t0 = time.monotonic()
        try:
            ok = decryptor.decrypt_chunks(
                str(ctx.stream_dir),
                self.key,
                str(dec_path),
                stream_type=stream.type,
                progress_cb=ctx.bar_manager.decrypt_progress_cb(ctx.task_key),
                key_fallback=self.key_fallback,
                tfdt_rebase=rebase_in_flux,
            )
        except Exception as exc:
            logger.warning(f"flux --chunks error for {out_path.name}: {exc}")
            ok = False

        if not ok:
            if not self._keep_tmp_on_error():
                dec_path.unlink(missing_ok=True)
            return False

        try:
            out_path.unlink(missing_ok=True)
            dec_path.rename(out_path)
        except OSError as exc:
            logger.error(f"rename failed after flux --chunks: {exc}")
            dec_path.unlink(missing_ok=True)
            return False

        logger.info(f"Merge + decrypt finished -> {out_path.name} in {time.monotonic() - t0:.1f}s")

        # The output is a clear *fragmented* MP4. flux >= 0.5.4 already zeroed its tfdt; with an older
        # flux it keeps the original absolute tfdt, exactly like a live-decrypted track.
        if not rebase_in_flux:
            from VibraVid.utils import config_manager

            from ..util._tfdt_rebase import rebase_fragment_timestamps

            if not rebase_fragment_timestamps(out_path, logger=logger):
                if config_manager.config.get("PROCESS", "engine", default="ffmpeg").lower() == "ffmpeg":
                    self._needs_join_ts_fix = True
                else:
                    self._normalize_out_path(out_path, False)
        return True

    def _keep_tmp_on_error(self) -> bool:
        from VibraVid.utils import config_manager

        return config_manager.config.get_bool("DOWNLOAD", "keep_tmp_on_error", default=False)
