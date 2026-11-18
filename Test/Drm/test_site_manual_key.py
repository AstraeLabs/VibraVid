# 10.10.26
# ruff: noqa: E402

import sys
from pathlib import Path

repo_root = Path(__file__).resolve().parents[2]
if str(repo_root) not in sys.path:
    sys.path.insert(0, str(repo_root))

import pytest

from VibraVid.cli import run
from VibraVid.core.downloader.dash import DASH_Downloader
from VibraVid.core.downloader.drm_keys import effective_manual_key
from VibraVid.core.downloader.hls import HLS_Downloader
from VibraVid.core.downloader.ism import ISM_Downloader
from VibraVid.core.ui.tracker import context_tracker

KEY = "00112233445566778899aabbccddeeff:ffeeddccbbaa99887766554433221100"
OTHER = "ffeeddccbbaa99887766554433221100:00112233445566778899aabbccddeeff"

DOWNLOADERS = [
    (HLS_Downloader, {"m3u8_url": "http://example.test/a.m3u8"}),
    (DASH_Downloader, {"mpd_url": "http://example.test/a.mpd"}),
    (ISM_Downloader, {"ism_url": "http://example.test/a.ism/Manifest"}),
]


@pytest.fixture(autouse=True)
def reset_site_options():
    previous = context_tracker.site_options
    context_tracker.site_options = {}
    yield
    context_tracker.site_options = previous


def test_effective_key_prefers_the_explicit_key():
    context_tracker.site_options = {"key": [KEY]}
    assert effective_manual_key(OTHER) == OTHER


def test_effective_key_falls_back_to_the_run_key():
    context_tracker.site_options = {"key": [KEY]}
    assert effective_manual_key(None) == [KEY]


def test_effective_key_is_none_without_any_key():
    assert effective_manual_key(None) is None
    context_tracker.site_options = {"key": None}
    assert effective_manual_key(None) is None


@pytest.mark.parametrize("cls,kwargs", DOWNLOADERS)
def test_site_run_key_reaches_the_downloader(cls, kwargs):
    context_tracker.site_options = {"key": [KEY]}
    assert cls(**kwargs).key == [KEY]


@pytest.mark.parametrize("cls,kwargs", DOWNLOADERS)
def test_service_key_wins_over_the_run_key(cls, kwargs):
    context_tracker.site_options = {"key": [KEY]}
    assert cls(key=OTHER, **kwargs).key == OTHER


@pytest.mark.parametrize("cls,kwargs", DOWNLOADERS)
def test_no_key_anywhere_stays_none(cls, kwargs):
    assert cls(**kwargs).key is None


def test_global_key_reaches_site_options():
    import argparse

    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--key", action="append")
    parser.add_argument("--drm", default=None)
    args = parser.parse_args(["--key", KEY])
    assert run.build_site_options(args, parser, [], None)["key"] == [KEY]
