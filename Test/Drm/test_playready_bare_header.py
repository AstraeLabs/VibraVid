# 24.10.26
# ruff: noqa: E402

"""A bare UTF-16LE WRMHEADER (no PSSH box, no PlayReady Object) loads as PlayReady DRM from DASH, HLS and ISM."""

import base64
import sys
from pathlib import Path
from uuid import UUID

import pytest

workspace_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(workspace_root))

from pyplayready.system.pssh import PSSH

from VibraVid.core.drm import playready
from VibraVid.core.drm.system import DRMType, _DRMSystems
from VibraVid.core.manifest.ism import ISMParser
from VibraVid.core.manifest.mpd import _NS, DashParser
from VibraVid.core.manifest.stream import DRMInfo

KID = UUID("00112233-4455-6677-8899-aabbccddeeff")
KID_B64 = base64.b64encode(KID.bytes_le).decode()
PR_URN = "urn:uuid:9a04f079-9840-4286-ab92-e65be0885f95"


def bare_header(version: str) -> str:
    """Base64 of a bare WRMHEADER, with the KID where the given header version puts it."""
    if version == "4.0.0.0":
        data = f"<PROTECTINFO><KEYLEN>16</KEYLEN><ALGID>AESCTR</ALGID></PROTECTINFO><KID>{KID_B64}</KID>"
    else:
        data = f'<PROTECTINFO><KIDS><KID ALGID="AESCTR" VALUE="{KID_B64}"></KID></KIDS></PROTECTINFO>'
    xml = (
        f'<WRMHEADER xmlns="http://schemas.microsoft.com/DRM/2007/03/PlayReadyHeader" version="{version}">'
        f"<DATA>{data}<LA_URL>https://example.invalid/pr</LA_URL></DATA></WRMHEADER>"
    )
    return base64.b64encode(xml.encode("utf-16-le")).decode()


def _mpd(child: str, value: str) -> str:
    return (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<MPD xmlns="urn:mpeg:dash:schema:mpd:2011" xmlns:cenc="urn:mpeg:cenc:2013" xmlns:mspr="urn:microsoft:playready" '
        'type="static" mediaPresentationDuration="PT10S"><Period><AdaptationSet contentType="video" mimeType="video/mp4">'
        f'<ContentProtection schemeIdUri="{PR_URN}"><{child}>{value}</{child}></ContentProtection>'
        '<Representation id="v" bandwidth="1000" codecs="avc1.640028" width="1280" height="720"><BaseURL>v.mp4</BaseURL>'
        '<SegmentList duration="10" timescale="1"><Initialization range="0-99"/><SegmentURL mediaRange="100-199"/></SegmentList>'
        "</Representation></AdaptationSet></Period></MPD>"
    )


@pytest.mark.parametrize("version", ["4.0.0.0", "4.1.0.0", "4.2.0.0", "4.3.0.0"])
def test_a_bare_header_is_wrapped_into_a_parsable_playready_object(version):
    wrapped = _DRMSystems.wrap_bare_playready_header(bare_header(version))

    assert PSSH(wrapped).wrm_headers
    assert _DRMSystems.extract_kids_from_playready_pro(wrapped) == [KID.hex]


@pytest.mark.parametrize("version", ["4.0.0.0", "4.3.0.0"])
@pytest.mark.parametrize("child", ["mspr:pro", "cenc:pssh"])
def test_dash_loads_a_bare_header(child, version):
    parser = DashParser("https://cdn.example/m.mpd", content=_mpd(child, bare_header(version)), quiet=True)
    parser.fetch_manifest()
    info = parser._extract_drm(parser._root.find(".//mpd:AdaptationSet", _NS))

    pssh = info.get_pssh_for(DRMType.PLAYREADY)
    assert pssh and PSSH(pssh).wrm_headers
    assert info.kid == KID.hex


def test_hls_style_registration_of_a_bare_header():
    info = DRMInfo()
    info.set_pssh(bare_header("4.0.0.0"), DRMType.PLAYREADY, key_uri="data:text/plain;charset=UTF-16;base64,x")

    pssh = info.get_pssh_for(DRMType.PLAYREADY)
    assert PSSH(pssh).wrm_headers and info.kid == KID.hex
    assert info.to_dict()["playready"][0]["pssh"] == pssh


def test_ism_loads_a_bare_header():
    xml = (
        '<SmoothStreamingMedia><Protection><ProtectionHeader SystemID="9A04F079-9840-4286-AB92-E65BE0885F95">'
        f"{bare_header('4.0.0.0')}</ProtectionHeader></Protection></SmoothStreamingMedia>"
    )
    import xml.etree.ElementTree as ET

    parser = ISMParser.__new__(ISMParser)
    info = parser._extract_drm(ET.fromstring(xml))

    assert PSSH(info.get_pssh_for(DRMType.PLAYREADY)).wrm_headers
    assert info.kid == KID.hex


def test_existing_playready_objects_and_garbage_are_returned_unchanged():
    wrapped = _DRMSystems.wrap_bare_playready_header(bare_header("4.0.0.0"))

    assert _DRMSystems.wrap_bare_playready_header(wrapped) == wrapped
    for data in (b"\x00\x00not a header", "<WRMHEADER/>".encode("utf-8"), b"<\x00a\x00"):
        b64 = base64.b64encode(data).decode()
        assert _DRMSystems.wrap_bare_playready_header(b64) == b64
    assert _DRMSystems.wrap_bare_playready_header("%%% not base64 %%%") == "%%% not base64 %%%"


def test_an_unparseable_playready_entry_does_not_discard_the_valid_ones(monkeypatch):
    from pyplayready.cdm import Cdm
    from pyplayready.device import Device

    seen = []

    class FakeCdm:
        def open(self):
            return "session"

        def close(self, session_id):
            pass

        def get_license_challenge(self, session_id, wrm_header):
            seen.append(wrm_header)
            return b"challenge"

    monkeypatch.setattr(Device, "load", classmethod(lambda cls, path: object()))
    monkeypatch.setattr(Cdm, "from_device", classmethod(lambda cls, device: FakeCdm()))
    monkeypatch.setattr(playready, "read_device_prd", lambda path: None)

    items = [
        {"pssh": base64.b64encode(b"\x00\x00not a header").decode(), "kid": "aa" * 16, "type": "PlayReady"},
        {"pssh": bare_header("4.0.0.0"), "kid": KID.hex, "type": "PlayReady"},
    ]
    playready._get_playready_keys_local_cdm(items, "https://license.test", "C:/binary/prd/x.prd", None, license_request_fn=lambda challenge, item: None)

    assert len(seen) == 1  # the bare header reached the CDM although the first entry could not be parsed
