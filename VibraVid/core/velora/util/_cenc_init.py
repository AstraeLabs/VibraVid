# 16.08.26

import struct

_ENCRYPTED_SAMPLE_ENTRY_TYPES = (b"encv", b"enca", b"encs", b"enct")
_VISUAL_SAMPLE_ENTRY_HEADER_SIZE = 78       # encv/encs/enct
_AUDIO_SAMPLE_ENTRY_HEADER_SIZE = 28        # enca


def _iter_boxes(buf, start: int, end: int):
    """Yield ``(offset, size, type, header_len)`` for each direct-child MP4 box in ``buf[start:end]``."""
    off = start
    while off + 8 <= end:
        size = struct.unpack(">I", buf[off : off + 4])[0]
        typ = bytes(buf[off + 4 : off + 8])
        hdr = 8
        if size == 1:
            size = struct.unpack(">Q", buf[off + 8 : off + 16])[0]
            hdr = 16
        elif size == 0:
            size = end - off
        if size < hdr or off + size > end:
            return
        yield off, size, typ, hdr
        off += size


def _find_box(buf, start: int, end: int, box_type: bytes):
    for off, size, typ, hdr in _iter_boxes(buf, start, end):
        if typ == box_type:
            return off, size, hdr
    return None


def _strip_sinf_from_stsd(buf: bytearray, stsd_off: int, stsd_size: int, stsd_hdr: int) -> int:
    """Rewrite every encv/enca/encs/enct sample entry inside one stsd box to its real codec fourcc (read from sinf>frma) with the sinf box removed."""
    entries_start = stsd_off + stsd_hdr + 4 + 4
    entries_end = stsd_off + stsd_size

    off = entries_start
    while off + 8 <= entries_end:
        entry_size = struct.unpack(">I", buf[off : off + 4])[0]
        entry_type = bytes(buf[off + 4 : off + 8])
        if entry_size < 8 or off + entry_size > entries_end:
            break

        if entry_type in _ENCRYPTED_SAMPLE_ENTRY_TYPES:
            fixed_hdr = _AUDIO_SAMPLE_ENTRY_HEADER_SIZE if entry_type == b"enca" else _VISUAL_SAMPLE_ENTRY_HEADER_SIZE
            found = _find_box(buf, off + 8 + fixed_hdr, off + entry_size, b"sinf")
            if found:
                sinf_off, sinf_size, _sinf_hdr = found
                frma = _find_box(buf, sinf_off + 8, sinf_off + sinf_size, b"frma")
                if frma:
                    frma_off, _frma_size, frma_hdr = frma
                    real_format = bytes(buf[frma_off + frma_hdr : frma_off + frma_hdr + 4])
                    buf[off + 4 : off + 8] = real_format

                # Remove the sinf box entirely and shrink every ancestor's size field.
                del buf[sinf_off : sinf_off + sinf_size]
                delta = sinf_size
                struct.pack_into(">I", buf, off, entry_size - delta)
                struct.pack_into(">I", buf, stsd_off, stsd_size - delta)
                return delta

        off += entry_size

    return 0


def strip_cenc_signaling(init_bytes: bytes) -> bytes:
    """Return a copy of a real ftyp+moov CENC init segment with every track's encv/enca/encs/enct sample entry rewritten to its plain codec fourcc and the corresponding sinf (frma+schm+schi incl. tenc) box removed."""
    buf = bytearray(init_bytes)

    while True:
        moov = _find_box(buf, 0, len(buf), b"moov")
        if not moov:
            break
        moov_off, moov_size, moov_hdr = moov

        made_change = False
        for trak_off, trak_size, trak_typ, trak_hdr in _iter_boxes(buf, moov_off + moov_hdr, moov_off + moov_size):
            if trak_typ != b"trak":
                continue

            mdia = _find_box(buf, trak_off + trak_hdr, trak_off + trak_size, b"mdia")
            if not mdia:
                continue
            mdia_off, mdia_size, mdia_hdr = mdia

            minf = _find_box(buf, mdia_off + mdia_hdr, mdia_off + mdia_size, b"minf")
            if not minf:
                continue
            minf_off, minf_size, minf_hdr = minf

            stbl = _find_box(buf, minf_off + minf_hdr, minf_off + minf_size, b"stbl")
            if not stbl:
                continue
            stbl_off, stbl_size, stbl_hdr = stbl

            stsd = _find_box(buf, stbl_off + stbl_hdr, stbl_off + stbl_size, b"stsd")
            if not stsd:
                continue
            stsd_off, stsd_size, stsd_hdr = stsd

            delta = _strip_sinf_from_stsd(buf, stsd_off, stsd_size, stsd_hdr)
            if delta == 0:
                continue

            # Walk back up shrinking every ancestor box's size field, moov included.
            for anc_off, anc_size in (
                (stbl_off, stbl_size),
                (minf_off, minf_size),
                (mdia_off, mdia_size),
                (trak_off, trak_size),
                (moov_off, moov_size),
            ):
                struct.pack_into(">I", buf, anc_off, anc_size - delta)

            made_change = True
            break  # buffer shifted -- restart the scan from moov

        if not made_change:
            break

    return bytes(buf)
