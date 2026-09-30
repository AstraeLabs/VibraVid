import VibraVid.core.velora._decrypt_pipeline.pipeline as pipeline
from VibraVid.core.downloader.base import BaseDownloader, DownloadResult


def test_missing_segment_guard_rejects_media_loss_above_limit(monkeypatch):
    monkeypatch.setattr(pipeline, "MAX_MISSING_SEGMENT_RATIO", 0.05)

    error = pipeline._missing_segment_failure_message(
        "H.264 1080p",
        "video",
        4,
        73,
    )

    assert error is not None
    assert "4/73" in error
    assert "5.5% > 5.0% limit" in error


def test_missing_segment_guard_allows_media_loss_within_limit(monkeypatch):
    monkeypatch.setattr(pipeline, "MAX_MISSING_SEGMENT_RATIO", 0.05)

    assert (
        pipeline._missing_segment_failure_message(
            "H.264 1080p",
            "video",
            3,
            73,
        )
        is None
    )


def test_missing_segment_guard_does_not_fail_subtitle_track(monkeypatch):
    monkeypatch.setattr(pipeline, "MAX_MISSING_SEGMENT_RATIO", 0.05)

    assert (
        pipeline._missing_segment_failure_message(
            "Italian subtitles",
            "subtitle",
            68,
            73,
        )
        is None
    )


def test_download_status_propagates_specific_error():
    downloader = BaseDownloader.__new__(BaseDownloader)
    downloader.download_id = None

    result = downloader._check_download_status(
        {"error": "H.264 1080p: too many missing segments"}
    )

    assert result == DownloadResult(
        None,
        True,
        "H.264 1080p: too many missing segments",
    )
