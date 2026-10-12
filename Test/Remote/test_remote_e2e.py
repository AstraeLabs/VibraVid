# 12.10.26
# ruff: noqa: E402

"""Whole remote flow with a real download: the server only resolves, the client really downloads, decrypts and muxes."""

import functools
import http.server
import json
import os
import subprocess
import sys
import threading
from pathlib import Path
from wsgiref.simple_server import WSGIRequestHandler, make_server

ROOT = Path(__file__).resolve().parents[2]
GUI_DIR = ROOT / "GUI"
for path in (ROOT, GUI_DIR):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "webgui.settings")

import django

django.setup()

import pytest
from django.core.management import call_command
from django.core.wsgi import get_wsgi_application

from searchapp import api_remote
from VibraVid.core.downloader import HLS_Downloader
from VibraVid.remote import cli as remote_cli
from VibraVid.remote.client import RemoteClient
from VibraVid.remote.config import RemoteServer, RemoteUser, ServeSettings
from VibraVid.remote.server import fetch_server_keys
from VibraVid.setup import get_ffmpeg_path, get_ffprobe_path, get_velora_path

KEY = "e2e-key"


def _velora() -> str | None:
    try:
        return get_velora_path()
    except Exception:
        return None


pytestmark = pytest.mark.skipif(
    not (get_ffmpeg_path() and get_ffprobe_path() and _velora()),
    reason="needs ffmpeg, ffprobe and the Velora binary",
)


class _Quiet(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass


class _QuietWsgi(WSGIRequestHandler):
    def log_message(self, *args):
        pass


@pytest.fixture(autouse=True)
def _really_download(monkeypatch):
    """The test needs a real download whatever DOWNLOAD.skip_download says in the config it runs with."""
    import VibraVid.core.downloader.dash as dash
    import VibraVid.core.downloader.hls as hls
    import VibraVid.core.downloader.ism as ism

    for module in (hls, dash, ism):
        monkeypatch.setattr(module, "SKIP_DOWNLOAD", False)


@pytest.fixture(scope="module")
def stream_url(tmp_path_factory):
    """A 6 s clear HLS (master + one rendition, 3 segments) served over HTTP."""
    folder = tmp_path_factory.mktemp("hls")
    subprocess.run(
        [
            get_ffmpeg_path(), "-hide_banner", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", "testsrc=duration=6:size=320x240:rate=15",
            "-f", "lavfi", "-i", "sine=frequency=440:duration=6",
            "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-g", "30", "-c:a", "aac", "-b:a", "64k",
            "-f", "hls", "-hls_time", "2", "-hls_playlist_type", "vod",
            "-hls_segment_filename", str(folder / "seg_%d.ts"),
            "-master_pl_name", "master.m3u8", "-var_stream_map", "v:0,a:0",
            str(folder / "stream_%v.m3u8"),
        ],
        check=True,
    )
    handler = functools.partial(_Quiet, directory=str(folder))
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}/master.m3u8"
    server.shutdown()
    server.server_close()


class StreamApi:
    """The 'service': resolves a title to the local stream, the way a real service would build its downloader."""

    def __init__(self, url: str, server_out: Path):
        self.url = url
        self.server_out = server_out

    def start_download(self, media_item, season=None, episodes=None):
        HLS_Downloader(m3u8_url=self.url, output_path=str(self.server_out / "film.mkv")).start()
        return True


@pytest.fixture
def server(monkeypatch, stream_url, tmp_path):
    call_command("migrate", verbosity=0)
    server_out = tmp_path / "server_side_output"
    api = StreamApi(stream_url, server_out)
    settings = ServeSettings(users={KEY: RemoteUser("e2e", server_cdm=True)})
    monkeypatch.setattr(api_remote, "get_api", lambda site: api)
    monkeypatch.setattr(api_remote, "_fallback_sites", lambda: [])
    monkeypatch.setattr(api_remote, "get_available_sites", lambda: ["localtv"])
    monkeypatch.setattr(api_remote, "load_serve_settings", lambda: settings)
    monkeypatch.setattr(api_remote, "_persisted_site_options", lambda site: {})
    monkeypatch.setattr(api_remote, "_store", None)

    wsgi = make_server("127.0.0.1", 0, get_wsgi_application(), handler_class=_QuietWsgi)
    threading.Thread(target=wsgi.serve_forever, daemon=True).start()
    yield RemoteClient(RemoteServer("e2e", f"http://127.0.0.1:{wsgi.server_port}", KEY), timeout=60), server_out
    wsgi.shutdown()
    wsgi.server_close()


def _duration(path: Path) -> float:
    out = subprocess.run(
        [get_ffprobe_path(), "-v", "error", "-show_entries", "format=duration", "-of", "json", str(path)],
        capture_output=True, text=True, check=True,
    ).stdout
    return float(json.loads(out)["format"]["duration"])


def test_server_resolves_only_and_the_client_downloads_for_real(server, monkeypatch, tmp_path):
    client, server_out = server
    payload = {"name": "Local Film", "type": "movie", "id": "1", "year": 2024}

    session_id, items = client.resolve("localtv", payload)

    # The server captured the downloader's inputs and wrote nothing.
    (media,) = items
    assert media.kind == "hls"
    assert media.url.endswith("/master.m3u8")
    assert media.has_drm is False
    assert not server_out.exists()

    client_out = tmp_path / "client_side_output" / "Local Film.mkv"
    monkeypatch.setattr(remote_cli, "output_path_for", lambda m, s: str(client_out))

    assert remote_cli.download_resolved(client, False, session_id, 0, media, "localtv") is True

    assert client_out.is_file()
    assert _duration(client_out) == pytest.approx(6.0, abs=1.0)
    assert not server_out.exists()  # still nothing on the server
    client.close(session_id)


def test_server_cdm_key_flow_on_clear_content_stops_before_downloading(server, tmp_path):
    client, server_out = server
    session_id, (media,) = client.resolve("localtv", {"name": "Local Film", "type": "movie", "id": "1"})

    assert client.keys(session_id, 0) == []  # nothing encrypted: no keys, and no download either

    assert fetch_server_keys(media, "localtv") == []
    assert not server_out.exists()
    client.close(session_id)
