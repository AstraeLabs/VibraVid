# 30.10.26

"""PROCESS.normalize_timestamps switches the extra ffmpeg re-mux of each downloaded fMP4 track."""

from pathlib import Path

import pytest

from VibraVid.core.muxing.helper import video as video_helper
from VibraVid.core.velora._decrypt_pipeline import pipeline as pipeline_module
from VibraVid.core.velora._decrypt_pipeline.pipeline import DecryptPipelineMixin


@pytest.fixture
def track(tmp_path) -> Path:
    path = tmp_path / "movie.mp4"
    path.write_bytes(b"merged")
    return path


@pytest.fixture
def calls(monkeypatch) -> list[Path]:
    seen: list[Path] = []

    def fake_normalize(src, _log=None):
        seen.append(src)
        out = src.with_name(f"{src.stem}.norm{src.suffix}")
        out.write_bytes(b"normalized")
        return out

    monkeypatch.setattr(video_helper, "normalize_timestamps", fake_normalize)
    return seen


def set_option(monkeypatch, value) -> None:
    def get_bool(section, key, data_type=bool, default=None):
        return value if (section, key) == ("PROCESS", "normalize_timestamps") else default

    monkeypatch.setattr(pipeline_module.config_manager.config, "get_bool", get_bool)


def test_enabled_by_default_re_muxes_the_track(monkeypatch, track, calls):
    set_option(monkeypatch, True)
    DecryptPipelineMixin._normalize_out_path(object(), track, False)
    assert calls == [track] and track.read_bytes() == b"normalized"


def test_disabled_leaves_the_track_untouched(monkeypatch, track, calls):
    set_option(monkeypatch, False)
    DecryptPipelineMixin._normalize_out_path(object(), track, False)
    assert calls == [] and track.read_bytes() == b"merged"


def test_missing_key_means_enabled(monkeypatch, track, calls):
    set_option(monkeypatch, None)
    monkeypatch.setattr(
        pipeline_module.config_manager.config, "get_bool", lambda section, key, data_type=bool, default=None: default
    )
    DecryptPipelineMixin._normalize_out_path(object(), track, False)
    assert calls == [track]
