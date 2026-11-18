# 01.04.25

from pathlib import Path
from typing import TYPE_CHECKING, Any

from VibraVid.utils.http_client import create_client

from ...decryptor._segment_crypto import decrypt_aes128_file
from ..util._stream_helpers import describe_key_for_log
from .constants import REQUEST_TIMEOUT, logger
from .sniff import _reads_as_plaintext_ts

if TYPE_CHECKING:
    from .context import _SegmentDownloadContext


class HlsDecryptMixin:
    def _decrypt_hls_segment(self, ctx: "_SegmentDownloadContext", fp: Path, seg: dict[str, Any]) -> None:
        enc = seg.get("enc") or {}
        method = str(enc.get("method") or "NONE").upper()
        if method != "AES-128":
            return

        if _reads_as_plaintext_ts(fp):
            # A "skipped" download event (segment already on disk from a
            # previous, interrupted run) still reaches here -- if it was
            # already decrypted, decrypting it again would corrupt it.
            logger.debug(f"HLS AES-128: {fp.name} already looks decrypted, skipping")
            if ctx.live_merger is not None:
                ctx.live_merger.submit(seg.get("number"), fp)
            return

        key_data = enc.get("key_bytes")
        if key_data is None:
            key_url = enc.get("key_url")
            if not key_url:
                raise RuntimeError(f"Missing AES-128 key URL for {fp.name}")

            key_data = ctx.key_cache.get(key_url)
            if key_data is None:
                with ctx.key_cache_lock:
                    key_data = ctx.key_cache.get(key_url)
                    if key_data is None:
                        with create_client(
                            headers=ctx.all_headers, timeout=REQUEST_TIMEOUT, follow_redirects=True
                        ) as c:
                            r = c.get(key_url)
                            r.raise_for_status()
                            key_data = r.content

                        if len(key_data) != 16:
                            logger.warning(f"HLS AES-128 key length is {len(key_data)} bytes for {key_url}")

                        ctx.key_cache[key_url] = key_data
                        logger.info(f"HLS AES-128 key fetched {enc.get('iv')}:{key_data.hex()}")

        logger.debug(f"AES-128 LIVE decrypt path={fp} with key={describe_key_for_log(key_data)}")
        tmp_path = fp.with_suffix(fp.suffix + ".dec")
        decrypt_aes128_file(fp, tmp_path, key_data, enc.get("iv"), int(seg.get("number", 0) or 0))
        self._replace_segment_file(tmp_path, fp, "HLS AES-128")

        logger.debug(f"HLS AES-128 decrypted -> {fp.name}")
        if ctx.live_merger is not None:
            ctx.live_merger.submit(seg.get("number"), fp)
