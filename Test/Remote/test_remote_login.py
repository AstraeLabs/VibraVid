# 12.10.26
# ruff: noqa: E402

"""A service's own login (``--login`` / ``--logout``) runs on the server and talks to the client: its messages and
questions are relayed, and the tokens it gets come back to the client, never stored on the server."""

import json
import os
import sys
import threading
import webbrowser
from pathlib import Path
from socketserver import ThreadingMixIn
from wsgiref.simple_server import WSGIRequestHandler, WSGIServer, make_server

ROOT = Path(__file__).resolve().parents[2]
for path in (ROOT, ROOT / "GUI"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "webgui.settings")

import django

django.setup()

import pytest
from django.core.management import call_command
from django.core.wsgi import get_wsgi_application
from django.test import Client
from rich.console import Console
from rich.prompt import Confirm, IntPrompt, Prompt

from searchapp import api_remote
from VibraVid.core.downloader import HLS_Downloader  # noqa: F401 - keeps the import order of the other remote tests
from VibraVid.remote import cli as remote_cli
from VibraVid.remote import isolation
from VibraVid.remote.client import RemoteClient
from VibraVid.remote.config import RemoteServer, RemoteUser, ServeSettings
from VibraVid.remote.credentials import LocalCache, build_identity
from VibraVid.remote.models import RemoteError
from VibraVid.remote.server import JobStore, LoginJob, job_runner
from VibraVid.utils import config_manager, disk_cache

KEY = "login-key"
HEADER = "X-VibraVid-Key"


class LoginService:
    """What a real service's login does: print, open a link, ask, store tokens and a session file, report success."""

    def __init__(self):
        self.region_seen = None
        self.fail_with = None

    def __call__(self, query=None, **kwargs):
        Console().print("[green]Hello[/green] from the [bold]service[/bold]")
        print("a plain print")
        webbrowser.open("https://login.example/pair")
        region = Prompt.ask("Region", choices=["IT", "US"], default="IT")
        self.region_seen = region
        count = IntPrompt.ask("How many devices")
        sure = Confirm.ask("Sure?")
        Prompt.ask("Press Enter after completing", default="")
        if self.fail_with:
            raise RuntimeError(self.fail_with)
        config_manager.login.set_key("appletv", "tv_service_token", f"NEW-{region}-{count}")
        config_manager.save_login()
        disk_cache.save("appletv", "session", {"token": f"SESSION-{region}"})
        print(f"saved to {config_manager.login_file_path}")
        return sure


def identity(login=None, cache=None):
    return build_identity("appletv", login if login is not None else {"appletv": {"tv_service_token": "OLD"}}, {}, cache if cache is not None else {"appletv": {}})


def start(service, **kwargs):
    store = JobStore()
    ident = identity(**kwargs)
    job = store.create("alice", "1.1.1.1", ident, job_runner("appletv", {"login": "tv"}, lambda: service()))
    return store, job, ident


def drive(job, answers, wait=3):
    """Poll like the client does; answer each new question from *answers*. Returns (output, questions, last poll)."""
    answers, out, questions, seen = list(answers), [], [], set()
    for _ in range(200):
        state = job.poll(wait)
        out += state["output"]
        prompt = state["prompt"]
        if prompt and prompt["seq"] not in seen:
            seen.add(prompt["seq"])
            questions.append(prompt)
            job.answer(answers.pop(0))
        if state["done"]:
            return "".join(out), questions, state
    raise AssertionError("the job never finished")


@pytest.fixture(autouse=True)
def _guards(monkeypatch):
    isolation.install_guards()
    monkeypatch.setattr(isolation, "reset_identity_state", lambda site: 0)
    monkeypatch.setattr(isolation, "_last_scope", {})
    monkeypatch.setattr(isolation, "_locks", {})  # a job a test left behind must not hold the next test's service lock
    started = []
    original_start = LoginJob.start
    monkeypatch.setattr(LoginJob, "start", lambda self: (started.append(self), original_start(self))[1])
    yield
    for job in started:  # every job ends with its test
        job.abort()


# ── the job itself ────────────────────────────────────────────────────────────

def test_messages_and_questions_reach_the_client_and_the_answers_reach_the_service(capsys):
    service = LoginService()
    store, job, ident = start(service)

    output, questions, final = drive(job, ["US", "3", "y", ""])

    assert [q["kind"] for q in questions] == ["text", "int", "confirm", "text"]
    assert questions[0]["choices"] == ["IT", "US"] and questions[0]["has_default"] and questions[0]["default"] == "IT"
    assert questions[1]["prompt"] == "How many devices" and not questions[1]["has_default"]
    assert "Hello" in output and "a plain print" in output and "https://login.example/pair" in output
    assert "\x1b[" in output  # rich styling travels as ANSI, the client shows it as is
    assert service.region_seen == "US"
    assert final["status"] == "done" and final["result"] is True and final["error"] is None
    assert "Hello" not in capsys.readouterr().out  # the server's own terminal stays quiet


def test_what_the_service_stores_comes_back_and_nothing_is_written_here(tmp_path, monkeypatch):
    monkeypatch.setattr(config_manager, "base_path", str(tmp_path))
    login_file = tmp_path / "login.json"
    monkeypatch.setattr(config_manager, "login_file_path", str(login_file))
    store, job, ident = start(LoginService())

    output, _, final = drive(job, ["US", "2", "y", ""])

    assert ident.take_updates() == {"appletv": {"tv_service_token": "NEW-US-2"}}
    assert ident.take_cache_updates() == {"appletv": {"session": {"token": "SESSION-US"}}}
    assert f"saved to {login_file}" in output  # the client swaps this path for its own
    assert not login_file.exists() and not list(tmp_path.rglob("*.json"))


def test_the_default_applies_to_an_empty_answer_and_choices_are_enforced():
    service = LoginService()
    store, job, ident = start(service)

    output, questions, final = drive(job, ["", "1", "y", ""])

    assert service.region_seen == "IT" and final["result"] is True

    store, job, ident = start(LoginService())
    output, questions, final = drive(job, ["XX", "US", "1", "y", ""])
    assert "error" in questions[1] and "XX" not in questions[2].get("error", "")  # asked again, with what was wrong
    assert [q["prompt"] for q in questions[:2]] == ["Region", "Region"]
    assert final["result"] is True


def test_numbers_must_be_numbers_and_confirmations_yes_or_no():
    store, job, ident = start(LoginService())

    output, questions, final = drive(job, ["US", "many", "3", "maybe", "n", ""])

    assert questions[2]["kind"] == "int" and questions[2].get("error")
    assert questions[4]["kind"] == "confirm" and questions[4].get("error")
    assert final["result"] is False  # Sure? -> n: the service's own return value reaches the client


def test_a_failure_in_the_service_is_reported():
    service = LoginService()
    service.fail_with = "Amazon refused the code"
    store, job, ident = start(service)

    _, _, final = drive(job, ["IT", "1", "y", ""])

    assert final["status"] == "failed" and final["error"] == "Amazon refused the code"


def test_the_client_can_cancel_a_waiting_login():
    store, job, ident = start(LoginService())
    state = job.poll(3)
    assert state["prompt"] is not None

    job.abort()
    final = {}
    for _ in range(10):
        final = job.poll(3)
        if final["done"]:
            break

    assert final["done"] and final["error"] == "the login was cancelled"


def test_a_client_that_stops_answering_ends_the_login(monkeypatch):
    monkeypatch.setattr(LoginJob, "IDLE_SECONDS", 0.5)
    store, job, ident = start(LoginService())
    assert job.poll(3)["prompt"] is not None

    import time

    time.sleep(2.5)  # no more polls, no answer

    assert job.finished and job.error == "the client stopped answering"


def test_answering_without_a_question_is_a_conflict():
    store, job, ident = start(LoginService())
    job.poll(3)
    job.answer("US")
    with pytest.raises(RemoteError) as error:
        job.answer("again")

    assert error.value.status == 409


def test_other_remote_threads_still_cannot_ask_or_print_to_a_client():
    from VibraVid.utils.scope import scoped

    with scoped({}, "id", {}):
        with pytest.raises(RemoteError, match="cannot give"):
            Prompt.ask("who?")


def test_outside_a_job_console_and_print_behave_normally(capsys):
    Console().print("normal output")
    print("normal print")

    out = capsys.readouterr().out
    assert "normal output" in out and "normal print" in out


# ── the store ─────────────────────────────────────────────────────────────────

def test_jobs_belong_to_their_key_and_address_and_vanish_when_closed():
    store, job, ident = start(LoginService())

    assert store.get(job.id, "alice", "1.1.1.1") is job
    for username, ip in (("bob", "1.1.1.1"), ("alice", "2.2.2.2")):
        with pytest.raises(RemoteError) as error:
            store.get(job.id, username, ip)
        assert error.value.status == 403
    assert store.get(job.id, "alice", "9.9.9.9", bind_to_ip=False) is job

    store.close(job)

    with pytest.raises(RemoteError) as gone:
        store.get(job.id, "alice", "1.1.1.1")
    assert gone.value.status == 404 and ident.overlay == {} and ident.cache == {}


def test_too_many_logins_at_once_are_refused(monkeypatch):
    monkeypatch.setattr(JobStore, "MAX_JOBS", 1)
    store, job, ident = start(LoginService())

    with pytest.raises(RemoteError) as error:
        store.create("alice", "1.1.1.1", identity(), job_runner("appletv", {}, lambda: None))
    assert error.value.status == 503
    job.abort()


# ── over the API and the client ───────────────────────────────────────────────

class Adapter:
    """The GUI adapter of the fake service."""

    def __init__(self, service):
        self.service = service

    def _get_search_fn(self):
        return self.service


@pytest.fixture
def server(monkeypatch):
    service = LoginService()
    settings = ServeSettings(sites=frozenset({"appletv"}), users={KEY: RemoteUser("alice")})
    monkeypatch.setattr(api_remote, "_api", lambda site: Adapter(service))
    monkeypatch.setattr(api_remote, "get_available_sites", lambda: ["appletv", "disney"])
    monkeypatch.setattr(api_remote, "_fallback_sites", lambda: [])
    monkeypatch.setattr(api_remote, "load_serve_settings", lambda: settings)
    monkeypatch.setattr(api_remote, "_persisted_site_options", lambda site: {})
    monkeypatch.setattr(api_remote, "_server_login", lambda: {})
    monkeypatch.setattr(api_remote, "_jobs", None)
    call_command("migrate", verbosity=0)
    return service


def _post(client, url, body, key=KEY, **extra):
    return client.post(url, data=json.dumps(body), content_type="application/json", headers={HEADER: key} if key else {}, **extra)


def test_api_walks_through_a_login(server):
    http = Client(HTTP_HOST="localhost")
    started = _post(http, "/api/remote/login/", {"site": "appletv", "site_options": {"login": "tv"}, "credentials": {"appletv": {"tv_service_token": "OLD"}}, "cache": {"appletv": {}}})
    assert started.status_code == 200
    job_id = started.json()["job_id"]

    answers = iter(["US", "2", "y", ""])
    seen, state = set(), {}
    for _ in range(50):
        state = http.get(f"/api/remote/login/{job_id}/?wait=2", headers={HEADER: KEY}).json()
        prompt = state.get("prompt")
        if prompt and prompt["seq"] not in seen:
            seen.add(prompt["seq"])
            assert _post(http, f"/api/remote/login/{job_id}/answer/", {"answer": next(answers)}).json() == {"accepted": True}
        if state.get("done"):
            break

    assert state["done"] and state["result"] is True
    assert state["credentials_update"] == {"appletv": {"tv_service_token": "NEW-US-2"}}
    assert state["cache_update"] == {"appletv": {"session": {"token": "SESSION-US"}}}
    assert state["login_file_path"] == str(config_manager.login_file_path)
    assert http.get(f"/api/remote/login/{job_id}/", headers={HEADER: KEY}).status_code == 404  # closed once delivered


def test_api_rules(server):
    http = Client(HTTP_HOST="localhost")

    assert _post(http, "/api/remote/login/", {"site": "appletv"}, key=None).status_code == 401
    assert _post(http, "/api/remote/login/", {"site": "disney"}).status_code == 403  # not in the allowlist
    assert _post(http, "/api/remote/login/", {"site": "nowhere"}).status_code == 404
    assert http.get("/api/remote/login/unknown/", headers={HEADER: KEY}).status_code == 404
    assert _post(http, "/api/remote/login/unknown/answer/", {"answer": "x"}).status_code == 404

    job_id = _post(http, "/api/remote/login/", {"site": "appletv"}).json()["job_id"]
    http.get(f"/api/remote/login/{job_id}/?wait=2", headers={HEADER: KEY})
    assert http.get(f"/api/remote/login/{job_id}/", headers={HEADER: KEY}, REMOTE_ADDR="10.9.9.9").status_code == 403
    assert http.delete(f"/api/remote/login/{job_id}/", headers={HEADER: KEY}).json() == {"cancelled": True}
    assert http.get(f"/api/remote/login/{job_id}/", headers={HEADER: KEY}).status_code == 404


class _Quiet(WSGIRequestHandler):
    def log_message(self, *args):
        pass


class _Threaded(ThreadingMixIn, WSGIServer):
    daemon_threads = True


@pytest.fixture
def live(server):
    httpd = make_server("127.0.0.1", 0, get_wsgi_application(), server_class=_Threaded, handler_class=_Quiet)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_port}"
    httpd.shutdown()
    httpd.server_close()


def _answers(monkeypatch, replies):
    asked = []
    iterator = iter(replies)
    monkeypatch.setattr(remote_cli, "_ask_locally", lambda spec: asked.append(spec) or next(iterator))
    return asked


def test_the_client_loop_shows_the_output_asks_here_and_stores_the_tokens_here(live, server, monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(config_manager, "base_path", str(tmp_path))
    updates = []
    client = RemoteClient(
        RemoteServer("t", live, KEY), timeout=30, login_data={"appletv": {"tv_service_token": "OLD"}},
        on_login_update=updates.append, local_cache=LocalCache(),
    )
    asked = _answers(monkeypatch, ["US", "2", "y", ""])

    ok = remote_cli.run_interactive(client, "appletv", {"login": "tv"})

    out = capsys.readouterr().out
    assert ok is True
    assert "Hello" in out and "a plain print" in out and "https://login.example/pair" in out
    assert str(config_manager.login_file_path) in out and "saved to" in out  # the server's path became this machine's
    assert [s["prompt"] for s in asked] == ["Region", "How many devices", "Sure?", "Press Enter after completing"]
    assert updates == [{"appletv": {"tv_service_token": "NEW-US-2"}}]
    assert disk_cache.load("appletv", "session") == {"token": "SESSION-US"}  # in the CLIENT's cache


def test_the_login_a_service_reports_as_failed_is_an_error_on_the_client(live, server, monkeypatch):
    server.fail_with = "Amazon refused the code"
    client = RemoteClient(RemoteServer("t", live, KEY), timeout=30, login_data={})
    _answers(monkeypatch, ["IT", "1", "y", ""])

    with pytest.raises(RemoteError, match="Amazon refused the code"):
        remote_cli.run_interactive(client, "appletv", {"login": "tv"})


def test_a_login_the_service_declines_exits_false(live, server, monkeypatch):
    client = RemoteClient(RemoteServer("t", live, KEY), timeout=30, login_data={})
    _answers(monkeypatch, ["IT", "1", "n", ""])  # Sure? -> n: the service returns False

    assert remote_cli.run_interactive(client, "appletv", {"login": "tv"}) is False


def test_a_question_is_asked_the_way_rich_asks_it(monkeypatch):
    seen = {}
    monkeypatch.setattr(Prompt, "ask", classmethod(lambda cls, prompt, **kw: seen.update(text=prompt, kw=kw) or "US"))

    answer = remote_cli._ask_locally({"kind": "text", "prompt": "Region", "choices": ["IT", "US"], "has_default": True, "default": "IT", "password": False})

    assert answer == "US" and seen["text"] == "Region" and seen["kw"]["choices"] == ["IT", "US"] and seen["kw"]["default"] == "IT"
    monkeypatch.setattr(Confirm, "ask", classmethod(lambda cls, prompt, **kw: True))
    assert remote_cli._ask_locally({"kind": "confirm", "prompt": "Sure?"}) == "y"
    monkeypatch.setattr(IntPrompt, "ask", classmethod(lambda cls, prompt, **kw: 7))
    assert remote_cli._ask_locally({"kind": "int", "prompt": "n"}) == "7"


def test_login_and_logout_options_start_the_remote_login(monkeypatch):
    called = []
    monkeypatch.setattr(remote_cli, "run_interactive", lambda client, site, options: called.append((site, options)) or True)
    server = RemoteServer("t", "http://127.0.0.1:1", KEY)
    monkeypatch.setattr(remote_cli, "pick_remote_server", lambda name: server)
    monkeypatch.setattr(RemoteClient, "sites", lambda self: {"sites": ["appletv"], "server_cdm": False})
    monkeypatch.setattr(RemoteClient, "site_info", lambda self, site: {})
    args = type("A", (), {"server": "t", "site": "appletv", "search": None, "item": None, "season": None, "episode": None, "server_opt": ["login=tv"]})()

    assert remote_cli.run_remote(args) is True

    assert called == [("appletv", {"login": "tv"})]
    args.server_opt = ["csv=songs.csv"]
    with pytest.raises(RemoteError, match="reads a file on this machine"):
        remote_cli.run_remote(args)
