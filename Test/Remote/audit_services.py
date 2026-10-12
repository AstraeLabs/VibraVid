# 12.10.26
# ruff: noqa: E402

import argparse
import ast
import json
import os
import re
import shutil
import socket
import sys
import tempfile
import threading
import time
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SERVER_MARK, A_MARK, B_MARK = "SERVER", "CA", "CB"
COMMON_KEYS = ("token", "access_token", "st", "cookie", "email", "password", "refresh_token")
DOWNLOADERS = {"HLS_Downloader", "DASH_Downloader", "ISM_Downloader", "MP4_Downloader", "Generic_Downloader", "YTDLPDownloader", "download"}
LOGIN_RECEIVERS = {"config_manager.login", "login", "login_cfg", "login_data_cfg"}
LOGIN_READS = {"get", "get_section", "has_section", "get_int", "get_bool", "get_list", "get_dict"}
HTTP_LIBS = {"requests", "httpx", "curl_cffi", "urllib", "cloudscraper", "websocket", "websockets", "aiohttp"}


# ── static reading ────────────────────────────────────────────────────────────

def _module_level(stmt):
    """Nodes that run when the module is imported: everything but function bodies."""
    stack = [stmt]
    while stack:
        node = stack.pop()
        yield node
        for child in ast.iter_child_nodes(node):
            if not isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                stack.append(child)


def _literal(node):
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None


def _is_login_call(node) -> bool:
    return (
        isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in LOGIN_READS | {"set_key"}
        and ast.unparse(node.func.value) in LOGIN_RECEIVERS
    )


def scan_service(folder: Path) -> dict:
    out = {
        "sections": {}, "import_time_reads": [], "writes": [], "cache_services": set(), "state": [], "downloaders": set(),
        "callables": set(), "prompts": 0, "file_writes": 0, "http": set(), "raw_login": 0, "files": 0,
    }
    for path in sorted(folder.rglob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):
            continue
        out["files"] += 1
        rel = path.relative_to(folder).as_posix()

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                out["http"] |= {a.name.split(".")[0] for a in node.names} & HTTP_LIBS
            elif isinstance(node, ast.ImportFrom) and node.module:
                out["http"] |= {node.module.split(".")[0]} & HTTP_LIBS
            elif isinstance(node, ast.Attribute) and node.attr == "_login_data":
                out["raw_login"] += 1
            elif isinstance(node, ast.Call):
                name = node.func.id if isinstance(node.func, ast.Name) else node.func.attr if isinstance(node.func, ast.Attribute) else ""
                if _is_login_call(node):
                    section = _literal(node.args[0]) if node.args else None
                    key = _literal(node.args[1]) if len(node.args) > 1 else None
                    keys = out["sections"].setdefault(section or "<variable>", set())
                    if key:
                        keys.add(key)
                    if node.func.attr == "set_key":
                        out["writes"].append(f"{rel}:{node.lineno} set_key({section}.{key})")
                elif name == "save_login":
                    out["writes"].append(f"{rel}:{node.lineno} save_login()")
                elif name in {"load", "save", "load_fresh", "invalidate", "lock", "clear_service", "cache_path"} and ast.unparse(node.func).startswith("disk_cache"):
                    out["cache_services"].add(_literal(node.args[0]) or "<variable>" if node.args else "<variable>")
                elif name in DOWNLOADERS:
                    out["downloaders"].add(name)
                    out["callables"] |= {kw.arg for kw in node.keywords if kw.arg in {"license_request_fn", "manifest_refresh_fn"}}
                elif name in {"ask", "input"} and (name == "input" or ast.unparse(node.func).split(".")[0] in {"Prompt", "msg", "Confirm", "console"}):
                    out["prompts"] += 1
                elif name == "open" and len(node.args) > 1 and any(c in (_literal(node.args[1]) or "") for c in "wax"):
                    out["file_writes"] += 1
                elif name in {"write_text", "write_bytes"}:
                    out["file_writes"] += 1

        for stmt in tree.body:
            if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for node in _module_level(stmt):
                if _is_login_call(node) and node.func.attr != "set_key":
                    out["import_time_reads"].append(f"{rel}:{node.lineno}")
            if isinstance(stmt, (ast.Assign, ast.AnnAssign)):
                targets = stmt.targets if isinstance(stmt, ast.Assign) else [stmt.target]
                value = stmt.value
                for target in targets:
                    if not isinstance(target, ast.Name) or value is None or target.id.isupper():
                        continue
                    kind = (
                        "None" if isinstance(value, ast.Constant) and value.value is None
                        else "container" if isinstance(value, (ast.Dict, ast.List, ast.Set))
                        else "instance" if isinstance(value, ast.Call) and isinstance(value.func, (ast.Name, ast.Attribute)) and (ast.unparse(value.func).split(".")[-1][:1].isupper())
                        else ""
                    )
                    if kind and target.id.startswith("_") and not target.id.startswith("__"):
                        out["state"].append(f"{rel}:{target.id} ({kind})")
    return out


# ── dynamic driving ───────────────────────────────────────────────────────────

class Recorder:
    """The fake network: records every request the services make and answers 503."""

    def __init__(self):
        self.calls: list[str] = []
        self.prompts = 0

    def reset(self):
        self.calls.clear()

    def install(self):
        import socket as socket_module
        import urllib.error
        import urllib.request

        import curl_cffi.requests as curl
        import requests

        recorder = self
        real_urlopen = urllib.request.urlopen
        real_connect = socket_module.socket.connect
        real_create = socket_module.create_connection

        def is_loopback(address) -> bool:
            host = address[0] if isinstance(address, tuple) else address
            return host in ("127.0.0.1", "localhost", "::1")

        class Response:
            status_code = 503
            ok = False
            reason = "Service Unavailable"
            text = "{}"
            content = b"{}"
            headers: dict = {}
            cookies: dict = {}
            encoding = "utf-8"
            elapsed = 0.0

            def __init__(self, url=""):
                self.url = url

            def json(self, *a, **k):
                return {}

            def raise_for_status(self):
                raise RuntimeError("503 (audit)")

            def iter_content(self, *a, **k):
                return iter(())

            def close(self):
                pass

        def request(session, method, url, *args, **kwargs):
            parts = [method, str(url), repr(args), repr(kwargs), repr(dict(getattr(session, "headers", {}) or {})), repr(getattr(session, "cookies", ""))]
            recorder.calls.append(" ".join(parts))
            return Response(str(url))

        def urlopen(target, *args, **kwargs):
            url = getattr(target, "full_url", target)
            if "127.0.0.1" in str(url):
                return real_urlopen(target, *args, **kwargs)
            recorder.calls.append(f"urlopen {url} {kwargs!r}")
            raise urllib.error.URLError("audit: network is faked")

        def connect(sock, address, *args, **kwargs):
            if is_loopback(address):
                return real_connect(sock, address, *args, **kwargs)
            raise OSError("audit: real network is blocked")

        def create_connection(address, *args, **kwargs):
            if is_loopback(address):
                return real_create(address, *args, **kwargs)
            raise OSError("audit: real network is blocked")

        def ask(*args, **kwargs):
            recorder.prompts += 1
            raise EOFError("audit: the service asked for interactive input")

        curl.Session.request = request
        requests.Session.request = request
        urllib.request.urlopen = urlopen
        socket_module.socket.connect = connect
        socket_module.create_connection = create_connection
        from rich.prompt import Confirm, Prompt

        Prompt.ask = staticmethod(ask)
        Confirm.ask = staticmethod(ask)
        import builtins

        builtins.input = ask


SHAPES = {
    # Mediaset exchanges a stored caToken + persona id for its token: the block must have that shape to reach the network.
    "mediasetinfinity": lambda p: {"mediasetinfinity": {"rtilogin_acd": {"caToken": f"{p}-caToken", "persona": {"id": f"{p}-persona"}}}},
}


def markers(prefix: str, scan: dict, site: str) -> dict:
    """A login for the sections/keys the service reads, every value tagged with *prefix*."""
    if site in SHAPES:
        return SHAPES[site](prefix)
    login = {}
    for section, keys in scan["sections"].items():
        if section == "<variable>":
            continue
        login[section] = {k: f"{prefix}-{section}-{k}" for k in (keys or set()) | set(COMMON_KEYS)}
    if not login:
        login[site] = {k: f"{prefix}-{site}-{k}" for k in COMMON_KEYS}
    return login


def find_marker(obj, needle: str, seen=None, depth=0) -> bool:
    seen = seen if seen is not None else set()
    if depth > 4 or id(obj) in seen:
        return False
    seen.add(id(obj))
    if isinstance(obj, str):
        return needle in obj
    if isinstance(obj, (bytes, bytearray)):
        return needle.encode() in bytes(obj)
    try:
        if isinstance(obj, dict):
            return any(find_marker(k, needle, seen, depth + 1) or find_marker(v, needle, seen, depth + 1) for k, v in list(obj.items()))
        if isinstance(obj, (list, tuple, set, frozenset)):
            return any(find_marker(v, needle, seen, depth + 1) for v in list(obj))
        if isinstance(obj, (types.ModuleType, type, types.FunctionType, types.BuiltinFunctionType, int, float, bool, type(None))):
            return False
        if hasattr(obj, "__dict__"):
            return find_marker(vars(obj), needle, seen, depth + 1)
    except Exception:  # noqa: BLE001 - an object that cannot be inspected is not evidence
        return False
    return False


def leftovers(site: str, needle: str) -> list[str]:
    """Module globals of the service that still hold *needle* after the request."""
    from VibraVid.remote.isolation import _service_modules

    found = []
    for module in _service_modules(site):
        for name, value in list(vars(module).items()):
            if name.startswith("__") or isinstance(value, (types.ModuleType, type, types.FunctionType)):
                continue
            if find_marker(value, needle):
                found.append(f"{module.__name__.split('.', 1)[-1]}.{name}")
    return found


def files_under(base: Path) -> list[str]:
    ignore = ("logs", "arr", "Conf", "ip.json", "_site_loader", "db.sqlite3", ".django_secret_key")
    out = []
    for p in base.rglob("*"):
        rel = p.relative_to(base).as_posix()
        if p.is_file() and not any(part in rel.split("/") or rel.endswith(part) for part in ignore) and "db" not in p.name:
            out.append(rel)
    return sorted(out)


def drive(site: str, scan: dict, ctx: dict) -> dict:
    from VibraVid.remote.client import RemoteClient
    from VibraVid.remote.config import RemoteServer
    from VibraVid.utils import config_manager

    recorder: Recorder = ctx["recorder"]
    result = {"error": None, "reached_network": False, "client_creds_used": False, "server_creds_leaked": False,
              "retried": [], "leftovers": [], "cross_identity_leak": False, "login_lines": 0, "wanted": []}
    before_login = json.dumps(config_manager.login._config_dict, sort_keys=True)

    def one(prefix):
        recorder.reset()
        client = RemoteClient(RemoteServer("audit", ctx["url"], ctx["key"]), timeout=60, login_data=markers(prefix, scan, site))
        err = None
        try:
            client.search(site, "audit query", {})
        except Exception as exc:  # noqa: BLE001 - the audit reports it
            err = f"{type(exc).__name__}: {exc}"
        return client, err, "\n".join(recorder.calls)

    try:
        a_client, a_err, a_calls = one(A_MARK)
        result["error"] = a_err
        result["reached_network"] = bool(a_calls)
        result["client_creds_used"] = f"{A_MARK}-" in a_calls
        result["server_creds_leaked"] = f"{SERVER_MARK}-" in a_calls
        result["retried"] = sorted(a_client._extra_sections.get(site, set()) | a_client._extra_cache.get(site, set()))
        result["leftovers"] = leftovers(site, f"{A_MARK}-")
        b_client, b_err, b_calls = one(B_MARK)
        result["cross_identity_leak"] = f"{A_MARK}-" in b_calls
        result["server_creds_leaked"] = result["server_creds_leaked"] or f"{SERVER_MARK}-" in b_calls
        result["leftovers"] = sorted(set(result["leftovers"]) | set(leftovers(site, f"{B_MARK}-")))
    finally:
        pass
    result["server_login_changed"] = json.dumps(config_manager.login._config_dict, sort_keys=True) != before_login
    result["prompts_hit"] = recorder.prompts
    return result


# ── main ──────────────────────────────────────────────────────────────────────

def verdict(static: dict, dyn: dict | None, loads: bool) -> tuple[str, list[str]]:
    notes = []
    if not loads:
        return "NOT LOADED", ["the service could not be loaded"]
    if static["import_time_reads"]:
        notes.append(f"reads the login at import time ({', '.join(static['import_time_reads'][:3])}): first value wins")
    if static["raw_login"]:
        notes.append(f"touches config_manager._login_data directly ({static['raw_login']}x): bypasses the per-client login")
    if static["file_writes"]:
        notes.append(f"{static['file_writes']} direct file write(s): not covered by the in-memory cache")
    if static["prompts"]:
        notes.append(f"{static['prompts']} interactive prompt(s): a server cannot answer them")
    if static["downloaders"] & {"Generic_Downloader", "YTDLPDownloader"}:
        notes.append("uses " + "/".join(sorted(static["downloaders"] & {"Generic_Downloader", "YTDLPDownloader"})) + ": cannot be resolved remotely")
    if static["callables"]:
        notes.append("callables kept on the server: " + ", ".join(sorted(static["callables"])))
    if "<variable>" in static["sections"]:
        notes.append("reads a login section by variable name (not statically known)")
    if dyn:
        if dyn["server_creds_leaked"]:
            notes.append("THE SERVER'S OWN CREDENTIALS REACHED THE NETWORK")
        if dyn["cross_identity_leak"]:
            notes.append("ONE CLIENT'S CREDENTIALS WERE USED FOR THE NEXT CLIENT")
        if dyn.get("server_login_changed"):
            notes.append("THE SERVER'S OWN LOGIN WAS MODIFIED")
        if dyn["leftovers"]:
            notes.append("identity still in memory after the request: " + ", ".join(dyn["leftovers"][:4]))
        if dyn["reached_network"] and not dyn["client_creds_used"] and static["sections"]:
            notes.append("reached the network but the client's credentials were not in any request (login probably needs a real token shape)")
        if not dyn["reached_network"]:
            notes.append(f"stopped before any request: {dyn['error']}")
    bad = any(n.isupper() or n.startswith(("THE SERVER", "ONE CLIENT")) for n in notes)
    hard = bad or static["import_time_reads"] or static["raw_login"]
    return ("PROBLEM" if hard else "CHECK" if notes else "OK"), notes


def discover(external: Path) -> dict:
    """service name -> folder, from the files (no import)."""
    folders = {}
    for source in (ROOT / "VibraVid" / "services", external):
        if source.is_dir():
            for init in sorted(source.glob("*/__init__.py")):
                if not init.parent.name.startswith("_"):
                    folders.setdefault(init.parent.name, init.parent)
    return folders


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--external", default=str(ROOT.parent / "VibraVid_Service"), help="folder with more services (an imp_service source)")
    parser.add_argument("--only", default="", help="comma-separated service names")
    parser.add_argument("--json", default="", help="write the full report here")
    args = parser.parse_args()
    external = Path(args.external)

    base = Path(tempfile.mkdtemp(prefix="vv_audit_"))
    (base / "Conf").mkdir()
    config = json.loads((ROOT / "Conf" / "config.json").read_text(encoding="utf-8"))
    config.setdefault("DEFAULT", {})["imp_service"] = ["default"] + ([str(external)] if external.is_dir() else [])
    (base / "Conf" / "config.json").write_text(json.dumps(config, indent=4), encoding="utf-8")
    shutil.copy(ROOT / "Conf" / "domains.json", base / "Conf" / "domains.json")
    folders = discover(external)
    statics = {name: scan_service(folder) for name, folder in folders.items()}
    server_login = {}
    for name, found in statics.items():
        for section, keys in markers(SERVER_MARK, found, name).items():
            server_login.setdefault(section, {}).update(keys)
    (base / "Conf" / "login.json").write_text(json.dumps(server_login), encoding="utf-8")  # a configured server: every section has an account
    os.environ.update({"VIBRAVID_BASE_PATH": str(base), "DJANGO_DB_DIR": str(base), "DJANGO_SETTINGS_MODULE": "webgui.settings"})
    for p in (ROOT, ROOT / "GUI"):
        sys.path.insert(0, str(p))

    import django

    django.setup()
    from django.core.management import call_command
    from django.core.wsgi import get_wsgi_application
    from wsgiref.simple_server import WSGIRequestHandler, make_server

    from searchapp import api_remote
    from VibraVid.remote import server as server_module
    from VibraVid.remote.config import RemoteUser, ServeSettings
    from VibraVid.services._base.site_loader import load_search_functions

    call_command("migrate", verbosity=0)
    recorder = Recorder()
    recorder.install()
    server_module._in_thread.__defaults__ = (25.0, None)  # a service stuck on something must not stall the audit

    key = "audit-key"
    settings = ServeSettings(users={key: RemoteUser("audit", server_cdm=True)})
    api_remote.load_serve_settings = lambda: settings

    class Quiet(WSGIRequestHandler):
        def log_message(self, *a):
            pass

    httpd = make_server("127.0.0.1", 0, get_wsgi_application(), handler_class=Quiet)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    ctx = {"recorder": recorder, "url": f"http://127.0.0.1:{httpd.server_port}", "key": key}

    functions = load_search_functions()
    load_errors = {}
    for func in functions.values():
        try:
            func.get_module()
        except Exception as exc:  # noqa: BLE001 - reported per service
            load_errors[func.module_name] = f"{type(exc).__name__}: {exc}"
    wanted_only = {s for s in args.only.split(",") if s}
    report = {}

    for site in sorted(statics):
        if wanted_only and site not in wanted_only:
            continue
        static = statics[site]
        loads = f"{site}_search" in functions and site not in load_errors
        dyn = None
        started = time.time()
        if loads:
            try:
                dyn = drive(site, static, ctx)
            except Exception as exc:  # noqa: BLE001
                dyn = {"error": f"audit crashed: {type(exc).__name__}: {exc}", "reached_network": False, "client_creds_used": False,
                       "server_creds_leaked": False, "cross_identity_leak": False, "leftovers": [], "retried": []}
        written = files_under(base)
        status, notes = verdict(static, dyn, loads) if loads else ("NOT LOADED", [load_errors.get(site) or "not found by the site loader"])
        if not loads and static["import_time_reads"]:
            notes.append(f"reads the login at import time ({', '.join(static['import_time_reads'][:3])})")
        if loads and written:
            notes.append("files written under the server's base path: " + ", ".join(written[:4]))
            status = "PROBLEM"
            shutil.rmtree(base / ".cache" / "services", ignore_errors=True)
        report[site] = {"status": status, "notes": notes, "static": {k: sorted(v) if isinstance(v, set) else v for k, v in (static or {}).items()}, "dynamic": dyn, "seconds": round(time.time() - started, 1)}

    httpd.shutdown()
    width = max(len(s) for s in report) if report else 10
    print(f"\n{'service':<{width}}  status      network  client-creds  server-creds  isolated  notes")
    for site, r in report.items():
        d = r["dynamic"] or {}
        yes = lambda v: "yes" if v else "no"  # noqa: E731
        print(f"{site:<{width}}  {r['status']:<10}  {yes(d.get('reached_network')):<7}  {yes(d.get('client_creds_used')):<12}  "
              f"{'LEAK' if d.get('server_creds_leaked') else 'never':<12}  {'NO' if (d.get('cross_identity_leak') or d.get('leftovers')) else 'yes':<8}  {len(r['notes'])}")
    print("\nDetails")
    for site, r in report.items():
        if r["notes"]:
            print(f"\n[{r['status']}] {site}")
            for note in r["notes"]:
                print(f"   - {note}")
    if args.json:
        Path(args.json).write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
        print(f"\nreport written to {args.json}")
    shutil.rmtree(base, ignore_errors=True)


if __name__ == "__main__":
    main()
