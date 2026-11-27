# 27.11.26

"""AnimeUnity: each dub is its own video; the requested languages become one file with one video and every audio."""

from types import SimpleNamespace
from unittest import mock

import pytest

from VibraVid.services.animeunity import audio
from VibraVid.services.animeunity import downloader as au
from VibraVid.services.animeunity import scrapper


@pytest.mark.parametrize(
    "languages, wanted, expected",
    [
        (["ita", "jpn"], ["it", "en"], ("ita", [])),  # default config: unchanged, Italian only
        (["ita", "jpn"], ["it", "ja"], ("ita", ["jpn"])),  # Italian video + Japanese audio
        (["jpn", "ita"], ["it", "ja"], ("ita", ["jpn"])),  # the requested order decides the video, not the site order
        (["ita", "jpn"], ["ja", "it"], ("jpn", ["ita"])),
        (["ita", "jpn"], None, ("ita", ["jpn"])),  # "all": Italian video, every other audio
        (["jpn"], ["it", "ja"], ("jpn", [])),
        (["jpn"], ["it"], ("jpn", [])),  # nothing requested exists: the only video
        (["ita", "jpn"], [], ("ita", [])),  # filter naming no language
        ([], ["it"], (None, [])),
    ],
)
def test_plan_audio(languages, wanted, expected):
    assert audio.plan_audio(languages, wanted) == expected


@pytest.mark.parametrize("raw, expected", [("ita|jpn", ["it", "ja"]), ("all", None), ("ita|eng", ["it", "en"])])
def test_wanted_audio_languages_reads_gui_choice_then_config(monkeypatch, raw, expected):
    monkeypatch.setattr(audio.context_tracker, "audio_filter", None, raising=False)
    monkeypatch.setattr(audio.config_manager.config, "get", lambda *a, **k: raw)
    assert audio.wanted_audio_languages() == expected
    monkeypatch.setattr(audio.context_tracker, "audio_filter", "ja", raising=False)
    assert audio.wanted_audio_languages() == ["ja"]


def test_episode_videos_and_embed_url(monkeypatch):
    page = '<iframe src="https://vixcloud.cc/embed/790573?token=abc&amp;expires=1&amp;canPlayFHD=1"></iframe>'
    requested = []

    class FakeClient:
        def __init__(self, **_): pass
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def get(self, url):
            requested.append(url)
            return SimpleNamespace(text=page, raise_for_status=lambda: None)

    monkeypatch.setattr(scrapper, "create_client", FakeClient)
    scrape = scrapper.ScrapeSerieAnime("https://site.example/")
    scrape.setup(None, 7808, "jojo")
    scrape.episodes_cache = [{"id": 89502, "videos": [{"id": 110298, "language": "ita"}, {"id": 110297, "language": "jpn"}]}]
    assert scrape.get_episode_videos(0) == {"ita": 110298, "jpn": 110297}
    assert scrape.get_episode_videos(5) == {}
    assert scrape.get_embed_url(110297) == "https://vixcloud.cc/embed/790573?token=abc&expires=1&canPlayFHD=1"
    assert requested == ["https://site.example/anime/7808-jojo/110297"]


def _run_episode(monkeypatch, wanted, manual=False):
    scrape = SimpleNamespace(
        series_name="jojo", is_series=True, url="https://site.example", media_id=7808,
        series_identity=("JoJo (2026)", None),
        get_episode_videos=lambda _i: {"ita": 110298, "jpn": 110297},
        get_video_url=mock.MagicMock(return_value="https://cdn.example/ita.mp4"),
    )
    playlists = {110298: ("https://vix/ita.m3u8", {"Referer": "ita"}), 110297: ("https://vix/jpn.m3u8", {"Referer": "jpn"})}
    hls, mp4 = mock.MagicMock(), mock.MagicMock(return_value="mp4")
    monkeypatch.setattr(au, "wanted_audio_languages", lambda: wanted)
    monkeypatch.setattr(au, "_vixcloud_playlist", lambda _s, video_id: playlists[video_id])
    monkeypatch.setattr(au, "lightest_rendition", lambda master, _h: master.replace(".m3u8", "_480p.m3u8"))
    monkeypatch.setattr(au, "HLS_Downloader", hls)
    monkeypatch.setattr(au, "MP4_Downloader", mp4)
    monkeypatch.setattr(au, "map_episode_path", lambda **_: ([], "ep"))
    monkeypatch.setattr(au, "anime_folder", lambda *_: "out")
    monkeypatch.setattr(au, "start_message", lambda: None)
    monkeypatch.setattr(au, "print_episode_header", lambda *a, **k: None)
    monkeypatch.setattr(au.os_manager, "get_sanitize_path", lambda p: p)
    monkeypatch.setattr(au.os_manager, "create_path", lambda *_: None)
    monkeypatch.setattr(au.context_tracker, "manual_tracks", manual, raising=False)
    au.download_episode(SimpleNamespace(id=89502, number="1"), 0, scrape, mock.MagicMock())
    return scrape, hls, mp4


def test_ita_and_jpn_give_italian_video_with_japanese_audio(monkeypatch):
    scrape, hls, mp4 = _run_episode(monkeypatch, ["it", "ja"])
    kwargs = hls.call_args.kwargs
    assert kwargs["m3u8_url"] == "https://vix/ita.m3u8" and kwargs["headers"] == {"Referer": "ita"}
    assert kwargs["other_tracks"] == [{"type": "audio", "language": "ja", "url": "https://vix/jpn_480p.m3u8",
                                       "headers": {"Referer": "jpn"}, "single_audio": True}]
    assert kwargs["embedded_audio_language"] == "it" and kwargs["required_audio_languages"] == ["it", "ja"]
    mp4.assert_not_called()
    scrape.get_video_url.assert_not_called()


def test_single_language_keeps_the_direct_mp4(monkeypatch):
    scrape, hls, mp4 = _run_episode(monkeypatch, ["it", "en"])
    hls.assert_not_called()
    mp4.assert_called_once()
    scrape.get_video_url.assert_called_once_with(89502, prefer_language="ita")


def test_only_japanese_requested_downloads_the_japanese_mp4(monkeypatch):
    scrape, hls, _mp4 = _run_episode(monkeypatch, ["ja"])
    hls.assert_not_called()
    scrape.get_video_url.assert_called_once_with(89502, prefer_language="jpn")


def test_manual_track_mode_keeps_one_language(monkeypatch):
    _scrape, hls, mp4 = _run_episode(monkeypatch, ["it", "ja"], manual=True)
    hls.assert_not_called()
    mp4.assert_called_once()
