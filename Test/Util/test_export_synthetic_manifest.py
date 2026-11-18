# 22.10.26

"""``--export`` for a service with no real manifest URL (Netflix ``custom://...``, Amazon Music ``amazon://...``): the links are kept."""

import json

import pytest

from VibraVid.core.downloader.util import _export
from VibraVid.core.manifest.stream import Segment, Stream
from VibraVid.remote.models import ResolvedMedia

MANIFEST = json.dumps({"vibravid_manifest": 1, "duration": 60, "tracks": []})


def _media(url="custom://cs/82010199", content=MANIFEST, **extra):
    return ResolvedMedia(kind="dash", url=url, manifest_content=content, key=["kid:key"], title="Fallout", media_type="TV",
                         season=1, episode=2, license_url="https://netflix.com/license", **extra)


def _streams():
    video = Stream(type="video", id="v1", resolution="1920x1080", codecs="hvc1", bitrate=5_000_000, selected=True, segments=[
        Segment(url="https://cdn.example/v1.mp4", number=0, seg_type="init", byte_range="0-999"),
        Segment(url="https://cdn.example/v1.mp4", number=1, seg_type="media"),
    ])
    audio = Stream(type="audio", id="a1", language="ita", codecs="mp4a", bitrate=128_000, selected=True, segments=[
        Segment(url="https://cdn.example/a1.mp4", number=1, seg_type="media"),
    ])
    other = Stream(type="video", id="v2", resolution="1280x720", selected=False, segments=[
        Segment(url="https://cdn.example/v2.mp4", number=1, seg_type="media"),
    ])
    return [video, audio, other]


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    monkeypatch.setattr(_export, "_written", [])


def test_only_a_made_up_url_with_a_manifest_is_synthetic():
    assert _export.is_synthetic(_media())
    assert _export.is_synthetic(_media(url="amazon://B0ABC.mpd"))
    assert not _export.is_synthetic(_media(url="https://cdn.example/manifest.mpd"))  # a real manifest is read again from its url
    assert not _export.is_synthetic(_media(content=None))


def test_track_links_lists_the_selected_streams_with_their_direct_url():
    video, audio = _export.track_links(_streams())  # the unselected 720p is left out

    assert video == {"type": "video", "resolution": "1920x1080", "codecs": "hvc1", "bitrate": 5_000_000, "url": "https://cdn.example/v1.mp4"}
    assert audio["language"] == "ita" and audio["url"] == "https://cdn.example/a1.mp4"


def test_track_links_carries_the_video_range_and_the_profile_of_a_video():
    stream = Stream(type="video", id="v1", resolution="1920x1080", video_range="DV", name="hevc-dv5-main10-L40-dash-cenc-prk-do", selected=True,
                    segments=[Segment(url="https://cdn.example/v1.mp4", number=1, seg_type="media")])
    (video,) = _export.track_links([stream])

    assert video["video_range"] == "DV" and video["profile"] == "hevc-dv5-main10-L40-dash-cenc-prk-do"


def test_export_shows_the_track_link_in_one_file_without_the_manifest(tmp_path):
    media = _media(tracks=_export.track_links(_streams()))
    target = tmp_path / "netflix.json"

    assert _export.write_export(media, str(target)) == str(target)

    [entry] = json.loads(target.read_text(encoding="utf-8"))
    assert entry["url"] == "https://cdn.example/v1.mp4"  # the video link, not custom://cs/...
    assert [t["url"] for t in entry["tracks"]] == ["https://cdn.example/v1.mp4", "https://cdn.example/a1.mp4"]
    assert "cmd" not in entry and "license_url" not in entry and "manifest_content" not in entry
    assert all("id" not in t and "segments" not in t for t in entry["tracks"])
    assert [p.name for p in tmp_path.iterdir()] == ["netflix.json"]  # nothing else is written next to it


def test_music_shows_the_audio_link(tmp_path):
    audio_only = [s for s in _streams() if s.type == "audio"]
    media = _media(url="amazon://B0ABC.mpd", tracks=_export.track_links(audio_only))
    target = tmp_path / "album.json"

    _export.write_export(media, str(target))

    [entry] = json.loads(target.read_text(encoding="utf-8"))
    assert entry["url"] == "https://cdn.example/a1.mp4"


def test_without_tracks_the_made_up_url_is_kept(tmp_path):
    target = tmp_path / "x.json"
    _export.write_export(_media(), str(target))

    [entry] = json.loads(target.read_text(encoding="utf-8"))
    assert entry["url"] == "custom://cs/82010199" and "manifest_content" not in entry


def test_several_entries_go_in_the_same_file(tmp_path):
    target = tmp_path / "album.json"
    _export.write_export(_media(url="amazon://1.mpd", tracks=[{"type": "audio", "url": "https://cdn.example/1.flac"}]), str(target))
    _export.write_export(_media(url="amazon://2.mpd", tracks=[{"type": "audio", "url": "https://cdn.example/2.flac"}]), str(target))

    entries = json.loads(target.read_text(encoding="utf-8"))
    assert [e["url"] for e in entries] == ["https://cdn.example/1.flac", "https://cdn.example/2.flac"]
    assert [p.name for p in tmp_path.iterdir()] == ["album.json"]


def test_a_real_manifest_url_is_exported_as_before(tmp_path):
    media = _media(url="https://cdn.example/manifest.mpd")
    target = tmp_path / "plain.json"

    _export.write_export(media, str(target))

    [entry] = json.loads(target.read_text(encoding="utf-8"))
    assert "manifest_content" not in entry and "tracks" not in entry
    assert "--down 'https://cdn.example/manifest.mpd'" in entry["cmd"]
    assert [p.name for p in tmp_path.iterdir()] == ["plain.json"]
