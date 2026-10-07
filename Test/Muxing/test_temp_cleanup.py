# 06.10.26

"""The temporary download folder is deleted only after the final file is verified."""

import pytest

from VibraVid.core.downloader import base


def _downloader(tmp_path):
    d = object.__new__(base.BaseDownloader)
    d.output_dir = str(tmp_path / ".temp")
    return d


@pytest.fixture
def removed(monkeypatch):
    calls = []
    monkeypatch.setattr(base.os_manager, "fast_rmtree", lambda path: calls.append(path))
    return calls


def test_verified_output_cleans_the_temp_folder(monkeypatch, tmp_path, removed):
    monkeypatch.setattr(base, "CLEANUP_TMP", True)
    d = _downloader(tmp_path)

    d._cleanup_temp_dir(True)

    assert removed == [d.output_dir]


def test_unverified_output_keeps_everything_and_says_where(monkeypatch, tmp_path, removed, capsys):
    monkeypatch.setattr(base, "CLEANUP_TMP", True)
    d = _downloader(tmp_path)

    d._cleanup_temp_dir(False)

    assert removed == []
    assert "kept" in capsys.readouterr().out.lower()


@pytest.mark.parametrize("verified", [True, False])
def test_cleanup_disabled_in_config_never_deletes(monkeypatch, tmp_path, removed, verified):
    monkeypatch.setattr(base, "CLEANUP_TMP", False)

    _downloader(tmp_path)._cleanup_temp_dir(verified)

    assert removed == []
