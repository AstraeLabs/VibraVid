from pathlib import Path

from VibraVid.core.muxing.helper.video import normalize


class _Done:
    def __init__(self, returncode=0, stderr=""):
        self.returncode = returncode
        self.stderr = stderr


def _fake_run(calls, fail_flux):
    def run(cmd, **_kwargs):
        calls.append(cmd[0])
        out = Path(cmd[cmd.index("-o") + 1] if "-o" in cmd else cmd[-1])
        if cmd[0] == "flux" and fail_flux:
            return _Done(1, "no content key")
        out.write_bytes(b"x")
        return _Done()

    return run


def test_flux_does_the_remux_when_it_can(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(normalize.subprocess, "run", _fake_run(calls, fail_flux=False))
    src = tmp_path / "track.mp4"
    src.write_bytes(b"x")

    result = normalize.normalize_timestamps(src, flux_path="flux")

    assert result == tmp_path / "track.norm.mp4"
    assert calls == ["flux"]


def test_ffmpeg_takes_over_when_flux_refuses(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(normalize.subprocess, "run", _fake_run(calls, fail_flux=True))
    monkeypatch.setattr(normalize, "get_ffmpeg_path", lambda: "ffmpeg")
    src = tmp_path / "track.mp4"
    src.write_bytes(b"x")

    result = normalize.normalize_timestamps(src, flux_path="flux")

    assert result == tmp_path / "track.norm.mp4"
    assert calls == ["flux", "ffmpeg"]


def test_without_flux_path_only_ffmpeg_runs(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(normalize.subprocess, "run", _fake_run(calls, fail_flux=False))
    monkeypatch.setattr(normalize, "get_ffmpeg_path", lambda: "ffmpeg")
    src = tmp_path / "track.mp4"
    src.write_bytes(b"x")

    normalize.normalize_timestamps(src)

    assert calls == ["ffmpeg"]
