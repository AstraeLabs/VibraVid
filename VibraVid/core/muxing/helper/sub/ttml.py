# 16.04.24

import logging
import os
import re
import xml.etree.ElementTree as et
from pathlib import Path

import ttconv.imsc.reader as imsc_reader
import ttconv.srt.writer as srt_writer
import ttconv.vtt.writer as vtt_writer
from rich.console import Console
from ttconv.srt.config import SRTWriterConfiguration
from ttconv.vtt.config import VTTWriterConfiguration

from .sanitize import sanitize_srt_file, sanitize_vtt_file

# suppress ttconv logging (Merging ISD paragraphs/regions)
logging.getLogger("ttconv").setLevel(logging.WARNING)

console = Console()
logger = logging.getLogger(__name__)
_XML10_INVALID = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f￾￿]")
_TS_RE = re.compile(r"(\d{1,}:[0-5]\d:[0-5]\d[,\.]\d{3})\s*-->\s*(\d{1,}:[0-5]\d:[0-5]\d[,\.]\d{3})")


def _get_declared_xml_encoding(block: bytes) -> str | None:
    """Extract encoding from XML declaration if present."""
    try:
        head = block[:256].decode("ascii", errors="ignore")
        match = re.search(r'<\?xml[^>]*encoding=["\']([^"\']+)["\']', head, re.IGNORECASE)
        if match:
            return match.group(1).strip()
    except Exception:
        pass
    return None


def _sanitize_xml(s: str) -> str:
    """Remove characters that are invalid in XML 1.0."""
    return _XML10_INVALID.sub("", s)


def _decode_ttml_block(block: bytes) -> str:
    """Decode TTML block with declared encoding first, then safe fallbacks."""
    candidates: list[str] = []
    declared = _get_declared_xml_encoding(block)
    if declared:
        candidates.append(declared)

    candidates.extend(
        [
            "utf-8-sig",
            "utf-8",
            "utf-16",
            "utf-16-le",
            "utf-16-be",
            "cp1252",
            "latin-1",
        ]
    )

    tried = set()
    for encoding in candidates:
        if encoding in tried:
            continue
        tried.add(encoding)
        try:
            decoded = block.decode(encoding)

            # A TTML block must start with '<' after stripping any BOM.
            # Skip encodings that mangle the start (e.g. UTF-16 turning '<tt' into CJK).
            stripped_start = decoded.lstrip("﻿￾")
            if stripped_start and stripped_start[0] != "<":
                continue

            logger.debug(f"Decoded TTML block with encoding: {encoding}")
            return decoded
        except Exception:
            logger.debug(f"Failed to decode TTML block with encoding: {encoding}")
            continue

    raise UnicodeDecodeError("utf-8", block, 0, min(len(block), 1), "could not decode TTML block with supported encodings")


def _parse_timestamp(ts: str) -> float | None:
    """Parse an SRT (00:00:00,000) or VTT (00:00:00.000) timestamp to seconds."""
    ts = ts.strip().replace(",", ".")
    parts = ts.split(":")
    if len(parts) != 3:
        return None
    try:
        h, m, s = parts
        return int(h) * 3600 + int(m) * 60 + float(s)
    except ValueError:
        return None


def _format_timestamp(seconds: float, is_vtt: bool) -> str:
    """Format seconds back to SRT (comma) or VTT (dot) timestamp."""
    seconds = max(0.0, seconds)
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = seconds % 60
    if is_vtt:
        return f"{h:02d}:{m:02d}:{s:06.3f}"
    return f"{h:02d}:{m:02d}:{s:06.3f}".replace(".", ",")


def _normalize_subtitle_timestamps(content: str, target_format: str) -> str:
    """Shift all cue timestamps so the first one starts at 0:00:00,000."""
    matches = list(_TS_RE.finditer(content))
    if not matches:
        return content

    first_starts = []
    for m in matches:
        t = _parse_timestamp(m.group(1))
        if t is not None:
            first_starts.append(t)

    if not first_starts:
        return content

    offset = min(first_starts)

    # Only shift if the offset is implausibly large (avoids touching normal files).
    if offset < 3600.0:
        return content

    is_vtt = target_format.lower() == "vtt"

    def _repl(m: "re.Match") -> str:
        start = _parse_timestamp(m.group(1))
        end = _parse_timestamp(m.group(2))
        if start is None or end is None:
            return m.group(0)
        new_start = _format_timestamp(start - offset, is_vtt)
        new_end = _format_timestamp(end - offset, is_vtt)
        return f"{new_start} --> {new_end}"

    return _TS_RE.sub(_repl, content)


def _shift_subtitle_timestamps(content: str, offset: float, target_format: str) -> str:
    """Add *offset* seconds to every cue timestamp in *content*."""
    is_vtt = target_format.lower() == "vtt"

    def _repl(m: "re.Match") -> str:
        start = _parse_timestamp(m.group(1))
        end = _parse_timestamp(m.group(2))
        if start is None or end is None:
            return m.group(0)
        return f"{_format_timestamp(start + offset, is_vtt)} --> {_format_timestamp(end + offset, is_vtt)}"

    return _TS_RE.sub(_repl, content)


def _last_cue_end_seconds(content: str) -> float | None:
    """Return the largest cue end-timestamp (in seconds) found in *content*, or None."""
    ends = [t for _, t in ((m.group(1), _parse_timestamp(m.group(2))) for m in _TS_RE.finditer(content)) if t is not None]
    return max(ends) if ends else None


def convert_ttml_to_format(
    ttml_path: str,
    output_path: str | None = None,
    target_format: str = "srt",
    chunk_offsets: list[float] | None = None,
) -> bool:
    """
    Convert TTML file or .m4s fragment containing TTML to SRT or VTT format.

    Args:
        ttml_path (str): Path to the TTML or .m4s file.
        output_path (Optional[str]): Path where to save the converted file. If None, uses same name as ttml_path but with target extension.
        target_format (str): The target format ('srt' or 'vtt').
        chunk_offsets (Optional[list[float]]): Absolute start time (seconds) of each
            manifest chunk, in the same order as the concatenated ``<tt>`` blocks
            (e.g. from the ISM chunk timeline). When given and long enough, this
            replaces the self-referential cumulative-offset estimate with the
            exact offset from the manifest -- correct even across caption-free
            gap chunks, which the estimate can't see.

    Returns:
        bool: True if conversion was successful, False otherwise.
    """
    if not os.path.exists(ttml_path):
        console.print(f"[red]File {ttml_path} does not exist")
        return False

    target_format = target_format.lower()
    if target_format not in ["srt", "vtt"]:
        console.print(f"[red]Unsupported target format for TTML conversion: {target_format}")
        return False

    if output_path is None:
        output_path = str(Path(ttml_path).with_suffix(f".{target_format}"))

    try:
        with open(ttml_path, "rb") as f:
            data = f.read()

        # Extract TTML blocks from plain XML or fragmented MP4 payloads.
        # Supports both XML declaration-prefixed documents and raw <tt> blocks.
        #
        # Fragmented-MP4 TTML (stpp) tracks pack one <tt> element per moof/mdat
        # sample, and "no caption right now" gaps are encoded as *self-closing*
        # <tt .../> stubs. The old pattern only recognized "...</tt>" as an end,
        # so a self-closing stub (which has no </tt>) made the non-greedy .*?
        # skip straight past it and swallow everything up to the next real
        # </tt> — including intervening moof/mdat box headers and further <tt>
        # opens — producing one corrupted, unparseable "block" per gap.
        # Matching the self-closing form explicitly keeps each sample isolated.
        raw_blocks = re.findall(
            rb"(?:<\?xml[^>]*\?>\s*)?<tt\b(?:[^>]*/>|[^>]*?>.*?</tt>)",
            data,
            re.DOTALL,
        )

        # Discard binary false-positives: real TTML XML is valid UTF-8; binary
        # MP4 box data that accidentally contains <tt...>...</tt> bytes is not.
        ttml_blocks = []
        for blk in raw_blocks:
            try:
                blk.decode("utf-8")
                ttml_blocks.append(blk)
            except UnicodeDecodeError:
                logger.debug(f"Discarding non-UTF-8 block that matched TTML pattern in {os.path.basename(ttml_path)}")
                pass

        if not ttml_blocks:
            # Try to see if it's a plain TTML without the XML declaration or just one block
            try:
                text_content = data.decode("utf-8", errors="ignore")
                if "<tt" in text_content and "</tt>" in text_content:
                    match = re.search(r"<tt.*?</tt>", text_content, re.DOTALL)
                    if match:
                        ttml_blocks = [match.group(0).encode("utf-8")]
            except Exception:
                pass

        if not ttml_blocks:
            console.print(f"[red]No valid TTML blocks found in {ttml_path}")
            return False

        all_captions: list[str] = []
        # ISM/Smooth Streaming packs one <tt> block per manifest chunk, and each
        # block's own cue timestamps restart near zero (relative to that chunk),
        # not to the absolute program time. Without re-basing, every block after
        # the first collapses back into the same [0, ~chunk-length] window instead
        # of covering its real position in the full duration.
        #
        # When the caller has the real per-chunk timeline (chunk_offsets), use it
        # directly -- exact, including across caption-free gap chunks. Otherwise
        # fall back to estimating a running offset from each block's own last cue
        # end-time, which is correct as long as every chunk has at least one cue.
        has_exact_offsets = bool(chunk_offsets) and len(chunk_offsets) >= len(ttml_blocks)
        cumulative_offset = 0.0

        for index, block in enumerate(ttml_blocks, start=1):
            try:
                # Decode and parse TTML
                ttml_str = _sanitize_xml(_decode_ttml_block(block))
                root = et.fromstring(ttml_str)
                tree = et.ElementTree(root)

                # Convert TTML to internal model
                model = imsc_reader.to_model(tree)

                if model is not None:
                    if target_format == "srt":
                        srt_config = SRTWriterConfiguration()
                        content = srt_writer.from_model(model, srt_config)
                    else:  # vtt
                        vtt_config = VTTWriterConfiguration()
                        content = vtt_writer.from_model(model, vtt_config)

                    content = content.strip()
                    if content:
                        offset = chunk_offsets[index - 1] if has_exact_offsets else cumulative_offset
                        if offset > 0:
                            content = _shift_subtitle_timestamps(content, offset, target_format)

                        if not has_exact_offsets:
                            block_end = _last_cue_end_seconds(content)
                            if block_end is not None:
                                cumulative_offset = max(cumulative_offset, block_end)

                        all_captions.append(content)

            except Exception as e:
                console.print(f"[yellow]Warning: Failed to process TTML block {index}/{len(ttml_blocks)}: {e}")
                continue

        if not all_captions:
            console.print(f"[red]No valid TTML blocks processed from {ttml_path}")
            return False

        # Combine output
        delimiter = "\n\n" if target_format == "srt" else "\n"
        output_content = delimiter.join(all_captions)
        output_content = _normalize_subtitle_timestamps(output_content, target_format)

        # Add VTT header if needed
        if target_format == "vtt" and not output_content.startswith("WEBVTT"):
            output_content = "WEBVTT\n\n" + output_content

        with open(output_path, "w", encoding="utf-8") as f:
            f.write(output_content)

        # Sanitize based on format
        if target_format == "srt":
            sanitize_srt_file(output_path)
        elif target_format == "vtt":
            sanitize_vtt_file(output_path)

        logger.info(f"convert_ttml_to_format: Successfully converted {os.path.basename(ttml_path)} to {target_format.upper()} with {len(ttml_blocks)} TTML blocks processed")
        return True

    except Exception as e:
        console.print(f"[red]Error during TTML to {target_format.upper()} conversion: {e}")
        return False


def extract_srt_from_m4s(m4s_file_path: str, output_srt_path: str | None = None) -> str:
    """Compatibility wrapper for the user requested function name."""
    if convert_ttml_to_format(m4s_file_path, output_srt_path):
        if output_srt_path is None:
            output_srt_path = str(Path(m4s_file_path).with_suffix(".srt"))
        with open(output_srt_path, encoding="utf-8") as f:
            return f.read()
    else:
        raise ValueError("Failed to extract SRT from m4s")
