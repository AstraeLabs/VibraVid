# 01.04.25

from pathlib import Path
from typing import TYPE_CHECKING, Any

from .constants import logger

if TYPE_CHECKING:
    from VibraVid.core.decryptor import Decryptor

    from .context import _SegmentDownloadContext


class IsmDecryptMixin:
    def _build_ism_init_from_fragment(self, ctx: "_SegmentDownloadContext", first_fp: Path) -> tuple[Path, Path]:
        """Synthesize both ftyp+moov variants (correct track_ID read from *first_fp*) and return (protected_init_path, clean_init_path)."""
        track_id = self._read_fragment_track_id(first_fp.read_bytes())
        kid_hex = getattr(ctx.stream.drm, "kid", None) if ctx.stream.drm else None
        kid_hex = kid_hex or "00000000000000000000000000000000"
        protected_data = self._build_ism_init(ctx.stream, kid_hex, track_id=track_id)
        clean_data = self._build_ism_init(ctx.stream, kid_hex, track_id=track_id, encrypted=False)

        protected_path = ctx.stream_dir / "_ism_live_init_protected.mp4"
        protected_path.write_bytes(protected_data)
        clean_path = ctx.stream_dir / f"seg_-000001{first_fp.suffix}"
        clean_path.write_bytes(clean_data)

        logger.debug(f"ISM live init synthesized from {first_fp.name} (track_id={track_id or 1}) -> {protected_path.name} + {clean_path.name}")
        return protected_path, clean_path

    def _ism_clear_segment(self, ctx: "_SegmentDownloadContext", fp: Path, seg: dict[str, Any]) -> None:
        """Clear (DRM-less) ISM: nothing to decrypt, but still needs the
        same tfhd.sample_description_index fix every fragment needs to
        match the single-entry init we synthesize (independent of
        encryption -- see _normalize_ism_fragment_sdi)."""
        fp.write_bytes(self._normalize_ism_fragment_sdi(fp.read_bytes()))
        if ctx.live_merger is not None:
            ctx.live_merger.submit(seg.get("number"), fp)

    def _decrypt_ism_segment(
        self, ctx: "_SegmentDownloadContext", fp: Path, seg: dict[str, Any], ism_decryptor: "Decryptor", init_path: Path
    ) -> None:
        fp.write_bytes(self._normalize_ism_fragment_sdi(fp.read_bytes()))

        dec_tmp = fp.with_suffix(fp.suffix + ".dec")
        want_key_sanity = self._want_key_sanity(ctx, fp, ism_decryptor)
        ok, message, _data = ism_decryptor.decrypt_segment_live(
            encrypted_path=str(fp),
            decrypted_path=str(dec_tmp),
            raw_keys=self.key,
            init_path=str(init_path),
            key_sanity=want_key_sanity,
        )
        if want_key_sanity:
            ctx.key_sanity_checked = True

        if not ok:
            if message and "key is wrong" in message:
                self._register_wrong_key(self._stream_kids(ctx.stream), self.license_url, getattr(ctx.stream.drm, "pssh", None))
                poisoned_now = self._kids_poisoned(self._stream_kids(ctx.stream))
                if ctx.decrypt_aborted_reason is None:
                    ctx.decrypt_aborted_reason = (
                        f"wrong key for KID {poisoned_now or 'unknown'} "
                        f"(confirmed by key-sanity on {fp.name}) -- terminating track"
                    )
                self._skip_stream_wrong_key(ctx.stream, poisoned_now or "unknown", ctx.bar_manager, ctx.task_key)
            raise RuntimeError(f"ISM live decrypt failed for {fp.name}: {message}")

        if not dec_tmp.exists():
            raise RuntimeError(f"ISM live decrypt produced no output for {fp.name}")

        self._replace_segment_file(dec_tmp, fp, "ISM live")
        logger.debug(f"ISM live decrypted -> {fp.name}")
        if ctx.live_merger is not None:
            ctx.live_merger.submit(seg.get("number"), fp)
