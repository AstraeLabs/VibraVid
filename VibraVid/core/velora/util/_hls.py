# 10.04.26

import logging
import re
from urllib.parse import urljoin, urlparse

logger = logging.getLogger(__name__)


def hls_base_url(playlist_url: str) -> str:
    """Return the base URL directory for a given HLS playlist URL."""
    p = urlparse(playlist_url)
    path = p.path.rsplit("/", 1)[0]
    return f"{p.scheme}://{p.netloc}{path}/"


def parse_hls_variant_playlist(
    content: str, base_url: str, enc_override: dict | None = None
) -> tuple[list[dict], str | None]:
    """
    Parse an HLS *variant* (media) playlist.

    Returns a tuple of:
        - List of segment dicts: {"url", "number", "enc"}
        - Optional init segment URL (from EXT-X-MAP)
    """
    # Each block: {"init": Optional[str], "segments": List[Dict]}
    blocks: list[dict] = []
    current_block: dict | None = None
    current_enc: dict = {"method": "NONE", "key_url": None, "iv": None}
    pending_byterange: tuple[int, int] | None = None
    byterange_next_offset = 0
    byterange_prev_url: str | None = None

    def _ensure_block(init: str | None) -> dict:
        block = {"init": init, "segments": []}
        blocks.append(block)
        return block

    lines = content.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i].strip()

        if line.startswith("#EXT-X-BYTERANGE:"):
            m = re.match(r"#EXT-X-BYTERANGE:(\d+)(?:@(\d+))?", line)
            if m:
                length = int(m.group(1))
                offset = int(m.group(2)) if m.group(2) is not None else byterange_next_offset
                pending_byterange = (offset, offset + length - 1)
                byterange_next_offset = offset + length

        elif line.startswith("#EXT-X-KEY:"):
            method_m = re.search(r"METHOD=([^,\s\"]+)", line)
            uri_m = re.search(r'URI="([^"]+)"', line)
            iv_m = re.search(r"IV=0x([0-9a-fA-F]+)", line, re.I)
            current_enc = {
                "method": method_m.group(1).upper() if method_m else "NONE",
                "key_url": urljoin(base_url, uri_m.group(1)) if uri_m else None,
                "iv": iv_m.group(1).lower().zfill(32) if iv_m else None,
            }

        elif line.startswith("#EXT-X-MAP:"):
            uri_m = re.search(r'URI="([^"]+)"', line)
            init_url = urljoin(base_url, uri_m.group(1)) if uri_m else None
            current_block = _ensure_block(init_url)

        elif line.startswith("#EXTINF:"):
            dur_m = re.match(r"#EXTINF:([\d.]+)", line)
            seg_duration = float(dur_m.group(1)) if dur_m else 0.0
            i += 1
            while i < len(lines) and (not lines[i].strip() or lines[i].strip().startswith("#")):
                skipped = lines[i].strip()
                if skipped.startswith("#EXT-X-BYTERANGE:"):
                    m = re.match(r"#EXT-X-BYTERANGE:(\d+)(?:@(\d+))?", skipped)
                    if m:
                        length = int(m.group(1))
                        offset = int(m.group(2)) if m.group(2) is not None else byterange_next_offset
                        pending_byterange = (offset, offset + length - 1)
                        byterange_next_offset = offset + length
                i += 1

            if i < len(lines):
                seg_url = lines[i].strip()
                if seg_url and not seg_url.startswith("#"):
                    if current_block is None:
                        current_block = _ensure_block(None)

                    resolved_url = urljoin(base_url, seg_url)

                    # BYTERANGE offsets with no explicit @offset continue from the end
                    # of the previous range *within the same URI*; a URI change (even
                    # back to a previously-seen file) resets the implicit cursor per spec.
                    if resolved_url != byterange_prev_url:
                        byterange_next_offset = 0
                    byterange_prev_url = resolved_url

                    # Apply any encryption override to the segment's encryption info.
                    seg_enc = dict(current_enc)
                    if enc_override:
                        seg_enc.update(enc_override)

                    seg_dict = {
                        "url": resolved_url,
                        "enc": seg_enc,
                        "duration": seg_duration,
                    }
                    if pending_byterange is not None:
                        start, end = pending_byterange
                        seg_dict["headers"] = {"Range": f"bytes={start}-{end}"}
                        pending_byterange = None

                    current_block["segments"].append(seg_dict)
            i += 1
            continue

        i += 1

    if not blocks:
        return [], None

    # Pick the dominant block (the feature), tie-broken by total duration.
    best = max(blocks, key=lambda b: (len(b["segments"]), sum(s["duration"] for s in b["segments"])))
    if len(blocks) > 1:
        logger.info(f"HLS playlist has {len(blocks)} init blocks (sizes: {[len(b['segments']) for b in blocks]}); keeping the dominant block with {len(best['segments'])} segments")

    segments = best["segments"]
    for seg_num, seg in enumerate(segments):
        seg["number"] = seg_num

    return segments, best["init"]


def parse_hls_live_playlist(
    content: str, base_url: str, enc_override: dict | None = None
) -> tuple[list[dict], str | None, int, int, bool]:
    """Parse a live HLS playlist and return all relevant scheduling metadata."""
    segments, init_url = parse_hls_variant_playlist(content, base_url, enc_override)

    td_m = re.search(r"#EXT-X-TARGETDURATION:(\d+)", content)
    target_duration: int = int(td_m.group(1)) if td_m else 6

    seq_m = re.search(r"#EXT-X-MEDIA-SEQUENCE:(\d+)", content)
    media_sequence: int = int(seq_m.group(1)) if seq_m else 0

    is_ended: bool = "#EXT-X-ENDLIST" in content
    return segments, init_url, target_duration, media_sequence, is_ended
