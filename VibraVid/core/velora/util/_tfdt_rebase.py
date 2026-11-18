# 16.08.26

import io
import struct
from pathlib import Path

_MOOF_SCAN_CHUNK = 1 * 1024 * 1024


def _read_box_header(fh, pos: int) -> tuple[bytes, int, int] | None:
    """Read the box header at *pos* without touching its payload. Returns (type, size, header_len) or None at EOF/malformed. 32-bit sizes only (CMAF/DASH/HLS moof/traf/tfdt boxes are always small)."""
    fh.seek(pos)
    head = fh.read(8)
    if len(head) < 8:
        return None
    size = struct.unpack(">I", head[:4])[0]
    box_type = head[4:8]
    if size == 1:
        head16 = fh.read(8)
        if len(head16) < 8:
            return None
        return box_type, struct.unpack(">Q", head16)[0], 16
    if size == 0:
        return None  # box-extends-to-EOF is never valid for moof/mdat in a fragmented stream we produced
    return box_type, size, 8


def _find_tfdt_offsets(fh, moof_off: int, moof_size: int) -> list[tuple[int, int]]:
    """Return [(value_offset, value_width_bytes), ...] for every traf>tfdt inside one moof."""
    out: list[tuple[int, int]] = []
    end = moof_off + moof_size
    off = moof_off + 8  # past moof's own header
    while off < end:
        hdr = _read_box_header(fh, off)
        if hdr is None:
            break
        box_type, size, header_len = hdr
        if size < header_len or off + size > end:
            break

        if box_type == b"traf":
            traf_end = off + size
            toff = off + header_len
            while toff < traf_end:
                thdr = _read_box_header(fh, toff)
                if thdr is None:
                    break
                ttype, tsize, theader_len = thdr
                if tsize < theader_len or toff + tsize > traf_end:
                    break
                if ttype == b"tfdt":
                    # tfdt is a FullBox: plain box header (theader_len, from the
                    # generic scanner above -- it doesn't know about FullBox
                    # version/flags) + version(1) + flags(3) + value.
                    fh.seek(toff + theader_len)
                    version = fh.read(1)[0]
                    value_off = toff + theader_len + 4  # past version(1)+flags(3)
                    out.append((value_off, 8 if version == 1 else 4))
                toff += tsize
        off += size
    return out


def track_timescale(init_path: Path) -> int | None:
    """Timescale (ticks per second) of the first track of an init segment (its mdhd box); None when it cannot be read."""
    try:
        data = Path(init_path).read_bytes()
    except OSError:
        return None
    pos = data.find(b"mdhd")
    if pos < 0 or pos + 24 > len(data):
        return None
    # [size(4)] 'mdhd' version(1) flags(3) creation modification timescale ... (v1 has 8-byte times)
    offset = pos + 16 if data[pos + 4] == 0 else pos + 24
    timescale = struct.unpack(">I", data[offset:offset + 4])[0]
    return timescale or None


def first_fragment_tfdt(path: Path) -> int | None:
    """baseMediaDecodeTime of the first fragment of *path* (tfdt is plaintext, so an encrypted segment can be asked too); None when it has no tfdt."""
    try:
        with open(path, "rb") as fh:
            fh.seek(0, 2)
            file_size = fh.tell()
            off = 0
            while off < file_size:
                hdr = _read_box_header(fh, off)
                if hdr is None:
                    return None
                box_type, size, _header_len = hdr
                if size <= 0 or off + size > file_size:
                    return None
                if box_type == b"moof":
                    offsets = _find_tfdt_offsets(fh, off, min(size, _MOOF_SCAN_CHUNK))
                    if not offsets:
                        return None
                    value_off, width = offsets[0]
                    fh.seek(value_off)
                    return struct.unpack(">Q" if width == 8 else ">I", fh.read(width))[0]
                off += size
    except OSError:
        return None
    return None


class FragmentContinuity:
    GAP_FACTOR = 1.5

    def __init__(self) -> None:
        self._contiguous = True
        self._prev_key: int | None = None
        self._prev_tfdt: int | None = None
        self._min_step: int | None = None

    def observe(self, key: int, tfdt: int | None) -> None:
        """Record the next fragment (*key* is its segment number, *tfdt* its first baseMediaDecodeTime)."""
        if tfdt is None or (self._prev_key is not None and key != self._prev_key + 1):
            self._contiguous = False
        elif self._prev_tfdt is not None:
            step = tfdt - self._prev_tfdt
            if step <= 0 or (self._min_step is not None and step > self._min_step * self.GAP_FACTOR):
                self._contiguous = False
            else:
                self._min_step = step if self._min_step is None else min(self._min_step, step)
        self._prev_key, self._prev_tfdt = key, tfdt

    def is_contiguous(self) -> bool:
        return self._contiguous


def rebase_fragment_bytes(data: bytes, base: int | None) -> tuple[bytes, int | None]:
    """Same as ``rebase_fragment_timestamps`` for one fragment already in memory (a live segment on its way to a pipe): subtract *base* from every tfdt in *data* and return ``(patched, base)``. With ``base=None`` the first tfdt found becomes the base, so feeding a track's segments one by one with the returned base starts it at zero. *data* comes back untouched when it has no tfdt."""
    buf = bytearray(data)
    fh = io.BytesIO(buf)
    size_total = len(buf)
    offsets: list[tuple[int, int]] = []
    off = 0
    while off < size_total:
        hdr = _read_box_header(fh, off)
        if hdr is None:
            break
        box_type, size, _header_len = hdr
        if size <= 0 or off + size > size_total:
            break
        if box_type == b"moof":
            offsets.extend(_find_tfdt_offsets(fh, off, size))
        off += size

    if not offsets:
        return data, base

    def _value(value_off: int, width: int) -> int:
        return struct.unpack(">Q" if width == 8 else ">I", buf[value_off : value_off + width])[0]

    if base is None:
        base = _value(*offsets[0])
    for value_off, width in offsets:
        patched = max(0, _value(value_off, width) - base)
        buf[value_off : value_off + width] = struct.pack(">Q" if width == 8 else ">I", patched)
    return bytes(buf), base


def rebase_fragment_timestamps(merged_path: Path, logger=None) -> bool:
    """In-place: subtract the first fragment's tfdt from every fragment's tfdt (moof>traf>tfdt), resetting an absolute CMAF/DASH-style base media decode time to start at zero"""
    try:
        with open(merged_path, "r+b") as fh:
            fh.seek(0, 2)
            file_size = fh.tell()

            all_tfdt: list[tuple[int, int]] = []
            off = 0
            while off < file_size:
                hdr = _read_box_header(fh, off)
                if hdr is None:
                    break
                box_type, size, _header_len = hdr
                if size <= 0 or off + size > file_size:
                    break

                if box_type == b"moof":
                    all_tfdt.extend(_find_tfdt_offsets(fh, off, min(size, _MOOF_SCAN_CHUNK)))

                off += size

            if not all_tfdt:
                return False

            def _read_value(value_off: int, width: int) -> int:
                fh.seek(value_off)
                raw = fh.read(width)
                return struct.unpack(">Q" if width == 8 else ">I", raw)[0]

            base = _read_value(*all_tfdt[0])
            if base == 0:
                return False  # already zero-based, nothing to do

            for value_off, width in all_tfdt:
                current = _read_value(value_off, width)
                new_value = max(0, current - base)
                fh.seek(value_off)
                fh.write(struct.pack(">Q" if width == 8 else ">I", new_value))

            if logger:
                logger.debug(f"[tfdt_rebase] {merged_path.name}: rebased {len(all_tfdt)} fragment(s) by -{base}")
            return True

    except Exception as exc:
        if logger:
            logger.debug(f"[tfdt_rebase] {merged_path.name}: skipped ({exc})")
        return False
