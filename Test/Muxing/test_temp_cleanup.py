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


def _finalizing_downloader(tmp_path):
    d = _downloader(tmp_path)
    d.output_path = str(tmp_path / "episode.mkv")
    d.copied_subtitles = []
    d.copied_audios = []
    d.download_id = None
    return d


def test_exception_after_verification_still_cleans_the_temp_folder(monkeypatch, tmp_path, removed):
    """retag / sidecars / tracking failing after a verified mux must not leave the temp folder behind."""
    monkeypatch.setattr(base, "CLEANUP_TMP", True)
    monkeypatch.setattr(base, "retag_file", lambda path: (_ for _ in ()).throw(RuntimeError("boom")))
    d = _finalizing_downloader(tmp_path)

    with pytest.raises(RuntimeError):
        d._finalize(final_file="")

    assert removed == [d.output_dir]


def test_exception_before_verification_keeps_the_temp_folder(monkeypatch, tmp_path, removed):
    monkeypatch.setattr(base, "CLEANUP_TMP", True)
    d = _finalizing_downloader(tmp_path)
    monkeypatch.setattr(d, "_move_copied_audios", lambda: (_ for _ in ()).throw(RuntimeError("boom")), raising=False)

    with pytest.raises(RuntimeError):
        d._finalize(final_file="")

    assert removed == []
