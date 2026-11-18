# 05.10.26
# ruff: noqa: E402

"""Test/conftest.py switches the helper-binary downloads off for the whole session (tests must not need the network)."""

import sys
from pathlib import Path

workspace_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(workspace_root))

from VibraVid.setup.binary_paths import binary_paths


def test_binary_downloads_are_disabled_in_the_test_session():
    assert getattr(binary_paths.download_binary, "__self__", None) is None  # replaced by a plain stub, not the real bound method
    assert binary_paths.download_binary("ffmpeg", "ffmpeg") is None
