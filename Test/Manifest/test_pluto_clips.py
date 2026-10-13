# 13.10.26
# ruff: noqa: E402

import sys
from pathlib import Path

workspace_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(workspace_root))

from VibraVid.services.plutotv.client import clean_hls_master, is_bumper, pick_clip_sources

_FIXTURE = Path(__file__).parent / "fixtures" / "pluto_clip_master.m3u8"
_SILO = "https://siloh.pluto.tv/497_Sony_Pictures/clip/6273811ac514110013447cf9_The_Jeffersons_IT_S01E101/720pDRM/20250410_021642"
_BUMPER_SILO = "https://siloh.pluto.tv/196_Pluto_TV_OandO/clip/56c2dcd7cc39dc6567615245_ptv_7423_ad_bumper_animation_orbit_15/1080pDRM/20251117_205303"


def _clip(name: str, base: str) -> dict:
    return {
        "name": name,
        "sources": [
            {"type": "DASH", "file": f"{base}/dash/0-end/main.mpd"},
            {"type": "HLS", "file": f"{base}/hls/0-end/master.m3u8"},
        ],
    }


def test_is_bumper_matches_real_promo_names():
    assert is_bumper("Dancing Astronaut 15s v1 - Promo")
    assert is_bumper("Exploding Logo 5s v2 - Promo")
    assert is_bumper("ptv_7423_ad_bumper_animation_orbit")
    assert is_bumper("Pluto_TV_OandO intro")


def test_is_bumper_keeps_real_titles():
    assert not is_bumper("The Jeffersons IT: Un'amica in Difficoltà (S1E101)")
    assert not is_bumper("Starting Up Love IT")
    assert not is_bumper("Mad Max: Ad Astra")
    assert not is_bumper("")


def test_pick_clip_skips_bumpers_and_rewrites_silo():
    clips = [
        _clip("Dancing Astronaut 15s v1 - Promo", _BUMPER_SILO),
        _clip("The Jeffersons IT: Un'amica in Difficoltà (S1E101)", _SILO),
        _clip("Exploding Logo 5s v2 - Promo", _BUMPER_SILO),
    ]
    sources = pick_clip_sources(clips)
    origin = _SILO.replace("https://siloh.pluto.tv", "http://silo-hybrik.pluto.tv.s3.amazonaws.com")
    assert sources == {"dash": f"{origin}/dash/0-end/main.mpd", "hls": f"{origin}/hls/0-end/master.m3u8"}


def test_pick_clip_none_when_only_bumpers_or_no_sources():
    assert pick_clip_sources([_clip("Exploding Logo 5s v2 - Promo", _BUMPER_SILO)]) is None
    assert pick_clip_sources([{"name": "Feature", "sources": []}]) is None
    assert pick_clip_sources([]) is None
    assert pick_clip_sources(None) is None


def test_pick_clip_partial_sources():
    sources = pick_clip_sources([{"name": "Feature", "sources": [{"type": "HLS", "file": f"{_SILO}/hls/master.m3u8"}]}])
    assert sources["dash"] is None
    assert sources["hls"].startswith("http://silo-hybrik.pluto.tv.s3.amazonaws.com/")


def test_clean_hls_master_drops_fairplay_variants():
    original = _FIXTURE.read_text(encoding="utf-8")
    assert original.count("fp/hls_") == 5

    cleaned = clean_hls_master(original)
    uris = [line.strip() for line in cleaned.splitlines() if line.strip() and not line.startswith("#")]
    assert uris == ["hls_300.m3u8", "hls_600.m3u8", "hls_1000.m3u8", "hls_1600.m3u8", "hls_2400.m3u8"]
    assert "fairplay" not in cleaned
    assert cleaned.count("#EXT-X-STREAM-INF") == 5


def test_clean_hls_master_without_fairplay_is_unchanged():
    text = "#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=1\nhls_300.m3u8\n"
    assert clean_hls_master(text) == text


def test_get_headers_is_not_shared():
    """Per-request headers (Pluto's search bearer) must not leak into later requests (S3 clip -> 403)."""
    from VibraVid.utils.http_client import get_headers

    first = get_headers()
    first["authorization"] = "Bearer leaked"
    assert "authorization" not in get_headers()
