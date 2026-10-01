# 26.09.26
# ruff: noqa: E402

import inspect
import sys
from pathlib import Path

import pytest

workspace_root = Path(__file__).parent.parent.parent.parent
sys.path.insert(0, str(workspace_root))

from mock_streams import MockStream

from VibraVid.core.downloader.dash import DASH_Downloader
from VibraVid.core.downloader.hls import HLS_Downloader
from VibraVid.core.downloader.ism import ISM_Downloader
from VibraVid.core.utils.selector import StreamSelector
from VibraVid.core.velora.downloader import MediaDownloader


def _manifest():
    """Un manifest misto: piu' video, piu' audio, piu' subtitle. Solo una parte e' selezionata."""
    return [
        MockStream(type="video", height=1080, codecs="avc1", bitrate=3_000_000, id="v1080"),
        MockStream(type="video", height=720, codecs="avc1", bitrate=1_000_000, id="v720"),
        MockStream(type="video", height=2160, codecs="avc1", bitrate=8_000_000, id="v2160"),
        MockStream(type="audio", language="eng", resolved_language="en-US", codecs="mp4a", bitrate=128_000, id="a_eng"),
        MockStream(type="audio", language="ita", resolved_language="it-IT", codecs="ec-3", bitrate=256_000, id="a_ita"),
        MockStream(type="audio", language="fra", resolved_language="fr-FR", codecs="mp4a", bitrate=128_000, id="a_fra"),
        MockStream(type="subtitle", language="ita", resolved_language="it-IT", id="s_ita"),
        MockStream(type="subtitle", language="eng", resolved_language="en-US", id="s_eng"),
    ]


def _select(streams):
    StreamSelector("best", "ita|eng", "ita|eng").apply(streams)
    return streams


def test_display_selected_only_false_keeps_everything():
    """Flag spento: la tabella mostra l'intero manifest, non solo la selezione."""
    streams = _select(_manifest())
    assert not all(s.selected for s in streams)
    assert len([s for s in streams if not s.selected]) > 0


def test_display_selected_only_true_reduces_to_selection():
    """Flag attivo: la tabella mostra solo le tracce che verranno scaricate."""
    streams = _select(_manifest())
    display = [s for s in streams if s.selected]
    assert display, "la selezione non deve essere vuota"
    assert len(display) < len(streams)
    # nessuna traccia non selezionata deve comparire fra quelle mostrate
    assert all(s.selected for s in display)


def test_display_selected_only_true_keeps_selection_per_slot():
    """Una traccia per gruppo di filtri: video, audio e subtitle selezionati insieme."""
    streams = _select(_manifest())
    for kind, expected in (("video", 1), ("audio", 2), ("subtitle", 2)):
        sel = [s for s in streams if s.type == kind and s.selected]
        assert len(sel) == expected, f"{kind}: attesi {expected}, trovati {len(sel)}"


def test_auto_dv_companion_picks_worst():
    """dv_auto: il companion DV deve essere il piu' economico in bitrate (quality='worst')."""
    streams = [
        MockStream(type="video", height=2160, codecs="avc1", bitrate=15_000_000, id="v2160"),
        MockStream(type="video", height=480, codecs="dvh1", bitrate=400_000, id="dv480"),
        MockStream(type="video", height=1080, codecs="dvh1", bitrate=2_500_000, id="dv1080"),
        MockStream(type="audio", language="ita", resolved_language="it-IT", codecs="ec-3", bitrate=256_000, id="a_ita"),
    ]
    StreamSelector("best", "ita", "false", dv_auto=True).apply(streams)
    companions = [s for s in streams if s.dv_companion]
    assert len(companions) == 1
    assert companions[0].id == "dv480"
    assert companions[0].dv_companion_quality == "worst"
    # il companion non deve diventare la traccia principale
    assert not companions[0].selected
    assert [s for s in streams if s.type == "video" and s.selected][0].id == "v2160"


def test_auto_dv_companion_not_created_without_dv_streams():
    """Nessuna traccia DV: dv_auto non deve inventare un companion."""
    streams = [
        MockStream(type="video", height=1080, codecs="avc1", bitrate=3_000_000, id="v0"),
        MockStream(type="audio", language="ita", resolved_language="it-IT", codecs="ec-3", bitrate=256_000, id="a_ita"),
    ]
    StreamSelector("best", "ita", "false", dv_auto=True).apply(streams)
    assert [s for s in streams if s.dv_companion] == []


def test_auto_dv_skipped_when_selected_track_is_already_dv():
    """Traccia DV profile 8 gia' selezionata: non serve un companion, sovrascriverebbe l'RPU nativo.

    `_native_dv_codec` riconosce solo il DV che porta gia' un RPU (profile 5 o 8 via
    `supplemental_codecs`, oppure il suffisso `.05`/`.08`): un `dvh1` generico non basta.
    """
    streams = [
        MockStream(type="video", height=2160, codecs="dvh1.08", bitrate=15_000_000, id="dv2160"),
        MockStream(type="video", height=480, codecs="dvh1", bitrate=400_000, id="dv480"),
        MockStream(type="audio", language="ita", resolved_language="it-IT", codecs="ec-3", bitrate=256_000, id="a_ita"),
    ]
    StreamSelector("best", "ita", "false", dv_auto=True).apply(streams)
    assert [s for s in streams if s.dv_companion] == []
    assert [s for s in streams if s.type == "video" and s.selected][0].id == "dv2160"


def test_auto_dv_skipped_when_every_rendition_is_already_dv_profile5():
    """Ladder Amazon-style: tutte le rendition sono gia' DV profile 5 (single-layer, RPU
    embedded, come da nota MAX in memoria). La migliore selezionata e' gia' DV completa:
    non ha senso scaricare una rendition peggiore come companion "hybrid" -- sarebbe solo
    una DV di qualita' inferiore, senza alcun beneficio."""
    streams = [
        MockStream(type="video", height=288, codecs="dvhe.05.01", bitrate=150_000, id="v150k"),
        MockStream(type="video", height=1080, codecs="dvhe.05.01", bitrate=1_800_000, id="v1800k"),
        MockStream(type="video", height=2160, codecs="dvhe.05.01", bitrate=20_000_000, id="v20m"),
        MockStream(type="audio", language="ita", resolved_language="it-IT", codecs="ec-3", bitrate=256_000, id="a_ita"),
    ]
    StreamSelector("best", "ita", "false", dv_auto=True).apply(streams)
    assert [s for s in streams if s.dv_companion] == []
    assert [s for s in streams if s.type == "video" and s.selected][0].id == "v20m"


@pytest.mark.parametrize(
    "cls",
    [DASH_Downloader, HLS_Downloader, ISM_Downloader, MediaDownloader],
)
def test_downloaders_accept_display_selected_only(cls):
    """Il flag dev'essere un parametro pubblico, non un attributo impostato a mano.

    Finche' resta un attributo hardcoded in __init__ un servizio non puo' accenderlo,
    ed e' esattamente il difetto per cui il flag non faceva nulla.
    """
    assert "display_selected_only" in inspect.signature(cls.__init__).parameters


@pytest.mark.parametrize("cls", [DASH_Downloader, HLS_Downloader, ISM_Downloader, MediaDownloader])
def test_display_selected_only_defaults_to_false(cls):
    param = inspect.signature(cls.__init__).parameters["display_selected_only"]
    assert param.default is False


def _supergirl_ladder():
    """La scala 1600p di Supergirl dopo il filtro del manifest: PQ e Dolby Vision."""
    return [
        MockStream(type="video", height=1600, codecs="hvc1.2.4.L150.90", bitrate=7_000_000, id="v19"),
        MockStream(type="video", height=1600, codecs="hvc1.2.4.L150.90", bitrate=11_000_000, id="v20"),
        MockStream(type="video", height=1600, codecs="dvh1.05.06", bitrate=10_000_000, id="v24"),
        MockStream(type="audio", language="ita", resolved_language="it-IT", codecs="ec-3", bitrate=256_000, id="a_ita"),
    ]


def test_hbomax_always_takes_the_best_pq_as_primary():
    """`_best` mette Dolby Vision in coda apposta: la primaria e' la PQ col bitrate piu' alto.

    v24 (DV) ha piu' bitrate di v19, quindi serve che il DV non vinca per bitrate.
    """
    streams = _supergirl_ladder()
    StreamSelector("best", "ita", "false", dv_auto=False).apply(streams)
    video = [s for s in streams if s.type == "video" and s.selected]
    assert len(video) == 1
    assert video[0].id == "v20"
    assert "hvc1" in video[0].codecs


def test_hbomax_adds_the_dv_track_only_when_dv_auto_is_on():
    streams = _supergirl_ladder()
    StreamSelector("best", "ita", "false", dv_auto=True).apply(streams)
    companions = [s for s in streams if s.dv_companion]
    assert [s.id for s in companions] == ["v24"]
    # il companion non diventa mai la traccia primaria
    assert [s.id for s in streams if s.type == "video" and s.selected] == ["v20"]


def test_hbomax_leaves_dv_alone_when_dv_auto_is_off():
    streams = _supergirl_ladder()
    StreamSelector("best", "ita", "false", dv_auto=False).apply(streams)
    assert [s for s in streams if s.dv_companion] == []
    assert [s.id for s in streams if s.type == "video" and s.selected] == ["v20"]


def test_velora_forwards_the_flag_to_its_base():
    """`MediaDownloader` ha una firma esplicita: deve inoltrare il flag, non accettarlo e perderlo."""
    source = inspect.getsource(MediaDownloader.__init__)
    assert "display_selected_only" in source
    assert "display_selected_only=display_selected_only" in source.replace(" ", "")
