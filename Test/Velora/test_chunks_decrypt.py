from pathlib import Path
from types import SimpleNamespace

from VibraVid.core.decryptor import decryptor as decryptor_module
from VibraVid.core.velora._decrypt_pipeline import chunks_decrypt as chunks_module
from VibraVid.core.velora._decrypt_pipeline import pipeline as pipeline_module
from VibraVid.core.velora._decrypt_pipeline.pipeline import DecryptPipelineMixin

FTYP = (24).to_bytes(4, "big") + b"ftyp" + b"isom" + b"\0" * 12
MOOF = (16).to_bytes(4, "big") + b"moof" + b"\0" * 8


def _pipeline(key="kid:key") -> DecryptPipelineMixin:
    obj = object.__new__(DecryptPipelineMixin)
    obj.key = key
    obj.key_fallback = None
    obj._needs_join_ts_fix = False
    return obj


def _chunks(tmp_path: Path, count: int = 3) -> list[Path]:
    paths = []
    for n in range(count):
        p = tmp_path / f"seg_{n:05d}.mp4"
        p.write_bytes((FTYP if n == 0 else MOOF) + b"x" * 32)
        paths.append(p)
    return paths


def _ctx(tmp_path: Path, **overrides) -> SimpleNamespace:
    drm = SimpleNamespace(method="cenc", is_encrypted=lambda: True)
    values = dict(
        stream=SimpleNamespace(type="video", drm=drm),
        protocol_lower="dash",
        live_decryption=False,
        stream_dir=tmp_path,
        task_key="t",
        bar_manager=SimpleNamespace(decrypt_progress_cb=lambda _key: None),
    )
    values.update(overrides)
    return SimpleNamespace(**values)


def test_single_contiguous_dash_chunk_set_is_applicable(tmp_path):
    paths = _chunks(tmp_path)
    assert _pipeline()._chunks_decrypt_applicable(_ctx(tmp_path), paths, tmp_path / "v.mp4", False)


def test_completion_order_paths_are_still_applicable(tmp_path):
    paths = _chunks(tmp_path, 6)
    shuffled = [paths[4], paths[3], paths[5], paths[0], paths[2], paths[1]]
    import random

    random.Random(1234).shuffle(shuffled)
    assert _pipeline()._chunks_decrypt_applicable(_ctx(tmp_path), shuffled, tmp_path / "v.mp4", False)


def test_chunk_set_with_a_gap_is_not_applicable(tmp_path):
    paths = _chunks(tmp_path, 4)
    paths[2].unlink()
    assert not _pipeline()._chunks_decrypt_applicable(_ctx(tmp_path), [p for p in paths if p.exists()], tmp_path / "v.mp4", False)


def test_stray_files_in_the_chunk_dir_veto_the_fast_path(tmp_path):
    paths = _chunks(tmp_path)
    (tmp_path / "seg_00003.mp4.dec").write_bytes(b"x")
    assert not _pipeline()._chunks_decrypt_applicable(_ctx(tmp_path), paths, tmp_path / "v.mp4", False)


def test_self_initializing_segments_veto_the_fast_path(tmp_path):
    paths = _chunks(tmp_path)
    paths[1].write_bytes(FTYP + b"x")
    assert not _pipeline()._chunks_decrypt_applicable(_ctx(tmp_path), paths, tmp_path / "v.mp4", False)


def test_live_hls_clear_and_subtitle_streams_are_not_applicable(tmp_path):
    paths = _chunks(tmp_path)
    out = tmp_path / "v.mp4"
    pipe = _pipeline()
    assert not pipe._chunks_decrypt_applicable(_ctx(tmp_path, live_decryption=True), paths, out, False)
    assert not pipe._chunks_decrypt_applicable(_ctx(tmp_path, protocol_lower="hls"), paths, out, False)
    assert not pipe._chunks_decrypt_applicable(_ctx(tmp_path), paths, out, True)
    assert not _pipeline(key=None)._chunks_decrypt_applicable(_ctx(tmp_path), paths, out, False)
    clear = SimpleNamespace(type="video", drm=SimpleNamespace(method=None, is_encrypted=lambda: False))
    assert not pipe._chunks_decrypt_applicable(_ctx(tmp_path, stream=clear), paths, out, False)
    sub = SimpleNamespace(type="subtitle", drm=SimpleNamespace(method="cenc", is_encrypted=lambda: True))
    assert not pipe._chunks_decrypt_applicable(_ctx(tmp_path, stream=sub), paths, out, False)


def test_flux_failure_falls_back_and_leaves_no_partial_output(tmp_path, monkeypatch):
    paths = _chunks(tmp_path)
    out = tmp_path.parent / f"{tmp_path.name}_v.mp4"

    def fake_decrypt_chunks(self, chunks_dir, keys, output_path, **kwargs):
        Path(output_path).write_bytes(b"partial")
        return False

    monkeypatch.setattr(pipeline_module.Decryptor, "decrypt_chunks", fake_decrypt_chunks)
    assert not _pipeline()._try_chunks_decrypt(_ctx(tmp_path), paths, out, False)
    assert not out.exists()
    assert not out.with_suffix(".mp4.dec").exists()


def test_success_renames_the_output_in_place(tmp_path, monkeypatch):
    paths = _chunks(tmp_path)
    out = tmp_path.parent / f"{tmp_path.name}_v.mp4"

    def fake_decrypt_chunks(self, chunks_dir, keys, output_path, **kwargs):
        Path(output_path).write_bytes(b"clear")
        return True

    monkeypatch.setattr(pipeline_module.Decryptor, "decrypt_chunks", fake_decrypt_chunks)
    import VibraVid.core.velora.util._tfdt_rebase as tfdt

    monkeypatch.setattr(tfdt, "rebase_fragment_timestamps", lambda *a, **k: True)
    assert _pipeline()._try_chunks_decrypt(_ctx(tmp_path), paths, out, False)
    assert out.read_bytes() == b"clear"
    assert not out.with_suffix(".mp4.dec").exists()
    out.unlink()


def test_flux_chunks_support_follows_version_and_env(monkeypatch, tmp_path):
    fake = tmp_path / "flux.exe"
    fake.write_bytes(b"")
    monkeypatch.setattr(decryptor_module, "_flux_versions", {})

    def run_version(version, check=decryptor_module.flux_supports_chunks):
        decryptor_module._flux_versions.clear()
        monkeypatch.setattr(
            decryptor_module.subprocess, "run", lambda *a, **k: SimpleNamespace(stdout=f"flux {version}\n")
        )
        return check(str(fake))

    assert run_version("0.5.3")
    assert run_version("0.6.0")
    assert not run_version("0.5.2")
    assert not decryptor_module.flux_supports_chunks(None)

    assert run_version("0.5.3")
    monkeypatch.setenv("VV_FLUX_CHUNKS", "0")
    assert not decryptor_module.flux_supports_chunks(str(fake))


def test_tfdt_rebase_needs_flux_054(monkeypatch, tmp_path):
    fake = tmp_path / "flux.exe"
    fake.write_bytes(b"")
    monkeypatch.setattr(decryptor_module, "_flux_versions", {})

    def supports(version):
        decryptor_module._flux_versions.clear()
        monkeypatch.setattr(
            decryptor_module.subprocess, "run", lambda *a, **k: SimpleNamespace(stdout=f"flux {version}\n")
        )
        return decryptor_module.flux_supports_tfdt_rebase(str(fake))

    assert not supports("0.5.3")
    assert supports("0.5.4")
    assert supports("0.6.0")
    assert not decryptor_module.flux_supports_tfdt_rebase(None)


def test_unparseable_flux_version_disables_both_features(monkeypatch, tmp_path):
    fake = tmp_path / "flux.exe"
    fake.write_bytes(b"")
    monkeypatch.setattr(decryptor_module, "_flux_versions", {})
    monkeypatch.setattr(decryptor_module.subprocess, "run", lambda *a, **k: SimpleNamespace(stdout="garbage\n"))
    assert not decryptor_module.flux_supports_chunks(str(fake))
    assert not decryptor_module.flux_supports_tfdt_rebase(str(fake))


def test_flux_rebasing_the_tfdt_skips_the_python_pass(tmp_path, monkeypatch):
    paths = _chunks(tmp_path)
    out = tmp_path.parent / f"{tmp_path.name}_rebase.mp4"
    seen = {}

    def fake_decrypt_chunks(self, chunks_dir, keys, output_path, **kwargs):
        seen.update(kwargs)
        Path(output_path).write_bytes(b"clear")
        return True

    def python_rebase(*a, **k):
        raise AssertionError("flux already zeroed the tfdt")

    monkeypatch.setattr(pipeline_module.Decryptor, "decrypt_chunks", fake_decrypt_chunks)
    monkeypatch.setattr(chunks_module, "flux_supports_tfdt_rebase", lambda path: True)
    import VibraVid.core.velora.util._tfdt_rebase as tfdt

    monkeypatch.setattr(tfdt, "rebase_fragment_timestamps", python_rebase)
    assert _pipeline()._try_chunks_decrypt(_ctx(tmp_path), paths, out, False)
    assert seen["tfdt_rebase"] is True
    out.unlink()


def test_old_flux_gets_the_python_rebase(tmp_path, monkeypatch):
    paths = _chunks(tmp_path)
    out = tmp_path.parent / f"{tmp_path.name}_oldflux.mp4"
    seen = {}

    def fake_decrypt_chunks(self, chunks_dir, keys, output_path, **kwargs):
        seen.update(kwargs)
        Path(output_path).write_bytes(b"clear")
        return True

    called = []
    monkeypatch.setattr(pipeline_module.Decryptor, "decrypt_chunks", fake_decrypt_chunks)
    monkeypatch.setattr(chunks_module, "flux_supports_tfdt_rebase", lambda path: False)
    import VibraVid.core.velora.util._tfdt_rebase as tfdt

    monkeypatch.setattr(tfdt, "rebase_fragment_timestamps", lambda *a, **k: called.append(a) or True)
    assert _pipeline()._try_chunks_decrypt(_ctx(tmp_path), paths, out, False)
    assert seen["tfdt_rebase"] is False
    assert called
    out.unlink()
