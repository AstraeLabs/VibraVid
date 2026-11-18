# 28.09.26
# ruff: noqa: E402

import sys
from pathlib import Path
from types import SimpleNamespace

workspace_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(workspace_root))

from VibraVid.core.velora._multiperiod import _stream_confirmed_unencrypted


def _drm(encrypted: bool):
    return SimpleNamespace(is_encrypted=lambda: encrypted)


def test_confirmed_unencrypted_when_manifest_drm_is_empty():
    """mpd.py sets an empty DRMInfo() (is_encrypted() -> False) when no
    <ContentProtection> was found anywhere for this stream -- e.g. plain
    WebVTT subtitles. The per-Period flux probe should be skippable."""
    stream = SimpleNamespace(type="subtitle", drm=_drm(False))
    assert _stream_confirmed_unencrypted(stream) is True


def test_not_confirmed_unencrypted_when_stream_carries_drm():
    """A stream whose manifest DRM info says encrypted (any Period, since
    mpd.py merges per-Representation DRM across Periods) must still go
    through the runtime auto-detect/decrypt path."""
    stream = SimpleNamespace(type="video", drm=_drm(True))
    assert _stream_confirmed_unencrypted(stream) is False


def test_not_confirmed_unencrypted_when_drm_info_is_missing():
    """No `.drm` attribute at all (e.g. a manifest format that never sets it)
    means we don't actually know -- fall back to the safe/defensive runtime
    auto-detect rather than assuming clear."""
    stream = SimpleNamespace(type="audio")
    assert _stream_confirmed_unencrypted(stream) is False


def test_not_confirmed_unencrypted_when_drm_is_none():
    stream = SimpleNamespace(type="audio", drm=None)
    assert _stream_confirmed_unencrypted(stream) is False
