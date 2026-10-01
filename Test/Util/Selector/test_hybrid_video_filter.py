# 28.09.26
# ruff: noqa: E402

import logging
import sys
from pathlib import Path

workspace_root = Path(__file__).parent.parent.parent.parent
sys.path.insert(0, str(workspace_root))

from mock_streams import MockStream

from VibraVid.core.utils.selector import StreamSelector


def test_hybrid_all_dv_pool_picks_best_dv_no_companion():
    """Ladder Amazon/MAX-style: tutte le rendition sono gia' DV profile 5. Con select_video="hybrid"
    non esiste un'alternativa HDR separata, quindi si seleziona la DV migliore e basta, senza companion."""
    streams = [
        MockStream(type="video", height=288, codecs="dvhe.05.01", bitrate=150_000, id="v150k"),
        MockStream(type="video", height=1080, codecs="dvhe.05.01", bitrate=1_800_000, id="v1800k"),
        MockStream(type="video", height=2160, codecs="dvhe.05.01", bitrate=20_000_000, id="v20m"),
        MockStream(type="audio", language="ita", resolved_language="it-IT", codecs="ec-3", bitrate=256_000, id="a_ita"),
    ]
    StreamSelector("hybrid", "ita", "false").apply(streams)
    assert [s for s in streams if s.dv_companion] == []
    assert [s for s in streams if s.type == "video" and s.selected][0].id == "v20m"


def test_hybrid_two_pools_hdr_and_dv_picks_hdr_base_and_dv_companion():
    """Due 'manifest' distinti (uno solo HDR10, uno solo DV) uniti in un unico pool, come fa
    Generic_Downloader._select() con piu' sorgenti -- select_video="hybrid" deve prendere l'HDR10
    come primaria e l'unica DV disponibile come companion, senza bisogno di role per-sorgente."""
    hdr_only = [
        MockStream(type="video", height=1080, codecs="hvc1.2.4.L120.B0", bitrate=8_000_000, video_range="HDR10", id="hdr1080"),
    ]
    dv_only = [
        MockStream(type="video", height=1080, codecs="dvhe.05.06", bitrate=2_500_000, video_range="DV", id="dv1080"),
    ]
    streams = hdr_only + dv_only
    StreamSelector("hybrid", "false", "false").apply(streams)
    assert [s for s in streams if s.type == "video" and s.selected][0].id == "hdr1080"
    companions = [s for s in streams if s.dv_companion]
    assert [s.id for s in companions] == ["dv1080"]


def test_hybrid_falls_back_to_best_when_no_dv_available(caplog):
    """Nessuna traccia DV nel pool: select_video="hybrid" deve ripiegare sulla selezione best
    normale (senza companion) e loggare un warning, senza sollevare eccezioni."""
    streams = [
        MockStream(type="video", height=720, codecs="hvc1", bitrate=2_000_000, video_range="HDR10", id="hdr720"),
        MockStream(type="video", height=1080, codecs="hvc1", bitrate=8_000_000, video_range="HDR10", id="hdr1080"),
    ]
    with caplog.at_level(logging.WARNING):
        StreamSelector("hybrid", "false", "false").apply(streams)
    assert [s for s in streams if s.dv_companion] == []
    assert [s for s in streams if s.type == "video" and s.selected][0].id == "hdr1080"
    assert any("no Dolby Vision stream available" in r.message for r in caplog.records)


def _dragons_s03e06_ladder():
    """House of the Dragon S03E06 'Unbowed and Unbent' (HBO Max), top-tier bitrates: DV vince
    davvero per bitrate (dragons-s03e01-06_HMAX json reale)."""
    return [
        MockStream(type="video", height=1080, codecs="hvc1.2.4.L153.B0", bitrate=23_748_550, video_range="SDR", id="sdr"),
        MockStream(type="video", height=1080, codecs="hvc1.2.4.L153.B0", bitrate=24_895_017, video_range="HDR10", id="hdr10"),
        MockStream(type="video", height=1080, codecs="dvhe.05.06", bitrate=29_657_419, video_range="DV", id="dv"),
    ]


def test_best_picks_dv_outright_when_it_has_highest_bitrate():
    """select_video="best" ora sceglie letteralmente il bitrate/risoluzione piu' alto: se la DV
    vince davvero (come nel ladder reale S03E06), deve essere selezionata come primaria."""
    streams = _dragons_s03e06_ladder()
    StreamSelector("best", "false", "false", dv_auto=True).apply(streams)
    video = [s for s in streams if s.type == "video" and s.selected]
    assert len(video) == 1
    assert video[0].id == "dv"
    # e' gia' native DV (profile 5): dv_auto non deve creare un companion inutile
    assert [s for s in streams if s.dv_companion] == []


def _dragons_s03e01_ladder():
    """House of the Dragon S03E01 (HBO Max), top-tier bitrates: HDR10 ('DV P8 Hybrid') vince
    per bitrate (dragons-s03e01-06_HMAX json reale)."""
    return [
        MockStream(type="video", height=1920, codecs="hvc1.2.4.L153.B0", bitrate=18_740_000, video_range="SDR", id="sdr"),
        MockStream(type="video", height=1920, codecs="hvc1.2.4.L153.B0", bitrate=23_650_000, video_range="HDR10", id="hdr10"),
        MockStream(type="video", height=1920, codecs="dvhe.05.06", bitrate=22_600_000, video_range="DV", id="dv"),
    ]


def test_best_still_picks_hdr10_when_it_wins_and_dv_auto_still_attaches_companion():
    """Quando HDR10 ha davvero il bitrate piu' alto, "best" continua a prenderla -- e il
    comportamento esistente di dv_auto (companion automatico) resta invariato."""
    streams = _dragons_s03e01_ladder()
    StreamSelector("best", "false", "false", dv_auto=True).apply(streams)
    video = [s for s in streams if s.type == "video" and s.selected]
    assert len(video) == 1
    assert video[0].id == "hdr10"
    companions = [s for s in streams if s.dv_companion]
    assert [s.id for s in companions] == ["dv"]
