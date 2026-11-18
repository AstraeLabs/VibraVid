# 29.07.26
# by @ManoloZocco

import contextlib
import io
import logging
import sys
import threading
import uuid
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import NamedTuple

from VibraVid.services._base import load_search_functions

logger = logging.getLogger(__name__)


class SiteInfo(NamedTuple):
    """Static site metadata for the catalog screens."""

    name: str
    indice: int
    use_for: str
    source: str


_sites_cache: list[SiteInfo] | None = None


def list_sites(refresh: bool = False) -> list[SiteInfo]:
    """Return visible site modules sorted by index.

    Reads only the lazy-loader metadata, so no site module gets imported.
    """
    global _sites_cache
    if _sites_cache is not None and not refresh:
        return _sites_cache

    search_functions = load_search_functions()
    sites = [
        SiteInfo(
            name=func.module_name,
            indice=func.indice,
            use_for=(func.use_for or "").lower(),
            source=func.source,
        )
        for func in search_functions.values()
    ]
    sites.sort(key=lambda s: (s.indice, s.name))
    logger.debug("TUI bridge loaded %d sites", len(sites))
    _sites_cache = sites
    return sites


def refresh_external_import() -> list[dict]:
    """Check the git `imp_service` sources now and apply any newer commit; returns one result per source."""
    from VibraVid.services._base.site_loader import refresh_external_sources

    with capture_stdio():
        results = refresh_external_sources()
        list_sites(refresh=True)
    return results


def sites_by_category() -> dict[str, list[SiteInfo]]:
    """Group visible sites by their _useFor category, preserving index order."""
    grouped: dict[str, list[SiteInfo]] = {}
    for site in list_sites():
        grouped.setdefault(site.use_for or "other", []).append(site)
    return grouped


class _ThreadStdProxy:
    """sys.stdout/sys.stderr replacement with per-thread capture buffers."""

    def __init__(self, stream_name: str) -> None:
        self._stream_name = stream_name
        self._local = threading.local()

    def write(self, data) -> int:
        data = str(data)
        capture = getattr(self._local, "capture", None)
        if capture is not None:
            return capture.write(data)
        if self._stream_name == "stderr":
            sys.__stderr__.write(data)
            sys.__stderr__.flush()
        elif data.strip():
            logger.getChild("stdio").debug("[%s] %s", self._stream_name, data.rstrip())
        return len(data)

    def writelines(self, lines) -> None:
        for line in lines:
            self.write(line)

    def flush(self) -> None:
        return None

    def isatty(self) -> bool:
        return False


def install_stdio_proxy() -> None:
    """Install the thread-aware proxies (idempotent)."""
    if not isinstance(sys.stdout, _ThreadStdProxy):
        sys.stdout = _ThreadStdProxy("stdout")
    if not isinstance(sys.stderr, _ThreadStdProxy):
        sys.stderr = _ThreadStdProxy("stderr")


class ServiceExit(RuntimeError):
    """A service called ``sys.exit()`` (failed login, missing config...): in the TUI that must be an error message, never the end of the app."""


def _printed_reason(*buffers: io.StringIO) -> str:
    """The last lines a service printed (its own explanation of a failure), or ``""``."""
    lines = [line.strip() for buf in buffers for line in buf.getvalue().splitlines() if line.strip()]
    return " / ".join(lines[-3:])


def _exit_message(exc: SystemExit, *buffers: io.StringIO) -> str:
    """What the service said before exiting (its last printed lines), else a generic note."""
    return _printed_reason(*buffers) or f"the service stopped the run (exit code {exc.code})"


@contextlib.contextmanager
def capture_stdio() -> Iterator[tuple[io.StringIO, io.StringIO]]:
    """Capture stdout/stderr of the CURRENT thread while running service code; a ``SystemExit`` from it is re-raised as :class:`ServiceExit`."""
    out_buf, err_buf = io.StringIO(), io.StringIO()
    out_local = getattr(sys.stdout, "_local", None)
    err_local = getattr(sys.stderr, "_local", None)
    if out_local is None or err_local is None:
        try:
            yield out_buf, err_buf  # proxy not installed (tests): passthrough
        except SystemExit as exc:
            raise ServiceExit(_exit_message(exc, out_buf, err_buf)) from exc
        return

    prev_out = getattr(out_local, "capture", None)
    prev_err = getattr(err_local, "capture", None)
    out_local.capture, err_local.capture = out_buf, err_buf
    try:
        yield out_buf, err_buf
    except SystemExit as exc:
        raise ServiceExit(_exit_message(exc, out_buf, err_buf)) from exc
    finally:
        out_local.capture, err_local.capture = prev_out, prev_err
        for name, buf in (("stdout", out_buf), ("stderr", err_buf)):
            text = buf.getvalue()
            if text.strip():
                logger.getChild("stdio").debug("[captured %s]\n%s", name, text.rstrip())


# ── GUI adapter layer (Django-free registry) ──────────────────────────────

def preload_registry() -> None:
    """Import the GUI adapter registry early; its import prints warnings."""
    with capture_stdio():
        try:
            import GUI.searchapp.api  # noqa: F401
        except Exception:
            logger.exception("GUI adapter registry failed to load")


def available_adapters() -> list[str]:
    from GUI.searchapp import api
    return api.get_available_sites()


# Sites that return nothing (or fail) without an account, so the global search leaves them out until one is set up.
LOGIN_REQUIRED = ("amazon_music", "crunchyroll", "timvision")


def has_login(site: str) -> bool:
    """Whether *site* needs no account, or has a credential in login.toml or a cached session."""
    if site not in LOGIN_REQUIRED:
        return True

    from VibraVid.tui.screens.settings import service_is_set_up
    from VibraVid.utils import config_manager, disk_cache

    if service_is_set_up(config_manager.login.get_section(site, default={})):
        return True
    return any(
        isinstance(cached, dict) and (cached.get("access_token") or cached.get("refresh_token"))
        for cached in (disk_cache.load(site, "token"), disk_cache.load(site, "token_tv"))
    )


# Home category -> the `_useFor` groups of the sites worth searching for it ("global" = all of them)
SCOPE_USE_FOR = {"anime": {"anime"}, "film": {"film_serie"}, "serie": {"film_serie", "serie", "anime"}, "music": {"song"}}


def global_search_sites(scope: str = "global") -> tuple[list[str], list[str]]:
    """``(sites to search, sites left out for want of a login)`` for a search over every site, or only those of a Home *scope*."""
    sites = available_adapters()
    groups = SCOPE_USE_FOR.get(scope)
    if groups is not None:
        in_scope = {site.name for use_for, listed in sites_by_category().items() if use_for in groups for site in listed}
        sites = [site for site in sites if site in in_scope]
    skipped = [site for site in sites if not has_login(site)]
    return [site for site in sites if site not in skipped], skipped


def _get_api(site: str):
    from GUI.searchapp import api
    return api.get_api(site)


def search_titles(site: str, query: str) -> list:
    """Run a catalog search on one site; returns a list of GUI Entries."""
    with capture_stdio():
        return _get_api(site).search(query)


def search_global(
    query: str,
    sites: list[str] | None = None,
    max_workers: int = 6,
    on_site_done: Callable[[str, list, int, int], None] | None = None,
) -> tuple[dict[str, list], dict[str, str]]:
    """Search every available adapter in parallel.

    Returns (results, errors): results maps site -> [Entries], errors maps
    site -> message for adapters that failed (offline, login required, ...).
    *on_site_done(site, items, done, total)* is called as each adapter finishes (items is empty on
    failure), so a caller can show the fast sites without waiting for the slowest one.
    """
    available = sites if sites is not None else available_adapters()
    results: dict[str, list] = {}
    errors: dict[str, str] = {}

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(search_titles, site, query): site for site in available}
        for done, future in enumerate(as_completed(futures), start=1):
            site = futures[future]
            items = []
            try:
                items = future.result()
                if items:
                    results[site] = items
            except Exception as e:
                logger.warning("global search failed on %s: %s", site, e)
                errors[site] = str(e)
            if on_site_done:
                try:
                    on_site_done(site, items or [], done, len(futures))
                except Exception:
                    logger.exception("search progress callback failed")
    return results, errors


def get_seasons(site: str, item) -> list | None:
    """Return List[Season] for a series/album item, or None when not applicable."""
    with capture_stdio():
        return _get_api(site).get_series_metadata(item)


def start_download(site: str, item, season: str | None = None, episodes: str | None = None) -> bool:
    """Start a download via the GUI adapter layer."""
    from VibraVid.core.ui.manual_tracks import manual_track_broker
    from VibraVid.core.ui.tracker import context_tracker
    from VibraVid.utils import config_manager

    manual = not config_manager.config.get_bool("DOWNLOAD", "auto_select")
    download_id = f"{site}_{uuid.uuid4().hex}" if manual else None
    if manual:
        context_tracker.manual_tracks = True
        context_tracker.defer_manual_tracks = False
        context_tracker.video_quality = None
        context_tracker.download_id = download_id
    try:
        with capture_stdio() as (out_buf, err_buf):
            started = _get_api(site).start_download(item, season=season, episodes=episodes)
        if not started:
            raise RuntimeError(_printed_reason(out_buf, err_buf) or "the download did not start")
        return True
    finally:
        if manual:
            context_tracker.manual_tracks = False
            context_tracker.download_id = None
            manual_track_broker.forget(download_id)
