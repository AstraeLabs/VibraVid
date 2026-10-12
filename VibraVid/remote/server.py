# 12.10.26

import hmac
import logging
import os
import secrets
import tempfile
import threading
import time
from dataclasses import dataclass, field

from VibraVid.core.ui.tracker import context_tracker

from .capture import CaptureBuffer, capturing
from .config import RemoteUser, ServeSettings
from .credentials import Identity
from .factory import build_downloader
from .isolation import forget, prepare, site_lock
from .models import RemoteError, ResolvedMedia

logger = logging.getLogger(__name__)

KEY_HEADER = "X-VibraVid-Key"
WORKER_TIMEOUT = 180.0


def authenticate(settings: ServeSettings, presented: str | None) -> RemoteUser | None:
    """The user owning *presented*, comparing against every configured key in constant time."""
    if not presented:
        return None
    match = None
    for key, user in settings.users.items():
        if hmac.compare_digest(presented.encode("utf-8"), key.encode("utf-8")):
            match = user
    return match


def site_allowed(settings: ServeSettings, user: RemoteUser, site: str) -> bool:
    """Effective allowlist = global ``sites`` ∩ the key's ``sites`` (``None`` means unrestricted)."""
    if settings.sites is not None and site not in settings.sites:
        return False
    if user.sites is not None and site not in user.sites:
        return False
    return True


def allowed_sites(settings: ServeSettings, user: RemoteUser, available: list[str]) -> list[str]:
    return [s for s in available if site_allowed(settings, user, s)]


@dataclass
class RemoteSession:
    id: str
    username: str
    ip: str
    site: str
    items: list[ResolvedMedia]
    callables: list[dict] = field(default_factory=list)
    identity: Identity | None = None
    created: float = field(default_factory=time.monotonic)
    touched: float = field(default_factory=time.monotonic)

    def hooks(self, index: int) -> dict:
        return self.callables[index] if index < len(self.callables) else {}

    def wipe(self) -> None:
        """Forget the client: its credentials, its cache files, the service's callables (which hold its logged-in client)."""
        if self.identity is not None:
            self.identity.wipe()
        self.callables = []
        self.items = []


class SessionStore:
    def __init__(self, ttl: int = 300, max_sessions: int = 100, bind_to_ip: bool = True):
        self.ttl = ttl
        self.max_sessions = max_sessions
        self.bind_to_ip = bind_to_ip
        self._sessions: dict[str, RemoteSession] = {}
        self._lock = threading.Lock()

    def _purge(self) -> None:
        now = time.monotonic()
        for sid in [s for s, sess in self._sessions.items() if now - sess.touched > self.ttl]:
            self._sessions.pop(sid).wipe()

    def create(
        self,
        username: str,
        ip: str,
        site: str,
        items: list[ResolvedMedia],
        callables: list[dict] | None = None,
        identity: Identity | None = None,
    ) -> RemoteSession:
        with self._lock:
            self._purge()
            if len(self._sessions) >= self.max_sessions:
                raise RemoteError("too many active sessions", 503)
            session = RemoteSession(secrets.token_urlsafe(18), username, ip, site, items, callables or [], identity=identity)
            self._sessions[session.id] = session
            return session

    def get(self, session_id: str, username: str, ip: str) -> RemoteSession:
        """The live session, or :class:`RemoteError` (404 unknown/expired, 403 someone else's or another IP)."""
        with self._lock:
            self._purge()
            session = self._sessions.get(session_id)
            if session is None:
                raise RemoteError("session not found or expired", 404)
            if session.username != username or (self.bind_to_ip and session.ip != ip):
                raise RemoteError("session belongs to another client", 403)
            session.touched = time.monotonic()
            return session

    def delete(self, session_id: str, username: str, ip: str) -> bool:
        try:
            self.get(session_id, username, ip)
        except RemoteError as exc:
            if exc.status == 404:
                return False
            raise
        with self._lock:
            session = self._sessions.pop(session_id, None)
        if session is not None:
            session.wipe()
        return session is not None

    def __len__(self) -> int:
        with self._lock:
            self._purge()
            return len(self._sessions)


def _in_thread(fn, timeout: float = WORKER_TIMEOUT, identity: Identity | None = None):
    """Run *fn* on a fresh thread and return its result (or re-raise its exception)."""
    box: dict = {}

    def runner():
        try:
            if identity is not None:
                with identity.active():
                    box["value"] = fn()
            else:
                box["value"] = fn()
        except BaseException as exc:  # noqa: BLE001 - re-raised on the caller's thread
            box["error"] = exc

    lock = site_lock(identity.site) if identity is not None else None
    if lock is not None:
        if not lock.acquire(timeout=timeout):
            raise RemoteError(f"{identity.site} is busy with another request", 503)
        prepare(identity.site, identity.scope)
    try:
        thread = threading.Thread(target=runner, name="remote-worker", daemon=True)
        thread.start()
        thread.join(timeout)
        if thread.is_alive():
            raise RemoteError("the service did not answer in time (is it waiting for interactive input?)", 504)
    finally:
        if lock is not None:
            forget(identity.site, identity.scope)
            lock.release()
    if "error" in box:
        raise box["error"]
    return box.get("value")


def _clean_options(options: dict | None) -> dict:
    """Client-supplied ``site_options``: flat scalars only."""
    return {str(k): v for k, v in (options or {}).items() if isinstance(v, (str, int, float, bool)) or v is None}


def run_for_client(identity: Identity, site: str, options: dict | None, fn):
    """Run *fn* on a worker thread as the client behind *identity* (its credentials, its caches, its service options)."""
    def work():
        context_tracker.site_name = site
        context_tracker.hide_manifest_info = True
        context_tracker.site_options = _clean_options(options)
        return fn()

    return _in_thread(work, identity=identity)


_login_hints: dict[str, set] = {}


def learn_login_sections(site: str, names) -> None:
    _login_hints.setdefault(site, set()).update(names)


def login_section_hints(site: str) -> list[str]:
    return sorted(_login_hints.get(site, ()))


_cache_hints: dict[str, set] = {}


def learn_cache_services(site: str, names) -> None:
    _cache_hints.setdefault(site, set()).update(names)


def cache_service_hints(site: str) -> list[str]:
    return sorted(_cache_hints.get(site, ()))


def resolve_media(
    api,
    media_item,
    site: str,
    payload: dict,
    season: str | None,
    episodes: str | None,
    site_options: dict | None = None,
    identity: Identity | None = None,
) -> tuple[list[ResolvedMedia], list[dict]]:
    """Run the service's own download flow for *media_item* in capture mode."""
    is_movie = bool(payload.get("is_movie")) or not season

    def work() -> tuple[list[ResolvedMedia], list[dict]]:
        buffer = CaptureBuffer()
        context_tracker.site_name = site
        context_tracker.media_type = "Film" if is_movie else "Serie"
        context_tracker.title = payload.get("name")
        context_tracker.no_concurrent = True
        context_tracker.hide_manifest_info = True
        context_tracker.season = int(season) if str(season or "").isdigit() else 0
        context_tracker.episode = int(episodes) if str(episodes or "").isdigit() else 0
        context_tracker.site_options = _clean_options(site_options)
        with capturing(buffer):
            try:
                api.start_download(media_item, season=season, episodes=episodes)
            except Exception as exc:  # noqa: BLE001
                if not buffer.items:
                    raise
                # Everything after the captured downloader (tagging a file that was never downloaded...) is the client's job.
                logger.info(f"resolve {site}: ignored '{type(exc).__name__}: {exc}' raised after the download was captured")

        if buffer.errors and not buffer.items:
            raise RemoteError("; ".join(buffer.errors), 422)
        if buffer.errors:
            logger.warning(f"resolve {site}: partial capture, {buffer.errors}")
        for item in buffer.items:
            item.title = item.title or payload.get("name")
            item.year = payload.get("year")
            item.media_type = item.media_type or ("Film" if is_movie else "Serie")
        return buffer.items, buffer.callables

    items, hooks = _in_thread(work, identity=identity)
    if not items:
        raise RemoteError(f"{site}: nothing to download for this selection", 404)
    return items, hooks


def fetch_server_keys(item: ResolvedMedia, site: str, hooks: dict | None = None, identity: Identity | None = None) -> list[str]:
    """Server-CDM mode: run the normal key flow (manifest → KID → vault → CDM) for *item* and return ``kid:key`` strings."""
    if item.kind == "mp4":
        return []
    hooks = hooks or {}

    def work() -> list[str]:
        keys: list[str] = []
        context_tracker.site_name = site
        context_tracker.hide_manifest_info = True
        with tempfile.TemporaryDirectory(prefix="vibravid-keys-") as tmp, capturing(None, keys):
            build_downloader(
                item,
                os.path.join(tmp, "keys.mkv"),
                license_request_fn=hooks.get("license_request_fn"),
                manifest_refresh_fn=hooks.get("manifest_refresh_fn"),
                with_extras=False,
            ).start()
        return list(dict.fromkeys(keys))

    return _in_thread(work, identity=identity)


def call_license_hook(hooks: dict, challenge: bytes, pssh_item: dict | None, identity: Identity | None = None):
    """The service's own license callable, or ``None`` when it has none."""
    fn = hooks.get("license_request_fn")
    if fn is None:
        return None
    result = _in_thread(lambda: fn(challenge, pssh_item or {}), identity=identity)
    if not result:
        raise RemoteError("the service's license call returned nothing", 502)
    return result


def call_refresh_hook(hooks: dict, identity: Identity | None = None) -> str | None:
    fn = hooks.get("manifest_refresh_fn")
    if fn is None:
        raise RemoteError("this media has no manifest refresh", 400)
    return _in_thread(fn, identity=identity)


class LoginJob:
    MAX_SECONDS = 600
    IDLE_SECONDS = 300

    def __init__(self, job_id: str, username: str, ip: str, identity: Identity, run):
        self.id, self.username, self.ip, self.identity = job_id, username, ip, identity
        self._run_fn = run
        self.status = "running"
        self.result = None
        self.error: str | None = None
        self._out: list[str] = []
        self._prompt: dict | None = None
        self._answer: str | None = None
        self._seq = 0
        self._delivered_seq = 0
        self._aborted = False
        self._cond = threading.Condition()
        self.last_seen = time.monotonic()
        self.started = time.monotonic()

    def start(self) -> None:
        threading.Thread(target=self._run, name=f"login-{self.id[:6]}", daemon=True).start()

    def _run(self) -> None:
        from .isolation import bind_job

        lock = site_lock(self.identity.site)
        if not lock.acquire(timeout=60):
            self._finish("failed", error=f"{self.identity.site} is busy with another request")
            return
        try:
            prepare(self.identity.site, self.identity.scope)
            with self.identity.active(), bind_job(self):
                result = self._run_fn()
            self._finish("done", result=None if result is None else bool(result))
        except BaseException as exc:  # noqa: BLE001 - reported to the client
            logger.info(f"login job {self.id[:6]}: {type(exc).__name__}: {exc}")
            self._finish("failed", error=str(exc) or type(exc).__name__)
        finally:
            forget(self.identity.site, self.identity.scope)
            lock.release()

    def _finish(self, status: str, result=None, error: str | None = None) -> None:
        with self._cond:
            self.status, self.result, self.error = status, result, error
            self._prompt = None
            self._cond.notify_all()

    def say(self, text: str) -> None:
        if text:
            with self._cond:
                self._out.append(text)
                self._cond.notify_all()

    def _check_alive(self) -> None:
        now = time.monotonic()
        if self._aborted:
            raise RemoteError("the login was cancelled", 499)
        if now - self.started > self.MAX_SECONDS:
            raise RemoteError("the login took too long", 408)
        if now - self.last_seen > self.IDLE_SECONDS:
            raise RemoteError("the client stopped answering", 408)

    def _wait_for_answer(self, spec: dict) -> str:
        with self._cond:
            self._seq += 1
            self._prompt = {**spec, "seq": self._seq}
            self._answer = None
            self.status = "pending_input"
            self._cond.notify_all()
            try:
                while self._answer is None:
                    self._check_alive()
                    self._cond.wait(timeout=1.0)
                return self._answer
            finally:
                self._prompt = None
                if self.status == "pending_input":
                    self.status = "running"

    def ask(self, cls, prompt, **kwargs):
        """``Prompt.ask`` for the service: the client is asked, and rich's own validation/conversion is applied to the answer."""
        from rich.prompt import Confirm, FloatPrompt, IntPrompt, InvalidResponse

        has_default = kwargs.get("default", ...) is not ...
        spec = {
            "kind": "confirm" if issubclass(cls, Confirm) else "int" if issubclass(cls, IntPrompt) else "float" if issubclass(cls, FloatPrompt) else "text",
            "prompt": str(prompt),
            "password": bool(kwargs.get("password", False)),
            "choices": list(kwargs["choices"]) if kwargs.get("choices") else None,
            "has_default": has_default,
            "default": kwargs.get("default") if has_default else None,
            "case_sensitive": kwargs.get("case_sensitive", True),
            "show_default": kwargs.get("show_default", True),
            "show_choices": kwargs.get("show_choices", True),
        }
        checker = cls(
            str(prompt), password=spec["password"], choices=spec["choices"], case_sensitive=spec["case_sensitive"],
            show_default=spec["show_default"], show_choices=spec["show_choices"],
        )
        while True:
            answer = self._wait_for_answer(spec)
            if answer == "" and has_default:
                return kwargs["default"]
            try:
                return checker.process_response(answer)
            except InvalidResponse as exc:
                spec = {**spec, "error": _plain(exc.message)}

    def ask_text(self, prompt: str) -> str:
        return self._wait_for_answer({"kind": "text", "prompt": prompt, "password": False, "choices": None, "has_default": False, "default": None})

    def poll(self, wait: float = 15.0) -> dict:
        """What happened since the last poll; waits up to *wait* seconds for something."""
        with self._cond:
            self.last_seen = time.monotonic()
            end = self.last_seen + wait
            while not self._out and not self._new_question() and not self.finished:
                remaining = end - time.monotonic()
                if remaining <= 0:
                    break
                self._cond.wait(timeout=min(remaining, 1.0))
            if self._prompt:
                self._delivered_seq = self._prompt["seq"]
            output, self._out = self._out, []
            finished = self.status in ("done", "failed") and not self._out
            return {
                "status": self.status,
                "output": output,
                "prompt": dict(self._prompt) if self._prompt else None,
                "done": finished,
                "result": self.result if finished else None,
                "error": self.error if finished else None,
            }

    def _new_question(self) -> bool:
        return self._prompt is not None and self._prompt["seq"] != self._delivered_seq

    def answer(self, text: str) -> None:
        with self._cond:
            if self._prompt is None:
                raise RemoteError("no question is waiting for an answer", 409)
            self._answer = str(text)
            self._prompt = None
            self.status = "running"
            self.last_seen = time.monotonic()
            self._cond.notify_all()

    def abort(self) -> None:
        with self._cond:
            self._aborted = True
            self._cond.notify_all()

    @property
    def finished(self) -> bool:
        return self.status in ("done", "failed")


def _plain(text) -> str:
    import re

    return re.sub(r"\[/?[^\]]*\]", "", str(text)).strip()


class JobStore:
    MAX_JOBS = 10

    def __init__(self):
        self._jobs: dict[str, LoginJob] = {}
        self._lock = threading.Lock()

    def _purge(self) -> None:
        now = time.monotonic()
        for job_id, job in list(self._jobs.items()):
            stale = now - job.last_seen > LoginJob.IDLE_SECONDS + 60
            if stale or (job.finished and now - job.last_seen > 120):
                job.abort()
                job.identity.wipe()
                del self._jobs[job_id]

    def create(self, username: str, ip: str, identity: Identity, run) -> LoginJob:
        with self._lock:
            self._purge()
            if len(self._jobs) >= self.MAX_JOBS:
                raise RemoteError("too many logins in progress", 503)
            job = LoginJob(secrets.token_urlsafe(18), username, ip, identity, run)
            self._jobs[job.id] = job
        job.start()
        return job

    def get(self, job_id: str, username: str, ip: str, bind_to_ip: bool = True) -> LoginJob:
        with self._lock:
            self._purge()
            job = self._jobs.get(job_id)
        if job is None:
            raise RemoteError("login not found or expired", 404)
        if job.username != username or (bind_to_ip and job.ip != ip):
            raise RemoteError("login belongs to another client", 403)
        return job

    def close(self, job: LoginJob) -> None:
        """The client has had the final answer: nothing about it stays."""
        job.abort()
        job.identity.wipe()
        with self._lock:
            self._jobs.pop(job.id, None)


def job_runner(site: str, options: dict | None, call):
    """The function a login job runs on its thread: the service's context first, then *call*."""
    def run():
        context_tracker.site_name = site
        context_tracker.hide_manifest_info = True
        context_tracker.site_options = _clean_options(options)
        return call()

    return run
