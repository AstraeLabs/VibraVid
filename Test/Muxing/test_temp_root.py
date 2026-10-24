# 24.10.26

"""DOWNLOAD.temp_root / VIBRAVID_TEMP_DIR moves the scratch folder off the output disk (e.g. a NAS) while the final file stays where it was."""

import os

import pytest

from VibraVid.core.downloader import base


class FakeConfig:
    def __init__(self, root=""):
        self.root = root

    def get(self, section, key, default=None):
        return self.root if (section, key) == ("DOWNLOAD", "temp_root") else default


@pytest.fixture
def setup(monkeypatch):
    def apply(root="", env=None):
        monkeypatch.setattr(base.config_manager, "config", FakeConfig(root))
        if env is None:
            monkeypatch.delenv("VIBRAVID_TEMP_DIR", raising=False)
        else:
            monkeypatch.setenv("VIBRAVID_TEMP_DIR", env)

    return apply


def _downloader(out_dir, name="Movie"):
    return base.BaseDownloader(str(out_dir / f"{name}.{base.EXTENSION_OUTPUT}"), "_dash_temp", sanitize_path=False)


def test_default_keeps_the_temp_folder_next_to_the_output(setup, tmp_path):
    setup()
    d = _downloader(tmp_path)

    assert d.output_dir == str(tmp_path / ".Movie_dash_temp")


def test_temp_root_from_config_is_used_and_the_output_path_is_untouched(setup, tmp_path):
    root = tmp_path / "scratch"
    setup(root=str(root))
    out = tmp_path / "library"
    out.mkdir()
    d = _downloader(out)

    assert os.path.dirname(os.path.dirname(d.output_dir)) == str(root)
    assert os.path.basename(d.output_dir) == ".Movie_dash_temp"
    assert d.output_path == str(out / f"Movie.{base.EXTENSION_OUTPUT}")


def test_environment_variable_wins_over_the_config(setup, tmp_path):
    setup(root=str(tmp_path / "from_config"), env=str(tmp_path / "from_env"))

    assert _downloader(tmp_path).output_dir.startswith(str(tmp_path / "from_env"))


def test_the_path_is_stable_for_resuming_and_apart_for_other_folders(setup, tmp_path):
    setup(root=str(tmp_path / "scratch"))
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()

    assert _downloader(tmp_path / "a").output_dir == _downloader(tmp_path / "a").output_dir
    assert _downloader(tmp_path / "a").output_dir != _downloader(tmp_path / "b").output_dir


def test_an_unusable_temp_root_falls_back_to_the_output_folder(setup, tmp_path, caplog):
    blocker = tmp_path / "file"
    blocker.write_text("x")  # a file where a folder is needed: makedirs fails
    setup(root=str(blocker / "scratch"))

    d = _downloader(tmp_path)

    assert d.output_dir == str(tmp_path / ".Movie_dash_temp")
    assert "not usable" in caplog.text


def test_moving_the_final_file_works_across_folders(setup, tmp_path, monkeypatch):
    setup()
    d = _downloader(tmp_path)
    src = tmp_path / "elsewhere" / "result.mkv"
    src.parent.mkdir()
    src.write_bytes(b"data")
    monkeypatch.setattr(base.shutil, "move", lambda a, b: os.replace(a, b))  # same-disk stand-in for a cross-filesystem move

    d._move_to_final_location(str(src))

    assert os.path.exists(d.output_path) and not src.exists()


def test_the_output_folder_is_created_even_when_the_temp_folder_lives_elsewhere(setup, tmp_path):
    # ffmpeg writes the final file straight into the output folder: without it the live mux dies with a broken pipe
    setup(root=str(tmp_path / "scratch"))
    out = tmp_path / "library" / "Movie" / "Title"
    d = _downloader(out)

    d._create_work_dirs()

    assert out.is_dir() and os.path.isdir(d.output_dir)
