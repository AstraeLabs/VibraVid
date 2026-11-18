# 23.10.26

"""A single-file subtitle (no segments) must not switch off live decryption of the video/audio it is downloaded with."""

from types import SimpleNamespace

from VibraVid.core.velora.downloader import MediaDownloader

all_live = MediaDownloader.all_support_live_decryption


def _s(kind, live):
    return SimpleNamespace(type=kind, supports_live_decryption=live)


def test_single_file_subtitle_does_not_veto_video_and_audio():
    assert all_live([_s("video", True), _s("audio", True), _s("subtitle", False)])


def test_a_non_segmented_video_or_audio_still_vetoes():
    assert not all_live([_s("video", False), _s("subtitle", True)])
    assert not all_live([_s("video", True), _s("audio", False)])


def test_nothing_to_decrypt_live_without_video_or_audio():
    assert not all_live([])
    assert not all_live([_s("subtitle", True)])


def test_a_stream_without_the_flag_counts_as_not_live():
    assert not all_live([SimpleNamespace(type="video")])
