# 26.09.26
# ruff: noqa: E402

import sys
from pathlib import Path

workspace_root = Path(__file__).parent.parent.parent.parent
sys.path.insert(0, str(workspace_root))

from mock_streams import MockStream

from VibraVid.core.utils.selector import StreamSelector, _is_unmuxable_audio


def _dtsx_audio(id_="a_dtsx"):
    return MockStream(type="audio", language="eng", resolved_language="en-US", codecs="dtsx", bitrate=768_000, id=id_)


def _eac3_atmos_audio(id_="atmos:eng"):
    # E-AC3 Atmos: CODECS dichiarato ec-3, id con prefisso "atmos" -> non deve mai essere scartata
    return MockStream(type="audio", language="eng", resolved_language="en-US", codecs="ec-3", bitrate=256_000, id=id_)


def _plain_audio(id_="a0"):
    return MockStream(type="audio", language="eng", resolved_language="en-US", codecs="mp4a", bitrate=128_000, id=id_)


def _video(id_="v0"):
    return MockStream(type="video", height=1080, codecs="avc1", bitrate=3_000_000, id=id_)


def _selected_audio(streams):
    return [s for s in streams if s.type == "audio" and s.selected]


def _selected_video(streams):
    return [s for s in streams if s.type == "video" and s.selected]


def test_dtsx_dropped_by_default():
    """mux_dtsx default (False) mantiene il comportamento attuale: DTS:X scartato."""
    s = _dtsx_audio()
    assert _is_unmuxable_audio(s, mux_dtsx=False) is True


def test_dtsx_kept_when_flag_enabled():
    s = _dtsx_audio()
    assert _is_unmuxable_audio(s, mux_dtsx=True) is False


def test_eac3_atmos_id_never_dropped_regardless_of_flag():
    """Falso positivo storico: id 'atmos:*' con CODECS=ec-3 non e' DTS:X e va sempre mantenuto."""
    s = _eac3_atmos_audio()
    assert _is_unmuxable_audio(s, mux_dtsx=False) is False
    assert _is_unmuxable_audio(s, mux_dtsx=True) is False


def test_dts_x_id_fallback_without_codecs_respects_flag():
    """Manifest senza CODECS sulla traccia audio (es. Disney): fallback sull'id."""
    s = MockStream(type="audio", language="eng", resolved_language="en-US", codecs=None, id="dts-x:eng")
    assert _is_unmuxable_audio(s, mux_dtsx=False) is True
    assert _is_unmuxable_audio(s, mux_dtsx=True) is False


def test_title_with_only_dtsx_has_no_audio_when_flag_disabled():
    streams = [_video(), _dtsx_audio()]
    selector = StreamSelector("best", "best", "false", mux_dtsx=False)
    selector.apply(streams)
    assert _selected_audio(streams) == []
    assert len(_selected_video(streams)) == 1


def test_title_with_only_dtsx_has_audio_when_flag_enabled():
    streams = [_video(), _dtsx_audio()]
    selector = StreamSelector("best", "best", "false", mux_dtsx=True)
    selector.apply(streams)
    assert len(_selected_audio(streams)) == 1
    assert _selected_audio(streams)[0].codecs == "dtsx"


def test_video_streams_unaffected_by_dtsx_filter():
    for flag in (False, True):
        streams = [_video(), _dtsx_audio()]
        selector = StreamSelector("best", "best", "false", mux_dtsx=flag)
        selector.apply(streams)
        assert len(_selected_video(streams)) == 1


def test_other_audio_codecs_unaffected_by_dtsx_filter():
    for flag in (False, True):
        streams = [_video(), _plain_audio()]
        selector = StreamSelector("best", "best", "false", mux_dtsx=flag)
        selector.apply(streams)
        assert len(_selected_audio(streams)) == 1
        assert _selected_audio(streams)[0].codecs == "mp4a"


def test_mixed_audio_dtsx_dropped_others_kept_regardless_of_flag():
    """Con piu' tracce audio disponibili, il filtro dtsx coesiste con la normale selezione per lingua/bitrate."""
    streams = [_video(), _dtsx_audio(id_="a_dtsx"), _plain_audio(id_="a_plain")]
    selector = StreamSelector("best", "all", "false", mux_dtsx=False)
    selector.apply(streams)
    selected_ids = {s.id for s in _selected_audio(streams)}
    assert "a_dtsx" not in selected_ids
    assert "a_plain" in selected_ids
