# 01.04.25

import logging
import os
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..util._stream_helpers import describe_key_for_log
from .constants import logger
from .sniff import _reads_as_self_initializing_mp4

if TYPE_CHECKING:
    from VibraVid.core.decryptor import Decryptor

    from .context import _SegmentDownloadContext


class DashDecryptMixin:
    def _decrypt_dash_segment(
        self,
        ctx: "_SegmentDownloadContext",
        fp: Path,
        seg: dict[str, Any],
        dash_decryptor: "Decryptor",
        init_path: Path | None,
    ) -> None:
        """Shared by DASH and HLS (EXT-X-MAP CENC/SAMPLE-AES) live decrypt --
        both share a bare moof+mdat fragment + separate real init segment
        model, so one function covers both. init_path=None (HLS with no
        EXT-X-MAP, every segment self-initializing) makes
        decrypt_segment_live fall through to flux's normal (non
        --fragments-info) decrypt, which reads the segment's own moov."""
        if seg.get("seg_type") == "init":
            logger.info(f"{ctx.protocol.upper()} init segment ready -> {fp.name}")
            return

        self._repair_media_segment(ctx, fp, seg)

        dec_tmp = fp.with_suffix(fp.suffix + ".dec")
        init_path_str = str(init_path) if init_path and init_path.exists() else None
        merger = ctx.live_merger
        adopt_whole_file = (
            merger is not None
            and len(ctx.media_segs_only) == 1
            and not (seg.get("headers") or {}).get("Range")
            and getattr(self, "_streaming_mux_output_path", None) is None
            and merger.can_adopt()
            and _reads_as_self_initializing_mp4(fp)
        )
        if adopt_whole_file:
            init_path_str = None

        if logger.isEnabledFor(logging.DEBUG):
            logger.debug(f"CENC LIVE decrypt path={fp} init={init_path_str or 'None'} with key={describe_key_for_log(self.key)}")

        want_key_sanity = self._want_key_sanity(ctx, fp, dash_decryptor)
        ok, message, _data = dash_decryptor.decrypt_segment_live(
            encrypted_path=str(fp),
            decrypted_path=str(dec_tmp),
            raw_keys=self.key,
            init_path=init_path_str,
            key_sanity=want_key_sanity,
        )
        if want_key_sanity:
            ctx.key_sanity_checked = True

        wrong_key = not ok and message and "key is wrong" in message
        if not ok and not wrong_key:
            # The bytes on disk decrypt-fail even though the download itself
            # reported success (e.g. a CDN response cut short mid-transfer
            # without the HTTP layer noticing) -- re-fetch this one segment
            # fresh and retry exactly once before giving up on it.
            logger.warning(f"{ctx.protocol.upper()} live decrypt failed for {fp.name}, re-downloading and retrying once: {message}")
            try:
                self._refetch_media_segment(ctx, fp, seg)
                ok, message, _data = dash_decryptor.decrypt_segment_live(
                    encrypted_path=str(fp),
                    decrypted_path=str(dec_tmp),
                    raw_keys=self.key,
                    init_path=init_path_str,
                    key_sanity=False,
                )
            except Exception as exc:
                message = f"{message} (retry fetch failed: {exc})"

        elif wrong_key:
            logger.error(
                f"{ctx.protocol.upper()} live decrypt for {fp.name}: key sanity check confirmed the stored "
                f"key is wrong for this KID -- skipping the re-download retry (it would fail identically) "
                f"and dropping this segment. The vault entry for this KID is likely stale/corrupt and "
                f"should be re-extracted from a fresh license request: {message}"
            )

            # Deterministic for the whole KID, not just this segment
            self._register_wrong_key(self._stream_kids(ctx.stream), self.license_url, getattr(ctx.stream.drm, "pssh", None))
            if ctx.decrypt_aborted_reason is None:
                poisoned_now = self._kids_poisoned(self._stream_kids(ctx.stream))
                ctx.decrypt_aborted_reason = (f"wrong key for KID {poisoned_now or 'unknown'} (confirmed by key-sanity on {fp.name}) -- terminating track")
                self._skip_stream_wrong_key(ctx.stream, poisoned_now or "unknown")

        if not ok or not dec_tmp.exists():
            # Still bad after the retry -- drop this one segment (a small
            # A/V gap) instead of aborting the whole track: the CDN glitch
            # is almost certainly isolated to this segment, and failing the
            # entire video/audio track over one bad fragment is far worse
            # than a brief skip.
            logger.warning(f"{ctx.protocol.upper()} live decrypt still failing for {fp.name} after retry -- dropping this segment: {message if not ok else 'no output produced'}")
            fp.unlink(missing_ok=True)
            dec_tmp.unlink(missing_ok=True)
            return

        if adopt_whole_file and merger is not None:
            _t_adopt = time.monotonic()
            if merger.adopt(dec_tmp):
                # Keep seg_NNNNN in place (the missing-segment census looks
                # for it) as a hard link to the output: no second copy.
                try:
                    fp.unlink(missing_ok=True)
                    os.link(merger.out_path, fp)
                except OSError as exc:
                    logger.debug(f"{ctx.protocol.upper()} whole-file adopt: could not relink {fp.name}: {exc}")
                logger.info(f"{ctx.protocol.upper()} whole-file decrypt adopted as track output (no merge copy, {(time.monotonic() - _t_adopt) * 1000:.0f}ms)")
                return

            # Progressive output can't be appended after the init: redo
            # it through the regular --fragments-info path instead.
            logger.warning(f"{ctx.protocol.upper()} whole-file adopt refused for {fp.name} -- re-decrypting via the regular live path")
            dec_tmp.unlink(missing_ok=True)
            ok, message, _data = dash_decryptor.decrypt_segment_live(
                encrypted_path=str(fp),
                decrypted_path=str(dec_tmp),
                raw_keys=self.key,
                init_path=str(init_path) if init_path and init_path.exists() else None,
                key_sanity=False,
            )
            
            if not ok or not dec_tmp.exists():
                logger.warning(f"{ctx.protocol.upper()} live decrypt failed for {fp.name} after adopt refusal -- dropping this segment: {message}")
                fp.unlink(missing_ok=True)
                dec_tmp.unlink(missing_ok=True)
                return

        self._replace_segment_file(dec_tmp, fp, f"{ctx.protocol.upper()} live")
        logger.debug(f"{ctx.protocol.upper()} live decrypted -> {fp.name}")
        if ctx.live_merger is not None:
            ctx.live_merger.submit(seg.get("number"), fp)
