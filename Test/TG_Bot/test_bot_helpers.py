# 05.10.26
# ruff: noqa: E402

import atexit
import json
import os
import shutil
import sys
import tempfile
import threading
from collections import OrderedDict
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest

pytest.importorskip("telethon")

_config_dir = tempfile.mkdtemp(prefix="vibravid_bot_tests_")
_state_dir = tempfile.mkdtemp(prefix="vibravid_bot_state_")
atexit.register(shutil.rmtree, _config_dir, ignore_errors=True)
atexit.register(shutil.rmtree, _state_dir, ignore_errors=True)
os.environ.setdefault("VIBRAVID_CONFIG", os.path.join(_config_dir, "config.json"))
os.environ.setdefault("STATE_DIR", _state_dir)

from docker.telegram_bot import common, state

assert Path(common.__file__).resolve().is_relative_to(ROOT), "the repo's docker/ package must win over the PyPI 'docker' SDK"

HEADER = "X-VibraVid-Token"


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for name in ("TG_ALLOWED_USERS", "TG_GUI_URL", "VIBRAVID_BOT_SECRET", "TG_MAX_RESULTS"):
        monkeypatch.delenv(name, raising=False)


def _data(button):
    """Callback payload of an inline button (telethon 1.45 moved it from ``button.data`` to ``button.type.data``)."""
    raw = getattr(button, "data", None)
    if raw is None:
        raw = button.type.data
    return raw.decode("utf-8")


def _all_callbacks(rows):
    return [_data(b) for row in rows for b in row]


# --- small helpers ------------------------------------------------------------------------------------------------------------

def test_esc_escapes_html_but_not_quotes():
    assert common._esc("<b>Tom & \"Jerry\"</b>") == "&lt;b&gt;Tom &amp; \"Jerry\"&lt;/b&gt;"
    assert common._esc(None) == "" and common._esc(7) == "7"


def test_remember_returns_unique_tokens_and_keeps_only_the_last_64():
    store = OrderedDict()
    tokens = [common._remember(store, i) for i in range(70)]

    assert len(set(tokens)) == 70
    assert len(store) == 64
    assert tokens[0] not in store and store[tokens[-1]] == 69


def test_label_for(monkeypatch):
    monkeypatch.setattr(state, "_site_opts", [{"value": "streamingcommunity", "label": "StreamingCommunity"}])

    assert common._label_for("streamingcommunity") == "StreamingCommunity"
    assert common._label_for("__all__") == "🌐 All sites"
    assert common._label_for("__cat__:film_serie") == "🌐 Film Serie"
    assert common._label_for("some_site") == "Some Site"


def test_poster_url_ignores_placeholders():
    assert common._poster_url({"payload": {"poster": "https://img/x.jpg"}}) == "https://img/x.jpg"
    assert common._poster_url({"payload": {"poster": "https://via.placeholder.com/1"}}) is None
    assert common._poster_url({"payload": {}}) is None and common._poster_url({}) is None


@pytest.mark.parametrize(
    "item, icon, label",
    [
        ({"type": "Song"}, "🎵", "Series"),
        ({"type": "ebook"}, "📚", "Series"),
        ({"is_movie": True}, "🎬", "Movie"),
        ({"is_movie": False}, "📺", "Series"),
        ({"media_kind": "album", "type": "album"}, "🎵", "Album"),
        ({"media_kind": " Live "}, "📺", "Live"),
        ({"media_kind": "podcast"}, "📺", "Podcast"),
    ],
)
def test_result_icon_and_type_label(item, icon, label):
    assert common._remote_icon(item) == icon
    assert common._type_label(item) == label


@pytest.mark.parametrize(
    "value, expected",
    [(0, "0.0 B"), (1023, "1023.0 B"), (1024, "1.0 KB"), (1536, "1.5 KB"), (5 * 1024**3, "5.0 GB"), (3 * 1024**5, "3072.0 TB"), ("abc", "?"), (None, "?")],
)
def test_fmt_bytes(value, expected):
    assert common._fmt_bytes(value) == expected


def test_cb_parts_splits_the_callback_data():
    event = SimpleNamespace(data=b"ws:abcd1234:7")

    assert common._cb_parts(event) == ["ws", "abcd1234", "7"]


# --- paginated keyboards: Telegram caps callback data at 64 bytes -----------------------------------------------------------

def test_site_view_paginates_and_marks_the_selection():
    options = [{"value": f"site{i}", "label": f"Site {i}"} for i in range(40)]
    title, rows = common._site_view(options, "site3", 0, "tok12345")

    assert "(page 1/3)" in title and "<b>Site3</b>" in title
    site_rows = rows[: state.PAGE_SIZE]
    assert len(site_rows) == state.PAGE_SIZE
    assert _data(site_rows[0][0]) == "ws:tok12345:0"
    assert any("✅" in b.text for row in site_rows for b in row)
    assert [_data(b) for b in rows[-1]] == ["wp:tok12345:1"]  # first page: only "next"

    _, last = common._site_view(options, "site3", 99, "tok12345")  # out of range -> last page
    assert [_data(b) for b in last[-1]] == ["wp:tok12345:1"]
    assert all(len(cb.encode()) <= 64 for cb in _all_callbacks(last))


def test_site_view_single_page_has_no_navigation():
    options = [{"value": "a", "label": "A"}, {"value": "b", "label": "B"}]
    title, rows = common._site_view(options, "a", 0, "t")

    assert "page" not in title
    assert len(rows) == 2


def test_episode_view_grid_and_navigation():
    head, rows = common._episode_view("My Show", "tok", "2", "1", 65, 1)

    assert "Season 1" in head and "(page 2/3)" in head
    assert _data(rows[0][0]) == "sa:tok:2:1:65"  # "all episodes"
    episodes = [_data(b) for row in rows[1:-1] for b in row]
    assert episodes[0] == "se:tok:2:1:31" and episodes[-1] == "se:tok:2:1:60"
    assert all(len(row) <= state.EP_COLS for row in rows[1:-1])
    assert [_data(b) for b in rows[-1]] == ["sp:tok:2:1:65:0", "sp:tok:2:1:65:2"]
    assert all(len(cb.encode()) <= 64 for cb in _all_callbacks(rows))


def test_episode_view_with_no_episodes():
    _, rows = common._episode_view("Show", "t", "0", "1", 0, 0)

    assert len(rows) == 1  # only the "all" button


# --- access control and configuration ---------------------------------------------------------------------------------------
def _event(sender_id):
    return SimpleNamespace(sender_id=sender_id)


def test_everyone_is_allowed_when_no_allow_list_is_set():
    assert common.is_allowed(_event(1)) is True


def test_allow_list_restricts_the_bot(monkeypatch):
    monkeypatch.setenv("TG_ALLOWED_USERS", "10, 20")

    assert common.is_allowed(_event(10)) is True and common.is_allowed(_event(20)) is True
    assert common.is_allowed(_event(30)) is False


def test_a_malformed_allow_list_never_opens_the_bot_to_everyone(monkeypatch):
    monkeypatch.setenv("TG_ALLOWED_USERS", "10,abc")
    assert common.is_allowed(_event(10)) is True
    assert common.is_allowed(_event(99)) is False

    monkeypatch.setenv("TG_ALLOWED_USERS", "abc")
    assert common.is_allowed(_event(99)) is False


def test_config_precedence_config_json_then_environment(monkeypatch, tmp_path):
    config_file = tmp_path / "config.json"
    config_file.write_text(json.dumps({"TELEGRAM": {"gui_url": "http://gui:9000/", "max_results": 3}}), encoding="utf-8")
    monkeypatch.setattr(state, "CONFIG_PATH", config_file)
    monkeypatch.setenv("TG_GUI_URL", "http://ignored")
    monkeypatch.setenv("TG_MAX_RESULTS", "50")

    cfg = state.Config()

    assert cfg.gui_url == "http://gui:9000" and cfg.max_results == 3

    config_file.write_text(json.dumps({"TELEGRAM": {}}), encoding="utf-8")
    os.utime(config_file, (1, 1))  # a different mtime triggers the hot reload
    cfg.refresh()
    assert cfg.gui_url == "http://ignored" and cfg.max_results == 50


def test_config_defaults_when_nothing_is_set(monkeypatch, tmp_path):
    monkeypatch.setattr(state, "CONFIG_PATH", tmp_path / "missing.json")
    cfg = state.Config()

    assert cfg.gui_url == "http://vibravid:8000" and cfg.max_results == 8 and cfg.enabled is True
    monkeypatch.setenv("TG_MAX_RESULTS", "lots")
    assert cfg.max_results == 8


# --- HTTP client towards the GUI ---------------------------------------------------------------------------------------------
class _Gui(BaseHTTPRequestHandler):
    seen = []

    def _reply(self, status, body):
        raw = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        _Gui.seen.append(("GET", self.path, dict(self.headers), None))
        self._reply(404 if self.path == "/missing" else 200, {"ok": self.path != "/missing", "path": self.path})

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])) or b"{}")
        _Gui.seen.append(("POST", self.path, dict(self.headers), body))
        self._reply(400 if self.path == "/bad" else 200, {"echo": body})

    def log_message(self, *args):
        pass


@pytest.fixture
def gui(monkeypatch):
    _Gui.seen = []
    server = HTTPServer(("127.0.0.1", 0), _Gui)
    threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True).start()
    monkeypatch.setenv("TG_GUI_URL", f"http://127.0.0.1:{server.server_port}/")
    monkeypatch.setattr(state, "cfg", state.Config())
    yield _Gui
    server.shutdown()
    server.server_close()


def test_http_json_posts_a_json_body_without_a_token_when_no_secret_is_set(gui):
    assert common._http_json("POST", "/api/bot/search/", {"query": "x"}) == {"echo": {"query": "x"}}

    method, path, headers, body = gui.seen[0]
    assert (method, path, body) == ("POST", "/api/bot/search/", {"query": "x"})
    names = {name.lower() for name in headers}
    assert headers["Content-Type"] == "application/json" and HEADER.lower() not in names


def test_http_json_sends_the_shared_secret(gui, monkeypatch):
    monkeypatch.setenv("VIBRAVID_BOT_SECRET", " s3cret ")
    common._http_json("GET", "/api/bot/status/")

    headers = {name.lower(): value for name, value in gui.seen[0][2].items()}
    assert headers[HEADER.lower()] == "s3cret"


def test_http_json_returns_the_json_body_of_error_responses(gui):
    assert common._http_json("POST", "/bad", {}) == {"echo": {}}
    assert common._http_json("GET", "/missing") == {"ok": False, "path": "/missing"}
