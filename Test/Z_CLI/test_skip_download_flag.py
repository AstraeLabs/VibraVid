import pytest

from VibraVid.cli.run import setup_argument_parser
from VibraVid.core.downloader import mp4
from VibraVid.core.ui.tracker import context_tracker


@pytest.fixture(scope="module")
def parser():
    return setup_argument_parser({})[0]


@pytest.fixture(autouse=True)
def _reset_tracker():
    context_tracker.skip_download = False
    yield
    context_tracker.skip_download = False


def test_flag_defaults_to_false(parser):
    assert parser.parse_args([]).skip_download is False


def test_flag_is_parsed(parser):
    assert parser.parse_args(["--skip-download"]).skip_download is True


def test_tracker_default_is_false():
    assert context_tracker.skip_download is False


def test_tracker_value_is_set_and_cleared():
    context_tracker.skip_download = True
    assert context_tracker.skip_download is True
    context_tracker.skip_download = False
    assert context_tracker.skip_download is False


def test_mp4_preflight_skips_when_flag_is_set(monkeypatch):
    monkeypatch.setattr(mp4, "SKIP_DOWNLOAD", False)
    context_tracker.skip_download = True
    downloader = object.__new__(mp4.MP4FileDownloader)
    assert downloader._preflight() is False
