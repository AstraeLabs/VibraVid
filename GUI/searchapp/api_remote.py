# 12.10.26

import base64
import json
import logging
import os
from functools import wraps

from django.http import HttpRequest, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods
from searchapp.api import get_api, get_available_sites
from searchapp.api.base import Entries

from VibraVid.remote import isolation
from VibraVid.remote.config import RemoteUser, ServeSettings, load_serve_settings
from VibraVid.remote.credentials import Identity, build_identity
from VibraVid.remote.license_relay import relay_license
from VibraVid.remote.models import RemoteError, pack_text
from VibraVid.remote.server import (
    KEY_HEADER,
    JobStore,
    SessionStore,
    allowed_sites,
    authenticate,
    cache_service_hints,
    call_license_hook,
    call_refresh_hook,
    fetch_server_keys,
    job_runner,
    learn_cache_services,
    learn_login_sections,
    login_section_hints,
    resolve_media,
    run_for_client,
    site_allowed,
)
from VibraVid.services._base.site_extra_args import get_site_extra_args_schema

from .views import _media_item_to_display_dict

log = logging.getLogger("searchapp.api_remote")
NO_KEY_ENV = "VIBRAVID_REMOTE_NO_KEY"
ANONYMOUS = RemoteUser(username="anonymous")

_store: SessionStore | None = None
_jobs: JobStore | None = None


def _job_store() -> JobStore:
    global _jobs
    if _jobs is None:
        _jobs = JobStore()
    return _jobs


def _forget_scrapers(scope: str) -> None:
    """Scrapers built for a client hold its logged-in session: drop them with the request."""
    from searchapp.api.base import BaseStreamingAPI

    prefix = f"{scope}:"
    cache = BaseStreamingAPI._scraper_cache
    for key in [k for k in list(cache) if k.startswith(prefix)]:
        cache.pop(key, None)


isolation.on_forget(_forget_scrapers)
isolation.install_guards()

_fallback_apis: dict = {}


def _fallback_sites() -> list[str]:
    """Services the site loader knows that have no GUI adapter (csi, pornhub...): the remote API serves them generically."""
    try:
        from VibraVid.services._base.site_loader import load_search_functions

        known = {alias[: -len("_search")] if alias.endswith("_search") else alias: lazy for alias, lazy in load_search_functions().items()}
    except Exception:  # noqa: BLE001 - the loader failing only means no extra services
        return []
    return sorted(name for name, lazy in known.items() if name not in get_available_sites() and not getattr(lazy, "hide", False))


def _api(site: str):
    """The GUI adapter of *site*, or a generic one for a service that has none (search and films; series need an adapter)."""
    if site in get_available_sites():
        return get_api(site)
    api = _fallback_apis.get(site)
    if api is None:
        from searchapp.api.generic import GenericStreamingAPI

        cls = type(f"{site.title()}RemoteAPI", (GenericStreamingAPI,), {"site_name": site, "log_label": site, "_build_scraper": lambda self, item: None})
        api = _fallback_apis[site] = cls()
    return api


def _session_store(settings: ServeSettings) -> SessionStore:
    global _store
    if _store is None:
        _store = SessionStore(settings.session_ttl, settings.max_sessions, settings.bind_session_to_ip)
    return _store


def _error(message: str, status: int, **extra) -> JsonResponse:
    return JsonResponse({"error": message, "status": status, **extra}, status=status)


def _identity_fields(identity: Identity | None, site: str | None = None) -> dict:
    """What a response tells the client about its credentials."""
    if identity is None:
        return {}
    
    out: dict = {}
    wanted = identity.take_wanted()

    if wanted:
        out["credentials_wanted"] = wanted
        if site:
            learn_login_sections(site, wanted)

    updates = identity.take_updates()
    if updates:
        out["credentials_update"] = updates

    cache_wanted = identity.take_cache_wanted()
    if cache_wanted:
        out["cache_wanted"] = cache_wanted
        if site:
            learn_cache_services(site, cache_wanted)

    cache_update = identity.take_cache_updates()
    if cache_update:
        out["cache_update"] = cache_update

    events = identity.take_login_events()
    if events:
        out["login_status"] = events
    return out


def _client_ip(request: HttpRequest) -> str:
    # REMOTE_ADDR only: X-Forwarded-For is client-controlled. Behind a reverse proxy sessions bind to the proxy's address.
    return request.META.get("REMOTE_ADDR", "")


def _json_body(request: HttpRequest) -> dict:
    try:
        body = json.loads(request.body.decode("utf-8"))
        return body if isinstance(body, dict) else {}
    except (ValueError, TypeError):
        return {}


def require_remote_key(view):
    """Authenticate with ``X-VibraVid-Key``. Fails closed: with no configured key and no ``--no-key`` nothing is served."""

    @wraps(view)
    def _guard(request: HttpRequest, *args, **kwargs):
        settings = load_serve_settings()
        if not settings.users:
            if os.environ.get(NO_KEY_ENV) == "1":
                request.remote_user_obj, request.remote_settings = ANONYMOUS, settings
                return view(request, *args, **kwargs)
            return _error("remote server has no API keys configured", 503)

        user = authenticate(settings, request.headers.get(KEY_HEADER))
        if user is None:
            log.warning("[remote] rejected %s from %s: missing or invalid key", request.path, _client_ip(request))
            return _error("API key is missing or invalid", 401)
        request.remote_user_obj, request.remote_settings = user, settings
        return view(request, *args, **kwargs)

    return _guard


def _guarded(methods):
    def decorate(view):
        return csrf_exempt(require_remote_key(require_http_methods(methods)(view)))

    return decorate


def _site_or_error(request: HttpRequest, site: str):
    if not site:
        return _error("site missing", 400)
    if site not in get_available_sites() and site not in _fallback_sites():
        return _error(f"unknown site '{site}'", 404)
    if not site_allowed(request.remote_settings, request.remote_user_obj, site):
        return _error(f"site '{site}' is not allowed for this key", 403)
    return None


def _server_login() -> dict:
    from VibraVid.utils import config_manager

    return config_manager.login._config_dict


def _persisted_site_options(site: str) -> dict:
    try:
        from .views._shared import _resolve_persisted_site_options

        return dict(_resolve_persisted_site_options(site) or {})
    except Exception:  # noqa: BLE001 - optional; the client's own options still apply
        log.debug("[remote] no persisted site options for %s", site, exc_info=True)
        return {}


def _identity_for(site: str, body: dict) -> Identity:
    """The client's own credentials replace every login section of the service; the server's accounts are never used."""
    return build_identity(site, body.get("credentials"), _server_login(), body.get("cache"))


def _options_for(site: str, body: dict) -> dict:
    """Server-persisted service options first, then what the client asked for (``--url``, ``--drm``, ``--store``...)."""
    return {**_persisted_site_options(site), **(body.get("site_options") or {})}


def _entry(payload: dict) -> Entries:
    return Entries(**{k: v for k, v in payload.items() if k in Entries.__dataclass_fields__})


@csrf_exempt
@require_http_methods(["GET"])
def remote_health(request: HttpRequest) -> JsonResponse:
    return JsonResponse({"status": "ok"})


@_guarded(["GET"])
def remote_sites(request: HttpRequest) -> JsonResponse:
    sites = allowed_sites(request.remote_settings, request.remote_user_obj, list(get_available_sites()) + _fallback_sites())
    return JsonResponse({"sites": sites, "server_cdm": request.remote_user_obj.server_cdm})


@_guarded(["GET"])
def remote_site_info(request: HttpRequest, site: str) -> JsonResponse:
    """-> {"options": [{flag, dest, kind, choices, default, help}], "login_sections": [...]}"""
    denied = _site_or_error(request, site)
    if denied:
        return denied
    try:
        options = get_site_extra_args_schema(site)
    except Exception:  # noqa: BLE001 - a service whose options cannot be listed still works without them
        log.exception("[remote] could not list the options of %s", site)
        options = []
    return JsonResponse({"options": options, "login_sections": login_section_hints(site), "cache_services": cache_service_hints(site)})


@_guarded(["POST"])
def remote_search(request: HttpRequest) -> JsonResponse:
    """{"site", "query", "site_options"?, "credentials"?} -> {"results": [{title, type, year, site, is_movie, payload}]}"""
    body = _json_body(request)
    query = str(body.get("query") or "").strip()
    site = str(body.get("site") or "").strip()
    if not query:
        return _error("query missing", 400)
    denied = _site_or_error(request, site)
    if denied:
        return denied

    def search():
        out = []
        for item in _api(site).search(query):
            row = _media_item_to_display_dict(item, site)
            try:
                payload = json.loads(row.get("payload_json") or "{}")
            except (ValueError, TypeError):
                payload = {}
            out.append({
                "title": row.get("display_title"),
                "type": row.get("display_type"),
                "year": row.get("year"),
                "site": row.get("source_alias") or site,
                "is_movie": bool(row.get("is_movie")),
                "payload": payload,
            })
        return out

    identity = None
    try:
        identity = _identity_for(site, body)
        results = run_for_client(identity, site, _options_for(site, body), search)
    except RemoteError as exc:
        return _error(str(exc), exc.status or 500, **_identity_fields(identity, site))
    except Exception as exc:  # noqa: BLE001 - resilience boundary
        log.exception("[remote] search failed on %s", site)
        return _error(str(exc), 500, **_identity_fields(identity, site))
    return JsonResponse({"results": results, **_identity_fields(identity, site)})


@_guarded(["POST"])
def remote_seasons(request: HttpRequest) -> JsonResponse:
    """{"site", "payload", "site_options"?, "credentials"?} -> {"seasons": [{number, name, episodes: [{number, name}]}]}"""
    body = _json_body(request)
    site = str(body.get("site") or "").strip()
    payload = body.get("payload") or {}
    denied = _site_or_error(request, site)
    if denied:
        return denied
    if not payload:
        return _error("payload missing", 400)
    try:
        entry = _entry(payload)
    except TypeError:
        return _error("payload needs at least 'name' and 'type'", 400)

    def seasons():
        return [
            {
                "number": s.number,
                "name": getattr(s, "name", None),
                "episodes": [{"number": e.number, "name": e.name} for e in (s.episodes or [])],
            }
            for s in (_api(site).get_series_metadata(entry) or [])
        ]

    identity = None
    try:
        identity = _identity_for(site, body)
        out = run_for_client(identity, site, _options_for(site, body), seasons)
    except RemoteError as exc:
        return _error(str(exc), exc.status or 500, **_identity_fields(identity, site))
    except Exception as exc:  # noqa: BLE001 - resilience boundary
        log.exception("[remote] seasons failed on %s", site)
        return _error(str(exc), 500, **_identity_fields(identity, site))
    return JsonResponse({"seasons": out, **_identity_fields(identity, site)})


@_guarded(["POST"])
def remote_resolve(request: HttpRequest) -> JsonResponse:
    """{"site", "payload", "season"?, "episodes"?, "site_options"?, "credentials"?} -> {"session_id", "items": [ResolvedMedia]}"""
    body = _json_body(request)
    site = str(body.get("site") or "").strip()
    payload = body.get("payload") or {}
    season = str(body.get("season") or "").strip() or None
    episodes = str(body.get("episodes") or "").strip() or None
    denied = _site_or_error(request, site)
    if denied:
        return denied
    if not payload:
        return _error("payload missing", 400)
    try:
        entry = _entry(payload)
    except TypeError:
        return _error("payload needs at least 'name' and 'type'", 400)
    payload = {**payload, "is_movie": bool(payload.get("is_movie")) or entry.is_movie}
    if not payload["is_movie"] and not season:
        return _error("series need a season", 400)

    user, settings = request.remote_user_obj, request.remote_settings
    identity = None
    try:
        identity = _identity_for(site, body)
        items, hooks = resolve_media(_api(site), entry, site, payload, season, episodes, _options_for(site, body), identity)
        session = _session_store(settings).create(user.username, _client_ip(request), site, items, hooks, identity)
    except RemoteError as exc:
        return _error(str(exc), exc.status or 500, **_identity_fields(identity, site))
    except Exception as exc:  # noqa: BLE001 - resilience boundary
        log.exception("[remote] resolve failed on %s", site)
        return _error(str(exc), 500, **_identity_fields(identity, site))

    log.info("[remote] %s resolved %d item(s) on %s (session %s...)", user.username, len(items), site, session.id[:6])
    return JsonResponse({"session_id": session.id, "items": [i.to_wire() for i in items], **_identity_fields(identity, site)})


def _session_and_item(request: HttpRequest, session_id: str, index):
    settings = request.remote_settings
    session = _session_store(settings).get(session_id, request.remote_user_obj.username, _client_ip(request))
    try:
        return session, session.items[int(index)]
    except (TypeError, ValueError, IndexError) as exc:
        raise RemoteError("item index out of range", 400) from exc


@_guarded(["POST"])
def remote_license(request: HttpRequest, session_id: str) -> JsonResponse:
    """{"item": 0, "drm": "WV"|"PR", "challenge": "<base64>", "pssh_item": {...}?} ->
    {"license": "<base64>"} (PlayReady text answers: {"license_text": "..."})"""
    body = _json_body(request)
    try:
        session, item = _session_and_item(request, session_id, body.get("item", 0))
        challenge = base64.b64decode(str(body.get("challenge") or ""), validate=True)
        if not challenge:
            raise RemoteError("challenge missing", 400)
        drm = "PR" if str(body.get("drm") or "WV").upper() == "PR" else "WV"
        pssh_item = body.get("pssh_item") if isinstance(body.get("pssh_item"), dict) else {}
        result = call_license_hook(session.hooks(int(body.get("item", 0))), challenge, pssh_item, session.identity)
        if result is None:
            result = relay_license(item, drm, challenge)
    except RemoteError as exc:
        return _error(str(exc), exc.status or 500)
    except ValueError:
        return _error("challenge is not valid base64", 400)
    except Exception as exc:  # noqa: BLE001 - resilience boundary
        log.exception("[remote] license relay failed")
        return _error(str(exc), 502)

    fields = _identity_fields(session.identity)
    if isinstance(result, bytes):
        return JsonResponse({"license": base64.b64encode(result).decode("ascii"), **fields})
    return JsonResponse({"license_text": result, **fields})


@_guarded(["POST"])
def remote_keys(request: HttpRequest, session_id: str) -> JsonResponse:
    """Server-CDM mode: {"item": 0} -> {"keys": ["kid:key", ...]}. Needs the ``server_cdm`` grant."""
    user = request.remote_user_obj
    if not user.server_cdm:
        return _error("this key has no server_cdm grant", 403)
    body = _json_body(request)
    try:
        session, item = _session_and_item(request, session_id, body.get("item", 0))
        keys = fetch_server_keys(item, session.site, session.hooks(int(body.get("item", 0))), session.identity)
    except RemoteError as exc:
        return _error(str(exc), exc.status or 500)
    except Exception as exc:  # noqa: BLE001 - resilience boundary
        log.exception("[remote] server-cdm key fetch failed")
        return _error(str(exc), 500)
    log.info("[remote] %s fetched %d key(s) with the server CDM", user.username, len(keys))
    return JsonResponse({"keys": keys, **_identity_fields(session.identity)})


@_guarded(["POST"])
def remote_refresh(request: HttpRequest, session_id: str) -> JsonResponse:
    """{"item": 0} -> {"manifest": {"z": bool, "d": str} | null}: the service's own manifest refresh, run on the server."""
    body = _json_body(request)
    try:
        session, _ = _session_and_item(request, session_id, body.get("item", 0))
        content = call_refresh_hook(session.hooks(int(body.get("item", 0))), session.identity)
    except RemoteError as exc:
        return _error(str(exc), exc.status or 500)
    except Exception as exc:  # noqa: BLE001 - resilience boundary
        log.exception("[remote] manifest refresh failed")
        return _error(str(exc), 502)
    return JsonResponse({"manifest": pack_text(content), **_identity_fields(session.identity)})


@_guarded(["DELETE"])
def remote_close(request: HttpRequest, session_id: str) -> JsonResponse:
    store = _session_store(request.remote_settings)
    username, ip = request.remote_user_obj.username, _client_ip(request)
    try:
        fields = _identity_fields(store.get(session_id, username, ip).identity)  # last chance to hand rotated tokens back
        closed = store.delete(session_id, username, ip)  # ...and then the client is forgotten
    except RemoteError as exc:
        if exc.status == 404:
            return JsonResponse({"closed": False})
        return _error(str(exc), exc.status or 500)
    return JsonResponse({"closed": closed, **fields})


@_guarded(["POST"])
def remote_login_start(request: HttpRequest) -> JsonResponse:
    """{"site", "site_options"?, "credentials"?, "cache"?} -> {"job_id"}"""
    body = _json_body(request)
    site = str(body.get("site") or "").strip()
    denied = _site_or_error(request, site)
    if denied:
        return denied
    try:
        identity = _identity_for(site, body)
        api = _api(site)
        run = job_runner(site, _options_for(site, body), lambda: api._get_search_fn()(None))
        job = _job_store().create(request.remote_user_obj.username, _client_ip(request), identity, run)
    except RemoteError as exc:
        return _error(str(exc), exc.status or 500)
    except Exception as exc:  # noqa: BLE001 - resilience boundary
        log.exception("[remote] could not start the login of %s", site)
        return _error(str(exc), 500)
    log.info("[remote] %s started a login on %s (job %s...)", request.remote_user_obj.username, site, job.id[:6])
    return JsonResponse({"job_id": job.id})


def _job(request: HttpRequest, job_id: str):
    return _job_store().get(job_id, request.remote_user_obj.username, _client_ip(request), request.remote_settings.bind_session_to_ip)


@_guarded(["GET", "DELETE"])
def remote_login(request: HttpRequest, job_id: str) -> JsonResponse:
    """GET: what happened since the last poll (long poll, ``?wait=`` up to 25 s): output, the question waiting, the final result."""
    try:
        job = _job(request, job_id)
        if request.method == "DELETE":
            job.abort()
            _job_store().close(job)
            return JsonResponse({"cancelled": True})
        try:
            wait = max(0.0, min(float(request.GET.get("wait", 15)), 25.0))
        except ValueError:
            wait = 15.0
        state = job.poll(wait)
        from VibraVid.utils import config_manager

        payload = {**state, **_identity_fields(job.identity, job.identity.site), "login_file_path": config_manager.login_file_path}
        if state["done"]:
            _job_store().close(job)  # the client has the final answer: nothing about it stays
        return JsonResponse(payload)
    except RemoteError as exc:
        return _error(str(exc), exc.status or 500)


@_guarded(["POST"])
def remote_login_answer(request: HttpRequest, job_id: str) -> JsonResponse:
    """{"answer": "..."}: the client's answer to the question the service is waiting on."""
    try:
        _job(request, job_id).answer(str(_json_body(request).get("answer", "")))
    except RemoteError as exc:
        return _error(str(exc), exc.status or 500)
    return JsonResponse({"accepted": True})


__all__ = [
    "remote_health", "remote_sites", "remote_site_info", "remote_search", "remote_seasons",
    "remote_resolve", "remote_license", "remote_keys", "remote_refresh", "remote_close",
    "remote_login_start", "remote_login", "remote_login_answer",
]
