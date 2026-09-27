# 05.05.26

import logging
import struct

from VibraVid.core.drm.system import _DRMSystems

logger = logging.getLogger(__name__)
ISM_TIMESCALE = 10_000_000  # PIFF / Smooth Streaming default (100-ns ticks)
TRACK_ID = 1


# ---------------------------------------------------------------------------
# Box primitives
# ---------------------------------------------------------------------------
def make_box(box_type: bytes, data: bytes) -> bytes:
    return struct.pack(">I4s", 8 + len(data), box_type) + data


def make_full_box(box_type: bytes, version: int, flags: int, payload: bytes) -> bytes:
    return make_box(box_type, bytes([version]) + flags.to_bytes(3, "big") + payload)


# ---------------------------------------------------------------------------
# ftyp
# ---------------------------------------------------------------------------
def make_ftyp() -> bytes:
    """Brands typically emitted for Smooth/PIFF CENC fragmented MP4."""
    payload = b"isml" + struct.pack(">I", 1)
    payload += b"iso5" + b"iso6" + b"piff" + b"msdh"
    return make_box(b"ftyp", payload)


# ---------------------------------------------------------------------------
# mvhd / tkhd / mdhd
# ---------------------------------------------------------------------------
def make_mvhd(duration: int, timescale: int = ISM_TIMESCALE) -> bytes:
    payload = struct.pack(">Q", 0)  # creation_time
    payload += struct.pack(">Q", 0)  # modification_time
    payload += struct.pack(">I", timescale)
    payload += struct.pack(">Q", duration)
    payload += struct.pack(">i", 0x00010000)  # rate = 1.0
    payload += struct.pack(">h", 0x0100)  # volume = 1.0
    payload += b"\x00" * 10  # reserved (2 + 8)
    payload += struct.pack(
        ">9i",
        0x00010000,
        0,
        0,
        0,
        0x00010000,
        0,
        0,
        0,
        0x40000000,
    )  # 3x3 matrix
    payload += b"\x00" * 24  # pre_defined (six 32-bit)
    payload += struct.pack(">I", 0xFFFFFFFF)  # next_track_id
    return make_full_box(b"mvhd", 1, 0, payload)


def make_tkhd_video(duration: int, width: int, height: int, track_id: int = TRACK_ID) -> bytes:
    payload = struct.pack(">Q", 0)  # creation_time
    payload += struct.pack(">Q", 0)  # modification_time
    payload += struct.pack(">I", track_id)
    payload += struct.pack(">I", 0)  # reserved
    payload += struct.pack(">Q", duration)
    payload += b"\x00" * 8  # reserved
    payload += struct.pack(">h", 0)  # layer
    payload += struct.pack(">h", 0)  # alternate_group
    payload += struct.pack(">h", 0)  # volume = 0 for video
    payload += b"\x00" * 2  # reserved
    payload += struct.pack(
        ">9i",
        0x00010000,
        0,
        0,
        0,
        0x00010000,
        0,
        0,
        0,
        0x40000000,
    )
    payload += struct.pack(">I", width << 16)
    payload += struct.pack(">I", height << 16)
    return make_full_box(b"tkhd", 1, 7, payload)


def make_tkhd_audio(duration: int, track_id: int = TRACK_ID) -> bytes:
    payload = struct.pack(">Q", 0)
    payload += struct.pack(">Q", 0)
    payload += struct.pack(">I", track_id)
    payload += struct.pack(">I", 0)
    payload += struct.pack(">Q", duration)
    payload += b"\x00" * 8
    payload += struct.pack(">h", 0)
    payload += struct.pack(">h", 1)  # alternate_group=1 (audio)
    payload += struct.pack(">h", 0x0100)  # volume = 1.0
    payload += b"\x00" * 2
    payload += struct.pack(
        ">9i",
        0x00010000,
        0,
        0,
        0,
        0x00010000,
        0,
        0,
        0,
        0x40000000,
    )
    payload += struct.pack(">I", 0)
    payload += struct.pack(">I", 0)
    return make_full_box(b"tkhd", 1, 7, payload)


def make_mdhd(duration: int, timescale: int, language: str = "und") -> bytes:
    payload = struct.pack(">Q", 0)
    payload += struct.pack(">Q", 0)
    payload += struct.pack(">I", timescale)
    payload += struct.pack(">Q", duration)
    payload += _pack_iso639_2(language)
    payload += struct.pack(">H", 0)  # pre_defined
    return make_full_box(b"mdhd", 1, 0, payload)


def _pack_iso639_2(lang: str) -> bytes:
    """Pack a 3-letter ISO-639-2 language code into the 15 bits used by mdhd."""
    code = (lang or "und").lower()
    if len(code) != 3 or not all("a" <= c <= "z" for c in code):
        code = "und"
    val = ((ord(code[0]) - 0x60) & 0x1F) << 10 | ((ord(code[1]) - 0x60) & 0x1F) << 5 | ((ord(code[2]) - 0x60) & 0x1F)
    return struct.pack(">H", val)


# ---------------------------------------------------------------------------
# hdlr / minf children
# ---------------------------------------------------------------------------
def make_hdlr(handler_type: bytes, name: str) -> bytes:
    payload = b"\x00" * 4
    payload += handler_type  # 'vide' / 'soun'
    payload += b"\x00" * 12
    payload += name.encode("utf-8") + b"\x00"
    return make_full_box(b"hdlr", 0, 0, payload)


def make_vmhd() -> bytes:
    payload = struct.pack(">H", 0)  # graphicsmode
    payload += struct.pack(">3H", 0, 0, 0)  # opcolor
    return make_full_box(b"vmhd", 0, 1, payload)


def make_smhd() -> bytes:
    payload = struct.pack(">h", 0)  # balance
    payload += struct.pack(">H", 0)  # reserved
    return make_full_box(b"smhd", 0, 0, payload)


def make_dref() -> bytes:
    url_box = make_full_box(b"url ", 0, 1, b"")
    payload = struct.pack(">I", 1) + url_box
    return make_full_box(b"dref", 0, 0, payload)


def make_dinf() -> bytes:
    return make_box(b"dinf", make_dref())


# ---------------------------------------------------------------------------
# Encryption metadata: tenc + sinf wrapper
# ---------------------------------------------------------------------------
def make_tenc(kid_bytes: bytes) -> bytes:
    payload = struct.pack(">B", 0)  # reserved
    payload += struct.pack(">B", 0)  # reserved
    payload += struct.pack(">B", 1)  # default_isProtected
    payload += struct.pack(">B", 8)  # default_Per_Sample_IV_Size
    payload += kid_bytes  # default_KID (16 bytes)
    return make_full_box(b"tenc", 0, 0, payload)


def make_sinf(original_format: bytes, kid_bytes: bytes) -> bytes:
    frma = make_box(b"frma", original_format)
    schm = make_full_box(b"schm", 0, 0, b"cenc" + struct.pack(">I", 0x00010000))
    schi = make_box(b"schi", make_tenc(kid_bytes))
    return make_box(b"sinf", frma + schm + schi)


# ---------------------------------------------------------------------------
# HEVC: parse Annex-B start-code NAL units and build a real hvcC box
# ---------------------------------------------------------------------------
def _split_annexb_nalus(data: bytes) -> list[bytes]:
    nalus: list[bytes] = []
    i = 0
    n = len(data)
    while i < n:
        if data[i : i + 4] == b"\x00\x00\x00\x01":
            i += 4
        elif data[i : i + 3] == b"\x00\x00\x01":
            i += 3
        else:
            i += 1
            continue
        start = i
        while i < n:
            if data[i : i + 4] == b"\x00\x00\x00\x01" or data[i : i + 3] == b"\x00\x00\x01":
                break
            i += 1
        nalus.append(data[start:i])
    return nalus


def _strip_emulation_prevention(data: bytes) -> bytes:
    out = bytearray()
    i = 0
    n = len(data)
    while i < n:
        if i + 2 < n and data[i] == 0 and data[i + 1] == 0 and data[i + 2] == 3:
            out.append(0)
            out.append(0)
            i += 3
        else:
            out.append(data[i])
            i += 1
    return bytes(out)


def build_hvcc(codec_private: bytes) -> bytes:
    """Build a valid hvcC payload from raw start-code VPS+SPS+PPS NAL units."""
    nalus = _split_annexb_nalus(codec_private)
    by_type: dict[int, list[bytes]] = {}
    for nalu in nalus:
        if not nalu:
            continue
        nut = (nalu[0] >> 1) & 0x3F
        by_type.setdefault(nut, []).append(nalu)

    sps_list = by_type.get(33, [])
    if not sps_list:
        raise RuntimeError("HEVC SPS NAL unit not found in CodecPrivateData")

    sps_clean = _strip_emulation_prevention(sps_list[0])
    if len(sps_clean) < 15:
        raise RuntimeError("HEVC SPS too short to extract profile_tier_level")

    ptl = sps_clean[3:15]
    profile_space_tier_idc = ptl[0]
    profile_compatibility = ptl[1:5]
    constraint = ptl[5:11]
    level_idc = ptl[11]

    payload = bytearray()
    payload.append(0x01)  # configurationVersion
    payload.append(profile_space_tier_idc)
    payload += profile_compatibility
    payload += constraint
    payload.append(level_idc)
    payload += struct.pack(">H", 0xF000)  # min_spatial_segmentation_idc
    payload.append(0xFC)  # parallelismType
    payload.append(0xFC)  # chromaFormat
    payload.append(0xF8)  # bitDepthLumaMinus8
    payload.append(0xF8)  # bitDepthChromaMinus8
    payload += struct.pack(">H", 0)  # avgFrameRate
    payload.append(0x03)  # constantFrameRate=0, lengthSizeMinusOne=3 (4-byte)

    arrays: list[tuple[int, list[bytes]]] = []
    for nut in (32, 33, 34):  # VPS, SPS, PPS
        items = by_type.get(nut, [])
        if items:
            arrays.append((nut, items))

    payload.append(len(arrays))
    for nut, items in arrays:
        payload.append(nut & 0x3F)  # array_completeness=0, NAL_unit_type
        payload += struct.pack(">H", len(items))
        for nalu in items:
            payload += struct.pack(">H", len(nalu))
            payload += nalu
    return bytes(payload)


# ---------------------------------------------------------------------------
# AVC (H.264): pass through standard avcC bytes when present
# ---------------------------------------------------------------------------
def build_avcc(codec_private: bytes) -> bytes:
    """
    Smooth Streaming H.264 CodecPrivateData is the SPS+PPS as Annex-B
    start-code NAL units.  Convert to an avcC structure (ISO/IEC 14496-15).
    """
    nalus = _split_annexb_nalus(codec_private)
    sps_list = [n for n in nalus if n and (n[0] & 0x1F) == 7]
    pps_list = [n for n in nalus if n and (n[0] & 0x1F) == 8]
    if not sps_list:
        raise RuntimeError("H.264 SPS NAL unit not found in CodecPrivateData")
    sps0 = sps_list[0]
    if len(sps0) < 4:
        raise RuntimeError("H.264 SPS too short")

    payload = bytearray()
    payload.append(0x01)  # configurationVersion
    payload.append(sps0[1])  # AVCProfileIndication
    payload.append(sps0[2])  # profile_compatibility
    payload.append(sps0[3])  # AVCLevelIndication
    payload.append(0xFF)  # reserved(6) | lengthSizeMinusOne(2)=3
    payload.append(0xE0 | (len(sps_list) & 0x1F))  # reserved(3) | numOfSPS(5)
    for sps in sps_list:
        payload += struct.pack(">H", len(sps))
        payload += sps

    payload.append(len(pps_list) & 0xFF)
    for pps in pps_list:
        payload += struct.pack(">H", len(pps))
        payload += pps

    return bytes(payload)


def _build_dec3_default(sample_rate: int, channels: int) -> bytes:
    """Synthesise a minimal EC-3 SpecificBox (dec3) payload from stream metadata."""
    # fscod: 0=48kHz, 1=44.1kHz, 2=32kHz
    fscod = 0 if sample_rate >= 44100 else (1 if sample_rate >= 32000 else 2)
    bsid = 16  # E-AC-3
    ch = channels or 6
    if ch >= 6:
        acmod, lfeon = 7, 1  # 3/2 + LFE
    elif ch >= 3:
        acmod, lfeon = 7, 0  # 3/2
    elif ch == 2:
        acmod, lfeon = 2, 0  # stereo
    else:
        acmod, lfeon = 1, 0  # mono

    # 13+3+2+5+1+1+3+3+1+3+4+1 = 40 bits = 5 bytes
    bits = 0
    bits = (bits << 13) | 0  # data_rate (unknown/VBR)
    bits = (bits << 3) | 0  # num_ind_sub (1 independent substream)
    bits = (bits << 2) | (fscod & 0x03)
    bits = (bits << 5) | (bsid & 0x1F)
    bits = (bits << 1) | 0  # reserved
    bits = (bits << 1) | 0  # asvc
    bits = (bits << 3) | 0  # bsmod (complete main)
    bits = (bits << 3) | (acmod & 0x07)
    bits = (bits << 1) | (lfeon & 0x01)
    bits = (bits << 3) | 0  # reserved
    bits = (bits << 4) | 0  # num_dep_sub
    bits = (bits << 1) | 0  # reserved
    return bits.to_bytes(5, "big")


def _build_dac3_default(sample_rate: int, channels: int) -> bytes:
    """Synthesise a minimal AC-3 SpecificBox (dac3) payload from stream metadata."""
    fscod = 0 if sample_rate >= 44100 else (1 if sample_rate >= 32000 else 2)
    bsid = 8  # AC-3
    ch = channels or 6
    if ch >= 6:
        acmod, lfeon = 7, 1
    elif ch == 2:
        acmod, lfeon = 2, 0
    else:
        acmod, lfeon = 1, 0

    # 2+5+3+3+1+5+5 = 24 bits = 3 bytes
    bits = 0
    bits = (bits << 2) | (fscod & 0x03)
    bits = (bits << 5) | (bsid & 0x1F)
    bits = (bits << 3) | 0  # bsmod
    bits = (bits << 3) | (acmod & 0x07)
    bits = (bits << 1) | (lfeon & 0x01)
    bits = (bits << 5) | 0  # bit_rate_code
    bits = (bits << 5) | 0  # reserved
    return bits.to_bytes(3, "big")


# ---------------------------------------------------------------------------
# AAC (audio): build esds from AudioSpecificConfig
# ---------------------------------------------------------------------------
def build_esds(audio_specific_config: bytes) -> bytes:
    """Build an esds box (ISO/IEC 14496-1) wrapping an AAC AudioSpecificConfig."""
    asc = audio_specific_config or b""

    def _desc(tag: int, body: bytes) -> bytes:
        # Use the 4-byte expanded length form universally.
        n = len(body)
        return bytes([tag]) + bytes([0x80, 0x80, 0x80, n & 0xFF]) + body

    dsi = _desc(0x05, asc)  # DecoderSpecificInfo
    dec_cfg_body = (
        b"\x40"  # objectTypeIndication = MPEG-4 AAC
        + b"\x15"  # streamType=AudioStream(0x05<<2|1)
        + b"\x00\x00\x00"  # bufferSizeDB
        + b"\x00\x00\x00\x00"  # maxBitrate
        + b"\x00\x00\x00\x00"  # avgBitrate
        + dsi
    )
    dec_cfg = _desc(0x04, dec_cfg_body)
    sl_cfg = _desc(0x06, b"\x02")  # SLConfigDescriptor predefined=2
    es_body = (
        struct.pack(">H", 0)  # ES_ID
        + b"\x00"  # flags
        + dec_cfg
        + sl_cfg
    )
    es_descriptor = _desc(0x03, es_body)
    return make_full_box(b"esds", 0, 0, es_descriptor)


# ---------------------------------------------------------------------------
# Sample entries
# ---------------------------------------------------------------------------
def _visual_sample_entry_header(width: int, height: int) -> bytes:
    entry = b"\x00" * 6  # reserved
    entry += struct.pack(">H", 1)  # data_reference_index
    entry += b"\x00" * 16  # pre_defined + reserved
    entry += struct.pack(">H", width)
    entry += struct.pack(">H", height)
    entry += b"\x00\x48\x00\x00" * 2  # 72 dpi horiz/vert resolution
    entry += b"\x00" * 4  # reserved
    entry += struct.pack(">H", 1)  # frame_count
    entry += b"\x00" * 32  # compressorname
    entry += struct.pack(">H", 0x0018)  # depth = 24
    entry += struct.pack(">h", -1)  # pre_defined = -1
    return entry


def _audio_sample_entry_header(sample_rate: int, channels: int) -> bytes:
    entry = b"\x00" * 6  # reserved
    entry += struct.pack(">H", 1)  # data_reference_index
    entry += b"\x00" * 8  # reserved
    entry += struct.pack(">H", channels or 2)  # channelcount
    entry += struct.pack(">H", 16)  # samplesize
    entry += struct.pack(">H", 0)  # pre_defined
    entry += struct.pack(">H", 0)  # reserved
    entry += struct.pack(">I", (sample_rate or 48000) << 16)
    return entry


def build_video_stsd(
    codec: str, codec_private: bytes, width: int, height: int, kid_bytes: bytes, encrypted: bool = True
) -> bytes:
    if codec in ("hvc1", "hev1"):
        config_box = make_box(b"hvcC", build_hvcc(codec_private))
        original_format = b"hvc1"
    elif codec in ("dvhe", "dvh1"):
        config_box = make_box(b"hvcC", build_hvcc(codec_private))
        original_format = b"dvh1"
    elif codec in ("avc1", "avc3"):
        config_box = make_box(b"avcC", build_avcc(codec_private))
        original_format = b"avc1"
    else:
        raise ValueError(f"Unsupported video codec for ISM init: {codec!r}")

    if not encrypted:
        entry = _visual_sample_entry_header(width, height) + config_box
        return make_full_box(b"stsd", 0, 0, struct.pack(">I", 1) + make_box(original_format, entry))

    sinf_box = make_sinf(original_format, kid_bytes)
    entry = _visual_sample_entry_header(width, height) + config_box + sinf_box
    encv = make_box(b"encv", entry)
    return make_full_box(b"stsd", 0, 0, struct.pack(">I", 1) + encv)


def build_audio_stsd(
    codec: str, codec_private: bytes, sample_rate: int, channels: int, kid_bytes: bytes, encrypted: bool = True
) -> bytes:
    if codec.startswith("mp4a") or codec in ("aac", "aacl", "aach", "aacp"):
        config_box = build_esds(codec_private)
        original_format = b"mp4a"
    elif codec in ("ec-3", "eac3"):
        payload = codec_private if codec_private else _build_dec3_default(sample_rate, channels)
        config_box = make_box(b"dec3", payload)
        original_format = b"ec-3"
    elif codec in ("ac-3", "ac3"):
        payload = codec_private if codec_private else _build_dac3_default(sample_rate, channels)
        config_box = make_box(b"dac3", payload)
        original_format = b"ac-3"
    else:
        raise ValueError(f"Unsupported audio codec for ISM init: {codec!r}")

    if not encrypted:
        entry = _audio_sample_entry_header(sample_rate, channels) + config_box
        return make_full_box(b"stsd", 0, 0, struct.pack(">I", 1) + make_box(original_format, entry))

    sinf_box = make_sinf(original_format, kid_bytes)
    entry = _audio_sample_entry_header(sample_rate, channels) + config_box + sinf_box
    enca = make_box(b"enca", entry)
    return make_full_box(b"stsd", 0, 0, struct.pack(">I", 1) + enca)


# ---------------------------------------------------------------------------
# Empty stbl tables (fragments carry the real timing in moof/traf/trun)
# ---------------------------------------------------------------------------
def make_empty_stts() -> bytes:
    return make_full_box(b"stts", 0, 0, struct.pack(">I", 0))


def make_empty_stsc() -> bytes:
    return make_full_box(b"stsc", 0, 0, struct.pack(">I", 0))


def make_empty_stsz() -> bytes:
    return make_full_box(b"stsz", 0, 0, struct.pack(">II", 0, 0))


def make_empty_stco() -> bytes:
    return make_full_box(b"stco", 0, 0, struct.pack(">I", 0))


def build_stbl(stsd_box: bytes) -> bytes:
    return make_box(
        b"stbl",
        make_empty_stts() + make_empty_stsc() + make_empty_stco() + make_empty_stsz() + stsd_box,
    )


# ---------------------------------------------------------------------------
# minf / mdia / trak / mvex
# ---------------------------------------------------------------------------
def build_video_minf(stsd_box: bytes) -> bytes:
    return make_box(b"minf", make_vmhd() + make_dinf() + build_stbl(stsd_box))


def build_audio_minf(stsd_box: bytes) -> bytes:
    return make_box(b"minf", make_smhd() + make_dinf() + build_stbl(stsd_box))


def build_video_trak(
    duration: int,
    width: int,
    height: int,
    codec: str,
    codec_private: bytes,
    kid_bytes: bytes,
    track_id: int = TRACK_ID,
    timescale: int = ISM_TIMESCALE,
    encrypted: bool = True,
) -> bytes:
    tkhd = make_tkhd_video(duration, width, height, track_id=track_id)
    mdhd = make_mdhd(duration, timescale=timescale)
    hdlr = make_hdlr(b"vide", "VideoHandler")
    stsd = build_video_stsd(codec, codec_private, width, height, kid_bytes, encrypted=encrypted)
    mdia = make_box(b"mdia", mdhd + hdlr + build_video_minf(stsd))
    return make_box(b"trak", tkhd + mdia)


def build_audio_trak(
    duration: int,
    codec: str,
    codec_private: bytes,
    sample_rate: int,
    channels: int,
    kid_bytes: bytes,
    language: str = "und",
    track_id: int = TRACK_ID,
    timescale: int = ISM_TIMESCALE,
    encrypted: bool = True,
) -> bytes:
    tkhd = make_tkhd_audio(duration, track_id=track_id)
    mdhd = make_mdhd(duration, timescale=timescale, language=language)
    hdlr = make_hdlr(b"soun", "SoundHandler")
    stsd = build_audio_stsd(codec, codec_private, sample_rate, channels, kid_bytes, encrypted=encrypted)
    mdia = make_box(b"mdia", mdhd + hdlr + build_audio_minf(stsd))
    return make_box(b"trak", tkhd + mdia)


def make_mehd(duration: int) -> bytes:
    return make_full_box(b"mehd", 1, 0, struct.pack(">Q", duration))


def make_trex(track_id: int = TRACK_ID) -> bytes:
    payload = struct.pack(">I", track_id)
    payload += struct.pack(">I", 1)  # default_sample_description_index
    payload += struct.pack(">I", 0)  # default_sample_duration
    payload += struct.pack(">I", 0)  # default_sample_size
    payload += struct.pack(">I", 0)  # default_sample_flags
    return make_full_box(b"trex", 0, 0, payload)


def build_mvex(duration: int, track_id: int = TRACK_ID) -> bytes:
    return make_box(b"mvex", make_mehd(duration) + make_trex(track_id=track_id))


# ---------------------------------------------------------------------------
# Optional pssh boxes (PlayReady / Widevine)
# ---------------------------------------------------------------------------
_PLAYREADY_SYSTEM_ID = bytes.fromhex(_DRMSystems.PLAYREADY)
_WIDEVINE_SYSTEM_ID = bytes.fromhex(_DRMSystems.WIDEVINE)


def make_pssh_playready(pro_bytes: bytes) -> bytes:
    # pssh v0: SystemID || DataSize || Data
    payload = _PLAYREADY_SYSTEM_ID + struct.pack(">I", len(pro_bytes)) + pro_bytes
    return make_full_box(b"pssh", 0, 0, payload)


def make_pssh_widevine(kid_bytes: bytes) -> bytes:
    # Minimal Widevine ContentProtection payload: tag 2 (key_id) of length 16.
    data = b"\x12\x10" + kid_bytes
    payload = _WIDEVINE_SYSTEM_ID + struct.pack(">I", len(data)) + data
    return make_full_box(b"pssh", 0, 0, payload)


# ---------------------------------------------------------------------------
# Public entry: assemble the final init segment
# ---------------------------------------------------------------------------
def kid_hex_to_bytes(kid: str) -> bytes:
    """Accept the canonical CENC big-endian hex string used by ``DRMInfo.kid``."""
    cleaned = (kid or "").replace("-", "").replace("{", "").replace("}", "").lower()
    if len(cleaned) != 32:
        raise ValueError(f"KID must be 16 bytes (32 hex chars), got {len(cleaned)}: {kid!r}")
    return bytes.fromhex(cleaned)


def build_ism_init_segment(
    *,
    stream_type: str,
    duration: int,
    codec: str,
    codec_private: bytes,
    kid_hex: str,
    width: int = 0,
    height: int = 0,
    sample_rate: int = 0,
    channels: int = 0,
    language: str = "und",
    timescale: int = ISM_TIMESCALE,
    pro_bytes: bytes | None = None,
    track_id: int = TRACK_ID,
    encrypted: bool = True,
) -> bytes:
    """
    Assemble ``ftyp + moov`` for a single-track fragmented MP4 init segment.

    *duration* is in *timescale* ticks and may be 0 (mehd will follow). All the
    real per-sample timing lives inside the ``moof`` boxes that follow this
    init in the encrypted stream.

    *encrypted* controls whether the ``stsd`` entry declares CENC protection
    (``encv``/``enca`` + ``sinf``/``tenc``) or the plain codec fourcc with no
    encryption signaling at all.
    """
    kid_bytes = kid_hex_to_bytes(kid_hex)

    if stream_type == "video":
        if not codec_private:
            raise RuntimeError("CodecPrivateData missing for ISM video stream")

        trak = build_video_trak(
            duration=duration,
            width=width,
            height=height,
            codec=(codec or "").lower(),
            codec_private=codec_private,
            kid_bytes=kid_bytes,
            track_id=track_id,
            timescale=timescale,
            encrypted=encrypted,
        )

    elif stream_type == "audio":
        audio_codec = (codec or "").lower()
        needs_private = audio_codec not in ("ec-3", "eac3", "ac-3", "ac3")
        if needs_private and not codec_private:
            raise RuntimeError("CodecPrivateData missing for ISM audio stream")

        trak = build_audio_trak(
            duration=duration,
            codec=(codec or "").lower(),
            codec_private=codec_private,
            sample_rate=sample_rate,
            channels=channels,
            kid_bytes=kid_bytes,
            language=language,
            track_id=track_id,
            timescale=timescale,
            encrypted=encrypted,
        )
    else:
        raise ValueError(f"Unsupported ISM stream type: {stream_type!r}")

    pssh_blocks = b""
    if pro_bytes:
        try:
            pssh_blocks += make_pssh_playready(pro_bytes)
        except Exception as exc:
            logger.debug(f"Skipping PlayReady pssh: {exc}")
    try:
        pssh_blocks += make_pssh_widevine(kid_bytes)
    except Exception as exc:
        logger.debug(f"Skipping Widevine pssh: {exc}")

    mvhd = make_mvhd(duration, timescale=timescale)
    mvex = build_mvex(duration, track_id=track_id)
    moov = make_box(b"moov", mvhd + trak + mvex + pssh_blocks)
    return make_ftyp() + moov
