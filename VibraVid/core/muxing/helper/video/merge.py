# 16.04.24

import bisect
import gzip
import logging
import os
import re
import shutil
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

logger = logging.getLogger(__name__)
_COPY_BUFSIZE_DEFAULT = 2 * 1024 * 1024
_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
_PNG_SCAN_WINDOW = 64 * 1024
_TS_SYNC_RE = re.compile(rb"\x47(?:.{187}\x47){4}", re.DOTALL)


def _copy_bufsize() -> int:
    """Return copy buffer size in bytes (env override: `VV_MERGE_COPY_BUFSIZE_MB`)."""
    try:
        mb = int(os.getenv("VV_MERGE_COPY_BUFSIZE_MB", "2"))
        if mb < 1:
            mb = 1
        if mb > 64:
            mb = 64
        return mb * 1024 * 1024
    except (TypeError, ValueError):
        return _COPY_BUFSIZE_DEFAULT

_COPY_BUFSIZE = _copy_bufsize()

def _parallel_workers() -> int:
    """Return the worker count for parallel merge (env override: `VV_MERGE_PARALLEL_WORKERS`)."""
    try:
        n = int(os.getenv("VV_MERGE_PARALLEL_WORKERS", "0"))
        if n > 0:
            return min(n, 32)
    except (TypeError, ValueError):
        pass
    cpu = os.cpu_count() or 4
    return max(2, min(8, cpu))


def _parallel_enabled() -> bool:
    """Return True if parallel merge is enabled (env override: `VV_MERGE_PARALLEL=1` to force it back on)."""
    return os.getenv("VV_MERGE_PARALLEL", "0") == "1"


def _parallel_min_total_bytes() -> int:
    """Below this total size, thread-pool overhead isn't worth it -- use the serial path."""
    try:
        mb = int(os.getenv("VV_MERGE_PARALLEL_MIN_MB", "64"))
        return max(0, mb) * 1024 * 1024
    except (TypeError, ValueError):
        return 64 * 1024 * 1024


def _find_ts_start(buf: bytes) -> int:
    """Return the offset of the first MPEG-TS packet inside a PNG-wrapped segment."""
    m = _TS_SYNC_RE.search(buf)
    return m.start() if m else 0


def _merge_fmt_size(nb: int) -> str:
    if nb >= 1_073_741_824:
        return f"{nb / 1_073_741_824:.2f}GB"
    if nb >= 1_048_576:
        return f"{nb / 1_048_576:.1f}MB"
    if nb >= 1_024:
        return f"{nb / 1024:.0f}KB"
    return f"{nb}B"


def _segment_number(path: Path) -> int:
    try:
        stem = path.stem
        if stem.startswith("seg_"):
            return int(stem[4:])
    except (ValueError, IndexError):
        pass
    return 999_999_999


def binary_merge_segments(paths: list[Path], output_path: Path, merge_logger: logging.Logger | None = None) -> None:
    """Merge downloaded segments using direct raw binary concatenation."""
    log = merge_logger or logger
    t_start = time.monotonic()

    # Single stat() per path: reused for both the size filter and total accounting.
    valid: list[tuple[Path, int, int]] = []
    for p in paths:
        try:
            size = p.stat().st_size
        except OSError:
            continue
        if size > 0:
            valid.append((p, _segment_number(p), size))

    valid.sort(key=lambda item: item[1])
    if not valid:
        log.error("[binary_merge] No valid segments found")
        return

    raw_total = sum(size for _, _, size in valid)
    log.debug(f"[binary_merge] {len(valid)} valid segment(s), {_merge_fmt_size(raw_total)} raw total (stat+sort in {time.monotonic() - t_start:.3f}s)")

    if _parallel_enabled() and raw_total >= _parallel_min_total_bytes():
        _binary_merge_segments_parallel(valid, output_path, log)
    else:
        _binary_merge_segments_serial(valid, output_path, log)


def concat_demux_merge_segments(paths: list[Path], output_path: Path, merge_logger: logging.Logger | None = None) -> bool:
    """Merge MPEG-TS segments via ffmpeg's -f concat demuxer, treating each segment as its own input instead of raw-byte-appending them (binary_merge_segments())."""
    from VibraVid.setup import get_ffmpeg_path

    log = merge_logger or logger
    valid = sorted((p for p in paths if p.exists() and p.stat().st_size > 0), key=_segment_number)
    if not valid:
        log.warning("[concat_demux_merge] no valid segments found")
        return False

    list_path = output_path.with_suffix(output_path.suffix + ".concat_list.txt")
    try:
        with open(list_path, "w", encoding="utf-8") as f:
            for p in valid:
                escaped = str(p.resolve()).replace("\\", "/").replace("'", "'\\''")
                f.write(f"file '{escaped}'\n")

        # Keep the real extension on the temp file (…​.concat_tmp.ts, not ….ts.concat_tmp):
        # ffmpeg picks the output muxer from the extension and refuses an unknown one.
        tmp_out = output_path.with_name(f"{output_path.stem}.concat_tmp{output_path.suffix}")
        cmd = [
            get_ffmpeg_path(), "-v", "warning", "-f", "concat", "-safe", "0", "-i", str(list_path),
            "-c", "copy", "-avoid_negative_ts", "make_zero", str(tmp_out), "-y",
        ]
        t0 = time.monotonic()
        result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
        if result.returncode != 0 or not tmp_out.exists() or tmp_out.stat().st_size == 0:
            log.warning(f"[concat_demux_merge] ffmpeg concat failed (rc={result.returncode}): {result.stderr[-400:]}")
            tmp_out.unlink(missing_ok=True)
            return False

        os.replace(tmp_out, output_path)
        log.info(f"[concat_demux_merge] {len(valid)} segment(s) merged via concat demuxer -> {output_path.name} in {time.monotonic() - t0:.1f}s")
        return True
    finally:
        list_path.unlink(missing_ok=True)


def _binary_merge_segments_serial(
    valid: list[tuple[Path, int, int]], output_path: Path, log: logging.Logger
) -> None:
    t0 = time.monotonic()
    total_written = 0
    with open(output_path, "wb") as out_f:
        png_wrapped = 0
        for seg_path, _, seg_size in valid:
            with open(seg_path, "rb") as in_f:
                head = in_f.read(8)

                # gzip-compressed segment (magic bytes 1f 8b): the whole segment must be held in memory to decompress it.
                if head[:2] == b"\x1f\x8b":
                    try:
                        log.debug(f"[binary_merge] detected gzip-compressed segment: {seg_path.name}, decompressing ...")
                        data = gzip.decompress(head + in_f.read())
                        out_f.write(data)
                        total_written += len(data)
                        continue
                    except Exception as e:
                        log.warning(f"[binary_merge] failed to decompress {seg_path.name}: {e}, using raw data")
                        in_f.seek(0)
                        shutil.copyfileobj(in_f, out_f, _COPY_BUFSIZE)
                        total_written += seg_size
                        continue

                # PNG-wrapped segment: a fake image cover hides the real MPEG-TS payload.
                if head == _PNG_SIGNATURE:
                    prefix = head + in_f.read(_PNG_SCAN_WINDOW - len(head))
                    ts_off = _find_ts_start(prefix)
                    if ts_off > 0:
                        png_wrapped += 1
                        out_f.write(prefix[ts_off:])
                        shutil.copyfileobj(in_f, out_f, _COPY_BUFSIZE)

                        # Written = (prefix after the wrapper) + (streamed tail past the scan window).
                        total_written += (len(prefix) - ts_off) + (seg_size - len(prefix))
                        continue

                    log.warning(f"[binary_merge] PNG-wrapped segment {seg_path.name} has no TS sync, using raw data")
                    out_f.write(prefix)
                    shutil.copyfileobj(in_f, out_f, _COPY_BUFSIZE)
                    total_written += seg_size
                    continue

                # Raw segment: stream in fixed-size blocks instead of loading it whole.
                out_f.write(head)
                shutil.copyfileobj(in_f, out_f, _COPY_BUFSIZE)
                total_written += seg_size

    elapsed = time.monotonic() - t0
    if png_wrapped:
        log.info(f"[binary_merge] stripped PNG wrapper from {png_wrapped}/{len(valid)} segment(s)")

    if output_path.exists() and output_path.stat().st_size > 0:
        mb_s = (total_written / 1_048_576) / elapsed if elapsed > 0 else 0.0
        log.debug(f"[binary_merge] serial raw concat OK: {output_path.name} ({_merge_fmt_size(total_written)}) in {elapsed:.2f}s ({mb_s:.1f} MB/s)")
    else:
        log.error(f"[binary_merge] output is empty or missing: {output_path}")


def _classify_segment(seg_path: Path, seg_size: int) -> tuple[str, int, int, bytes | None]:
    """Return (kind, effective_size, extra_offset, cached_bytes).

    kind: 'raw' | 'gzip' | 'png'. For 'gzip', cached_bytes holds the fully decompressed payload.
    """
    with open(seg_path, "rb") as f:
        head = f.read(8)

        if head[:2] == b"\x1f\x8b":
            try:
                f.seek(0)
                data = gzip.decompress(f.read())
                return "gzip", len(data), 0, data
            except Exception:
                return "raw", seg_size, 0, None

        if head == _PNG_SIGNATURE:
            prefix = head + f.read(_PNG_SCAN_WINDOW - len(head))
            ts_off = _find_ts_start(prefix)
            if ts_off > 0:
                return "png", seg_size - ts_off, ts_off, None
            return "raw", seg_size, 0, None

    return "raw", seg_size, 0, None


def _write_chunk(
    output_path: Path,
    chunk: list[tuple[Path, str, int, int, bytes | None]],
    start_offset: int,
) -> int:
    """Write a *contiguous* run of segments starting at start_offset, one file open for the whole chunk instead of one per segment"""
    written = 0
    with open(output_path, "r+b") as out_f:
        out_f.seek(start_offset)
        for seg_path, kind, effective_size, ts_off, cached_bytes in chunk:
            if kind == "gzip":
                assert cached_bytes is not None
                out_f.write(cached_bytes)
                written += len(cached_bytes)
                continue
            if kind == "png":
                with open(seg_path, "rb") as in_f:
                    in_f.seek(ts_off)
                    shutil.copyfileobj(in_f, out_f, _COPY_BUFSIZE)
                written += effective_size
                continue
            with open(seg_path, "rb") as in_f:
                shutil.copyfileobj(in_f, out_f, _COPY_BUFSIZE)
            written += effective_size
    return written


def _binary_merge_segments_parallel(
    valid: list[tuple[Path, int, int]], output_path: Path, log: logging.Logger
) -> None:
    workers = _parallel_workers()
    t0 = time.monotonic()

    # Pass 1: classify each segment (cheap -- only peeks the first few KB, except for the rare gzip case which must fully decompress to learn its real size)
    classified: list[tuple[Path, str, int, int, bytes | None]] = [None] * len(valid)  # type: ignore[list-item]
    png_wrapped = 0
    gzip_count = 0
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(_classify_segment, seg_path, seg_size): idx
            for idx, (seg_path, _, seg_size) in enumerate(valid)
        }
        for fut in as_completed(futures):
            idx = futures[fut]
            seg_path, _, seg_size = valid[idx]
            kind, effective_size, extra_off, cached_bytes = fut.result()
            if kind == "png":
                png_wrapped += 1
            elif kind == "gzip":
                gzip_count += 1
                log.debug(f"[binary_merge] detected gzip-compressed segment: {seg_path.name}, decompressing ...")
            classified[idx] = (seg_path, kind, effective_size, extra_off, cached_bytes)

    t_classify = time.monotonic()
    log.debug(f"[binary_merge] classify phase: {len(valid)} segment(s) in {t_classify - t0:.2f}s ({workers} workers, {png_wrapped} png-wrapped, {gzip_count} gzip)")

    offsets: list[int] = [0] * len(valid)
    running = 0
    for idx, (_seg_path, _kind, effective_size, _extra_off, _cached) in enumerate(classified):
        offsets[idx] = running
        running += effective_size
    total_size = running

    if total_size == 0:
        log.error(f"[binary_merge] total effective size is 0 after classification: {output_path}")
        return

    # Preallocate so every worker can seek+write its own disjoint byte range
    # of the same file concurrently without ever touching another's region.
    with open(output_path, "wb") as out_f:
        out_f.truncate(total_size)

    t_prealloc = time.monotonic()
    log.debug(f"[binary_merge] preallocate phase: {_merge_fmt_size(total_size)} in {t_prealloc - t_classify:.3f}s")
    n_chunks = max(1, min(workers, len(classified)))
    target_step = total_size / n_chunks
    boundaries = [0]
    for i in range(1, n_chunks):
        target_byte = round(i * target_step)
        idx = bisect.bisect_left(offsets, target_byte)
        boundaries.append(max(boundaries[-1], min(idx, len(classified))))
    boundaries.append(len(classified))

    total_written = 0
    with ThreadPoolExecutor(max_workers=n_chunks) as pool:
        futures = []
        for i in range(n_chunks):
            lo, hi = boundaries[i], boundaries[i + 1]
            if lo >= hi:
                continue
            futures.append(pool.submit(_write_chunk, output_path, classified[lo:hi], offsets[lo]))
        for fut in as_completed(futures):
            total_written += fut.result()

    t_write = time.monotonic()
    write_elapsed = t_write - t_prealloc
    total_elapsed = t_write - t0

    if png_wrapped:
        log.info(f"[binary_merge] stripped PNG wrapper from {png_wrapped}/{len(valid)} segment(s)")

    if output_path.exists() and output_path.stat().st_size > 0:
        mb_s = (total_written / 1_048_576) / write_elapsed if write_elapsed > 0 else 0.0
        log.debug(
            f"[binary_merge] parallel raw concat OK ({workers} workers): {output_path.name} "
            f"({_merge_fmt_size(total_written)}) in {total_elapsed:.2f}s total "
            f"(classify {t_classify - t0:.2f}s, prealloc {t_prealloc - t_classify:.3f}s, "
            f"write {write_elapsed:.2f}s @ {mb_s:.1f} MB/s)"
        )
    else:
        log.error(f"[binary_merge] output is empty or missing: {output_path}")