# 28.09.26

import sys
from pathlib import Path

workspace_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(workspace_root))

import VibraVid.services.hbomax.downloader as hbomax_downloader


def test_create_dash_downloader_does_not_crash_on_custom_filters():
    """`custom_filters` is a post-construction attribute on DASH_Downloader, not a
    constructor kwarg -- passing it straight through __init__ raises TypeError and
    would crash every HBO Max download."""
    downloader = hbomax_downloader._create_dash_downloader(output_path="unused.mp4")
    assert downloader.custom_filters == hbomax_downloader.HBOMAX_CUSTOM_FILTERS


def test_create_dash_downloader_forwards_other_kwargs():
    downloader = hbomax_downloader._create_dash_downloader(
        output_path="unused.mp4", license_url="https://example/license"
    )
    assert downloader.license_url == "https://example/license"
    assert downloader.display_selected_only == hbomax_downloader.DISPLAY_SELECTED_ONLY
