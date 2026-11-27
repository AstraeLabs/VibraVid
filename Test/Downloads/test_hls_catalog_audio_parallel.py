# 27.11.26

"""Audio taken from another catalog's playlist downloads with the main tracks, not in a second pass after them."""

from types import SimpleNamespace

from VibraVid.core.downloader.hls import HLS_Downloader
from VibraVid.core.manifest.stream import Stream


def _downloader(other_tracks):
    dl = HLS_Downloader.__new__(HLS_Downloader)
    dl.other_tracks = list(other_tracks)
    fake_row = Stream(type="audio", language="ja", selected=True, is_external=True)
    fake_row.id = "EXT"
    dl.media_downloader = SimpleNamespace(
        streams=[Stream(type="video", selected=True), fake_row],
        other_tracks=list(other_tracks),
        external_other_tracks=[dict(t) for t in other_tracks if t["type"].startswith("audio")],
    )
    return dl


def test_single_audio_track_becomes_a_stream_of_the_main_download():
    jpn = {"type": "audio", "language": "ja", "url": "https://vix/jpn_480p.m3u8?token=x", "headers": {}, "single_audio": True}
    dv = {"type": "video:dv", "url": "https://cdn/dv.m3u8"}
    dl = _downloader([jpn, dv])
    dl._promote_single_audio_tracks()

    audio = [s for s in dl.media_downloader.streams if s.type == "audio"]
    assert len(audio) == 1  # the table's external placeholder is replaced, not duplicated
    stream = audio[0]
    assert (stream.language, stream.selected, stream.is_external, stream.playlist_url, stream.format) == (
        "ja", True, False, "https://vix/jpn_480p.m3u8?token=x", "hls")
    # nothing left for the second pass, the other kinds of track keep using it
    assert dl.other_tracks == [dv] and dl.media_downloader.other_tracks == [dv]
    assert dl.media_downloader.external_other_tracks == []


def test_audio_tracks_without_single_audio_keep_the_second_pass():
    plain = {"type": "audio", "language": "en", "url": "https://cdn/en.m3u8"}
    dl = _downloader([plain])
    dl._promote_single_audio_tracks()
    assert dl.other_tracks == [plain]
    assert [s.type for s in dl.media_downloader.streams if not s.is_external] == ["video"]
