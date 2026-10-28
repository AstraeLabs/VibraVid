"""End to end: the real flux feeds the real ffmpeg through an OS pipe. Skipped when either binary is missing or flux is too old."""

import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from VibraVid.core.decryptor import decryptor as decryptor_module
from VibraVid.core.velora._decrypt_pipeline.pipeline import DecryptPipelineMixin
from VibraVid.core.velora._decrypt_pipeline.streaming_mux import StreamingMuxPlan

FIXTURE = Path(__file__).parent / "fixtures" / "cenc_audio.mp4"
KID = "f8c80c25690f47368132430e5c6994ce"
KEY = "7bc99cb1dd0623cd0b5065056a57a1dd"
# 1500 bytes holds ftyp + moov and cuts the first moof in half, like the 8 MiB slices of one DASH file do.
CHUNK_SIZES = [1500, 7, 4093, 20_011, 9, 65_536]


def _binary(getter_name: str) -> str | None:
    try:
        import VibraVid.setup as setup

        path = getattr(setup, getter_name)()
    except Exception:
        return None
    return str(path) if path and Path(str(path)).exists() else None


@pytest.fixture(scope="module")
def binaries() -> tuple[str, str]:
    flux, ffmpeg = _binary("get_flux_path"), _binary("get_ffmpeg_path")
    if not flux or not ffmpeg:
        pytest.skip("flux and ffmpeg are both needed")
    decryptor_module._flux_versions.clear()
    if not decryptor_module.flux_supports_tfdt_rebase(flux):
        pytest.skip("flux >= 0.5.4 is needed for --chunks -o - --tfdt-rebase")
    return flux, ffmpeg


def _split(tmp_path: Path) -> Path:
    chunks = tmp_path / "chunks"
    chunks.mkdir()
    data, pos, n = FIXTURE.read_bytes(), 0, 0
    while pos < len(data):
        size = min(CHUNK_SIZES[n % len(CHUNK_SIZES)], len(data) - pos)
        (chunks / f"seg_{n:05d}.mp4").write_bytes(data[pos : pos + size])
        pos += size
        n += 1
    return chunks


def _packet_md5(ffmpeg: str, media: Path) -> str:
    result = subprocess.run(
        [ffmpeg, "-v", "error", "-i", str(media), "-map", "0:a:0", "-c", "copy", "-f", "md5", "-"],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def _host(flux: str, ffmpeg: str, out: Path, key: str) -> DecryptPipelineMixin:
    host = object.__new__(DecryptPipelineMixin)
    host.key = f"{KID}:{key}"
    host.key_fallback = None
    host.flux_path = flux
    host._streaming_mux_output_path = str(out)
    host.streaming_mux_result = None
    host.streaming_mux_chapters_injected = False
    host._keep_tmp_on_error = lambda: False
    plan = StreamingMuxPlan(
        cmd=[ffmpeg, "-y", "-v", "error", "-progress", "pipe:1", "-f", "mp4", "-i", "-", "-map", "0", "-c", "copy", str(out)],
        output_path=str(out),
        chapters_injected=False,
    )
    host._prepare_streaming_mux = lambda *a, **k: plan
    return host


def _ctx(chunks: Path, updates: list) -> tuple[SimpleNamespace, list[Path]]:
    ctx = SimpleNamespace(
        stream=SimpleNamespace(type="video"),
        stream_dir=chunks,
        task_key="vid",
        bar_manager=SimpleNamespace(handle_progress_line=updates.append),
    )
    return ctx, sorted(chunks.glob("seg_*.mp4"))


def test_real_flux_into_real_ffmpeg_matches_a_direct_decrypt(tmp_path, binaries):
    flux, ffmpeg = binaries
    chunks = _split(tmp_path)
    assert len(list(chunks.iterdir())) > 3

    direct = tmp_path / "direct.mp4"
    subprocess.run([flux, "--chunks", str(chunks), "-o", str(direct), "--key", f"{KID}:{KEY}", "--tfdt-rebase", "--no-profile"], check=True, capture_output=True)

    out = tmp_path / "piped.mkv"
    host = _host(flux, ffmpeg, out, KEY)
    updates: list = []
    ctx, paths = _ctx(chunks, updates)

    assert host._try_chunks_pipe_mux(ctx, paths, tmp_path / "v.mp4")

    assert out.stat().st_size > 0
    assert _packet_md5(ffmpeg, out) == _packet_md5(ffmpeg, direct)
    assert host.streaming_mux_result == str(out)
    # The chunks are only read: a failed pipe must still be able to fall back to the file mode.
    assert b"".join(p.read_bytes() for p in paths) == FIXTURE.read_bytes()


def test_wrong_key_never_leaves_a_playable_looking_mkv_behind(tmp_path, binaries):
    flux, ffmpeg = binaries
    chunks = _split(tmp_path)
    out = tmp_path / "piped.mkv"
    host = _host(flux, ffmpeg, out, "00" * 16)
    ctx, paths = _ctx(chunks, [])

    assert not host._try_chunks_pipe_mux(ctx, paths, tmp_path / "v.mp4")
    assert not out.exists()
    assert host.streaming_mux_result is None


def test_a_truncated_chunk_set_fails_cleanly(tmp_path, binaries):
    flux, ffmpeg = binaries
    chunks = _split(tmp_path)
    last = sorted(chunks.glob("seg_*.mp4"))[-1]
    last.write_bytes(last.read_bytes()[:-5000])
    out = tmp_path / "piped.mkv"
    host = _host(flux, ffmpeg, out, KEY)
    ctx, paths = _ctx(chunks, [])

    assert not host._try_chunks_pipe_mux(ctx, paths, tmp_path / "v.mp4")
    assert not out.exists()
