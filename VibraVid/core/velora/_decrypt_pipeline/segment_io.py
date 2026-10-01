# 01.04.25

import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from VibraVid.utils.http_client import create_client

from ..util._stream_helpers import extract_redirect_url, looks_like_bare_fragment, parse_range_header
from .constants import REQUEST_TIMEOUT, logger

if TYPE_CHECKING:
    from VibraVid.core.decryptor import Decryptor

    from .context import _SegmentDownloadContext


class SegmentIOMixin:
    def _replace_segment_file(self, source_path: Path, target_path: Path, reason: str) -> None:
        last_exc: Exception | None = None
        for attempt in range(1, 9):
            try:
                if target_path.exists():
                    try:
                        target_path.unlink()
                    except Exception:
                        pass

                source_path.replace(target_path)
                return
            except OSError as exc:
                last_exc = exc
                if attempt >= 8:
                    raise

                if getattr(exc, "winerror", None) not in (5, 32) and not isinstance(exc, PermissionError):
                    raise

                logger.debug(f"{reason} replace retry {attempt}/8 for {source_path.name} -> {target_path.name}: {exc}")
                time.sleep(0.05 * attempt)

        if last_exc:
            raise last_exc

    def _want_key_sanity(self, ctx: "_SegmentDownloadContext", fp: Path, decryptor: "Decryptor | None") -> bool:
        """True only for the first chunk with >= 1 senc fragment."""
        if ctx.key_sanity_checked:
            return False
        if decryptor is None:
            return False
        try:
            scanned = decryptor.scan_fragments(str(fp))
        except Exception as exc:
            logger.debug(f"{ctx.protocol.upper()} daemon scan failed for {fp.name}: {exc}")
            scanned = None
        if scanned is None:
            logger.error(f"{ctx.protocol.upper()} key-sanity deferred for {fp.name}: flux scan unavailable (daemon down or flux < 0.4.10) -- waiting for the next chunk")
            return False

        tracks, _cut = scanned
        if any(int(t.get("encrypted_fragments") or 0) > 0 for t in tracks):
            return True

        logger.debug(f"{ctx.protocol.upper()} key-sanity deferred for {fp.name}: no senc fragment in this chunk (pure clear lead) -- waiting for the first chunk carrying sample-encryption")
        return False

    def _fetch_exact_range(
        self, ctx: "_SegmentDownloadContext", url: str, seg_headers: dict, byte_range: tuple[int, int]
    ) -> bytes:
        """GET *url* with a Range header for the inclusive *byte_range*
        (reusing this segment's own headers on top of the stream's base
        headers), and return exactly that many bytes -- slicing
        client-side if the server, or an intermediate redirect hop,
        ignores the Range header and sends the whole resource back
        instead of honoring it with a 206."""
        start, end = byte_range
        req_headers = dict(ctx.all_headers)
        req_headers.update(seg_headers)
        req_headers["Range"] = f"bytes={start}-{end}"
        with create_client(headers=req_headers, timeout=REQUEST_TIMEOUT, follow_redirects=True) as c:
            resp = c.get(url)
            resp.raise_for_status()
            body = resp.content
        expected_len = end - start + 1
        if resp.status_code != 206 and len(body) > expected_len:
            body = body[start : end + 1]
        return body

    def _repair_media_segment(self, ctx: "_SegmentDownloadContext", fp: Path, seg: dict[str, Any]) -> None:
        """If *fp*'s downloaded bytes don't look like the bare fragment
        --fragments-info expects for a byte-range DASH/HLS media segment,
        try to recover the correct byte range before handing it to flux."""
        seg_headers = seg.get("headers") or {}
        byte_range = parse_range_header(seg_headers.get("Range"))
        if byte_range is None:
            return

        start, end = byte_range
        expected_len = end - start + 1
        try:
            data = fp.read_bytes()
        except OSError:
            return
        if len(data) == expected_len and looks_like_bare_fragment(data):
            return

        logger.debug(f"{ctx.protocol.upper()} media segment {fp.name} looks wrong (got {len(data)} bytes, expected {expected_len}) -- attempting repair")
        repaired: bytes | None = None
        try:
            redirect_url = extract_redirect_url(data)
            if redirect_url:
                logger.debug(f"{ctx.protocol.upper()} media segment {fp.name}: retrying via extracted URL: {redirect_url}")
                repaired = self._fetch_exact_range(ctx, redirect_url, seg_headers, byte_range)

            if repaired is None or len(repaired) != expected_len or not looks_like_bare_fragment(repaired):
                logger.debug(f"{ctx.protocol.upper()} media segment {fp.name}: retrying original URL with explicit Range")
                repaired = self._fetch_exact_range(ctx, seg["url"], seg_headers, byte_range)
        except Exception as exc:
            raise RuntimeError(f"{ctx.protocol.upper()} media segment {fp.name} repair failed: {exc}") from exc

        if len(repaired) != expected_len or not looks_like_bare_fragment(repaired):
            raise RuntimeError(f"{ctx.protocol.upper()} media segment {fp.name} still wrong after repair (got {len(repaired)} bytes, expected {expected_len})")

        fp.write_bytes(repaired)
        logger.info(f"{ctx.protocol.upper()} media segment repaired -> {fp.name}")

    def _refetch_media_segment(self, ctx: "_SegmentDownloadContext", fp: Path, seg: dict[str, Any]) -> None:
        """Re-download *fp*'s bytes fresh from the network. Used when a
        segment passes every structural check (right shape, right size)
        yet still fails to decrypt -- e.g. a CDN response cut short
        mid-transfer without the HTTP layer itself reporting an error,
        corrupting only the trailing mdat/senc data."""
        seg_headers = seg.get("headers") or {}
        byte_range = parse_range_header(seg_headers.get("Range"))
        if byte_range is not None:
            data = self._fetch_exact_range(ctx, seg["url"], seg_headers, byte_range)
        else:
            req_headers = dict(ctx.all_headers)
            req_headers.update(seg_headers)
            with create_client(headers=req_headers, timeout=REQUEST_TIMEOUT, follow_redirects=True) as c:
                resp = c.get(seg["url"])
                resp.raise_for_status()
                data = resp.content
        fp.write_bytes(data)
