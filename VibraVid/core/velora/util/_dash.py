# 10.04.26

import logging

from VibraVid.utils.http_client import create_client

logger = logging.getLogger(__name__)


def split_http_ranges(total_size: int, chunk_size: int) -> list[tuple[int, int]]:
    """Partition *total_size* bytes into ``(start, end)`` inclusive Range pairs."""
    ranges: list[tuple[int, int]] = []
    start = 0

    while start < total_size:
        end = min(start + chunk_size - 1, total_size - 1)
        ranges.append((start, end))
        start = end + 1

    return ranges


def build_dash_ranged_segments(media_url: str, headers: dict, chunk_size: int, request_timeout: int) -> tuple[list[dict], int]:
    """
    Return ``(chunks, total_size)`` for a single-file DASH/ISM asset."""
    try:
        content_len = 0
        accept_ranges = ""
        head_ok = False

        with create_client(headers=headers, timeout=request_timeout, follow_redirects=True) as c:
            try:
                r = c.head(media_url)
                r.raise_for_status()
                content_len = int((r.headers.get("content-length") or "0").strip() or "0")
                accept_ranges = (r.headers.get("accept-ranges") or "").lower()
                head_ok = True
            except Exception as head_exc:
                logger.debug(f"DASH range-split HEAD failed for {media_url}: {head_exc} — retrying with GET Range=0-0")

            if not head_ok or content_len == 0:
                try:
                    probe_headers = dict(headers)
                    probe_headers["Range"] = "bytes=0-0"
                    r2 = c.get(media_url, headers=probe_headers)

                    if r2.status_code == 206:
                        accept_ranges = "bytes"
                        cr = r2.headers.get("content-range", "")
                        if "/" in cr:
                            try:
                                content_len = int(cr.split("/")[-1].strip())
                            except ValueError:
                                pass
                    elif r2.status_code == 200:
                        content_len = int((r2.headers.get("content-length") or "0").strip() or "0")
                        accept_ranges = (r2.headers.get("accept-ranges") or "").lower()

                except Exception as get_exc:
                    logger.debug(f"DASH range-split GET probe failed for {media_url}: {get_exc}")

        if content_len <= 0 or "bytes" not in accept_ranges:
            logger.debug(f"DASH range-split skipped | url={media_url} | size={content_len} | accept-ranges={accept_ranges!r}")
            return [], 0

        ranges = split_http_ranges(content_len, chunk_size)
        logger.info(f"DASH range-split | url={media_url} | size={content_len} | chunk={chunk_size} | parts={len(ranges)}")
        return [
            {
                "url": media_url,
                "number": 0,
                "enc": {"method": "NONE"},
                "headers": {"Range": f"bytes={start}-{end}"},
            }
            for start, end in ranges
        ], content_len

    except Exception as exc:
        logger.warning(f"DASH range-split failed for {media_url}: {exc} — caller will use single-file fallback")
        return [], 0


def _iter_top_boxes(data: bytes):
    """Yield ``(offset, type, size, header_size)`` for each complete-header top-level box."""
    off = 0
    while off + 8 <= len(data):
        size = int.from_bytes(data[off:off + 4], "big")
        box_type = data[off + 4:off + 8]
        hdr = 8
        if size == 1:
            if off + 16 > len(data):
                return
            size = int.from_bytes(data[off + 8:off + 16], "big")
            hdr = 16
        if size < hdr:
            return
        yield off, box_type, size, hdr
        off += size


def _parse_sidx_refs(box: bytes, box_offset: int, hdr: int) -> list[tuple[int, int]]:
    """Absolute ``(start, size)`` of every subsegment a (flat) ``sidx`` references."""
    b = box[hdr:]
    version = b[0]
    p = 12  # version/flags + reference_ID + timescale
    if version == 0:
        first_offset = int.from_bytes(b[p + 4:p + 8], "big")
        p += 8
    else:
        first_offset = int.from_bytes(b[p + 8:p + 16], "big")
        p += 16
    count = int.from_bytes(b[p + 2:p + 4], "big")
    p += 4
    pos = box_offset + len(box) + first_offset
    refs: list[tuple[int, int]] = []
    for _ in range(count):
        ref = int.from_bytes(b[p:p + 4], "big")
        p += 12
        if ref & 0x80000000:  # hierarchical sidx -> not a flat moof list
            return []
        size = ref & 0x7FFFFFFF
        refs.append((pos, size))
        pos += size
    return refs


def build_dash_sidx_segments(media_url: str, headers: dict, chunk_size: int, request_timeout: int) -> tuple[list[dict], int]:
    """Return ``(chunks, total_size)`` for a single-file fragmented-MP4 asset, split on the ``sidx``'s moof+mdat boundaries (grouped to ~``chunk_size``)."""
    try:
        head = b""
        want = 256 * 1024
        with create_client(headers=headers, timeout=request_timeout, follow_redirects=True) as c:
            for _ in range(4):
                probe_headers = dict(headers)
                probe_headers["Range"] = f"bytes=0-{want - 1}"
                r = c.get(media_url, headers=probe_headers)
                if r.status_code != 206:
                    logger.debug(f"DASH sidx-split skipped | url={media_url} | status={r.status_code}")
                    return [], 0
                
                head = r.content
                sidx = next(((o, s, h) for o, t, s, h in _iter_top_boxes(head) if t == b"sidx"), None)
                if sidx is None:
                    if any(t == b"moof" for _o, t, _s, _h in _iter_top_boxes(head)) or len(head) < want:
                        logger.debug(f"DASH sidx-split skipped | url={media_url} | no sidx before first moof")
                        return [], 0
                elif sidx[0] + sidx[1] <= len(head):
                    break
                want *= 4
            else:
                return [], 0

        off, size, hdr = sidx
        refs = _parse_sidx_refs(head[off:off + size], off, hdr)
        if not refs:
            logger.debug(f"DASH sidx-split skipped | url={media_url} | empty/hierarchical sidx")
            return [], 0

        total_size = refs[-1][0] + refs[-1][1]
        ranges: list[tuple[int, int]] = []
        start, acc = refs[0][0], 0
        for ref_start, ref_size in refs:
            acc += ref_size
            if acc >= chunk_size:
                ranges.append((start, ref_start + ref_size - 1))
                start, acc = ref_start + ref_size, 0
        if acc:
            ranges.append((start, total_size - 1))

        logger.info(f"DASH sidx-split | url={media_url} | size={total_size} | fragments={len(refs)} | chunk~{chunk_size} | parts={len(ranges)}")
        return [
            {
                "url": media_url,
                "number": 0,
                "enc": {"method": "NONE"},
                "headers": {"Range": f"bytes={s}-{e}"},
            }
            for s, e in ranges
        ], total_size

    except Exception as exc:
        logger.warning(f"DASH sidx-split failed for {media_url}: {exc} — caller will use single-file fallback")
        return [], 0
