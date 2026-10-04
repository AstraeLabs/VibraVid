# 04.10.26
# ruff: noqa: E402

import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

repo_root = Path(__file__).resolve().parents[2]
gui_dir = repo_root / "GUI"
for p in (str(repo_root), str(gui_dir)):
    if p not in sys.path:
        sys.path.insert(0, p)

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "webgui.settings")

import django

django.setup()

from searchapp.arr.arr_service import _episode_has_aired, _normalize_external_id


def _iso(delta: timedelta) -> str:
    return (datetime.now(timezone.utc) + delta).strftime("%Y-%m-%dT%H:%M:%SZ")


def test_aired_episode_is_wanted():
    assert _episode_has_aired({"airDateUtc": _iso(timedelta(days=-3))})


def test_future_episode_is_not_wanted():
    assert not _episode_has_aired({"airDateUtc": _iso(timedelta(days=3))})


def test_episode_without_or_with_broken_date_is_not_wanted():
    assert not _episode_has_aired({})
    assert not _episode_has_aired({"airDateUtc": None})
    assert not _episode_has_aired({"airDateUtc": "TBA"})


def test_zero_and_empty_external_ids_mean_no_id():
    # Sonarr/Radarr store 0 when the id is unknown: it must not look like a conflicting id
    for value in (0, "0", " 0 ", "", None, False):
        assert _normalize_external_id(value) is None
    assert _normalize_external_id(312009) == "312009"
    assert _normalize_external_id(" tt6751668 ") == "tt6751668"
