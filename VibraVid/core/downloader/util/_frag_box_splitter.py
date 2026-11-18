# 17.08.26

import struct


class FragBoxSplitter:
    def __init__(self) -> None:
        self._buf = bytearray()
        self._pos = 0
        self._header_acc = bytearray()
        self._frag_acc = bytearray()
        self._seen_moov = False
        self._in_fragment_phase = False
        self.eligible: bool | None = None
        self.init_bytes: bytes | None = None
        self.leftover: bytes = b""

    def _read_box_header(self, off: int):
        """(total_size, type, header_len) for the box at buf offset off, or None if not fully buffered yet / unsplittable (size==0, extends to EOF)."""
        if len(self._buf) - off < 8:
            return None
        size = struct.unpack(">I", self._buf[off : off + 4])[0]
        typ = bytes(self._buf[off + 4 : off + 8])
        hdr = 8
        if size == 1:
            if len(self._buf) - off < 16:
                return None
            size = struct.unpack(">Q", self._buf[off + 8 : off + 16])[0]
            hdr = 16
        elif size == 0:
            return None
        return size, typ, hdr

    def feed(self, chunk: bytes) -> list[bytes]:
        """Feed a chunk of bytes to the splitter. Returns a list of complete fragments (moof+mdat) that were split out, if any."""
        if self.eligible is False:
            self.leftover += chunk
            return []

        self._buf += chunk
        fragments: list[bytes] = []

        while True:
            parsed = self._read_box_header(self._pos)
            if parsed is None:
                break
            size, typ, _hdr = parsed
            if size < 8 or len(self._buf) - self._pos < size:
                break

            box_bytes = bytes(self._buf[self._pos : self._pos + size])
            self._pos += size

            if not self._in_fragment_phase:
                if typ == b"mdat" and not self._seen_moov:
                    self.eligible = False
                    self.leftover = bytes(self._buf)
                    self._buf = bytearray()
                    self._pos = 0
                    self._header_acc = bytearray()
                    return []

                if typ == b"moof" and self._seen_moov:
                    self.eligible = True
                    self.init_bytes = bytes(self._header_acc)
                    self._header_acc = bytearray()
                    self._in_fragment_phase = True
                    self._frag_acc += box_bytes
                else:
                    self._header_acc += box_bytes
                    if typ == b"moov":
                        self._seen_moov = True
            else:
                self._frag_acc += box_bytes
                if typ == b"mdat":
                    fragments.append(bytes(self._frag_acc))
                    self._frag_acc = bytearray()

            if self._pos > 4 * 1024 * 1024:
                del self._buf[: self._pos]
                self._pos = 0

        return fragments

    def finish(self) -> bytes:
        """Call once after the last `feed()`. Returns any trailing bytes that never completed into a fragment (e.g. a closing `mfra` index box with no following `mdat`)"""
        trailing = bytes(self._frag_acc)
        self._frag_acc = bytearray()
        return trailing
