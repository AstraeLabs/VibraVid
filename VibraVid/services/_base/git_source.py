# 07.08.26

import json
import logging
import os
import re
import shutil
import threading
import time
import zipfile
from urllib.parse import urlparse, urlunparse

from VibraVid.utils import config_manager
from VibraVid.utils.http_client import create_client, get_headers

logger = logging.getLogger(__name__)

_CACHE_ROOT = "imported_service"
_REFRESH_INTERVAL = 15 * 60
_STAGE_SUFFIX = ".new"
_SLUG_RE = re.compile(r"[^A-Za-z0-9._-]+")
_lock = threading.Lock()
_checked_this_run: set[str] = set()
_staging: set[str] = set()
_default_branches: dict[str, str] = {}


def is_remote_source(source: str) -> bool:
    """True if `source` is an http(s) URL to a GitHub/Gitea repository rather than a local path."""
    try:
        return urlparse(source).scheme in ("http", "https")
    except Exception:
        return False


def redact(source: str) -> str:
    """Strip any embedded credentials from a source URL, for safe logging."""
    try:
        parsed = urlparse(source)
        netloc = parsed.hostname or parsed.netloc.rsplit("@", 1)[-1]
        if parsed.port:
            netloc = f"{netloc}:{parsed.port}"
        return urlunparse((parsed.scheme, netloc, parsed.path, "", "", ""))
    except Exception:
        return source


def _slug(text: str) -> str:
    return _SLUG_RE.sub("_", text).strip("_") or "x"


def _repo_from_path(path: str) -> tuple[str, str] | None:
    parts = [p for p in path.split("/") if p]
    if len(parts) < 2:
        return None
    owner, repo = parts[0], parts[1]
    repo = repo[:-4] if repo.endswith(".git") else repo
    return owner, repo


def _cache_dir(netloc: str, owner: str, repo: str, ref: str) -> str:
    """Human-readable cache location: .cache/imported_service/<host>__<owner>__<repo>__<ref>/"""
    name = "__".join(_slug(part) for part in (netloc, owner, repo, ref))
    return os.path.join(config_manager.base_path, ".cache", _CACHE_ROOT, name)


def _read_manifest(manifest_path: str) -> dict | None:
    try:
        with open(manifest_path, encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def _write_manifest(manifest_path: str, data: dict) -> None:
    try:
        with open(manifest_path, "w", encoding="utf-8") as fh:
            json.dump(data, fh)
    except Exception as e:
        logger.warning(f"Could not persist imp_service manifest '{manifest_path}': {e}")


def _default_branch(client, api_base: str, owner: str, repo: str, is_github: bool, auth, timeout: float) -> str:
    key = f"{api_base}/{owner}/{repo}"
    if key in _default_branches:
        return _default_branches[key]

    url = f"https://api.github.com/repos/{owner}/{repo}" if is_github else f"{api_base}/api/v1/repos/{owner}/{repo}"
    try:
        response = client.get(url, auth=auth, timeout=timeout)
        if response.status_code == 200:
            branch = response.json().get("default_branch") or "main"
            _default_branches[key] = branch
            return branch
    except Exception as e:
        logger.debug(f"Could not resolve default branch for {owner}/{repo}: {e}")
    return "main"


def _remote_commit_sha(client, api_base: str, owner: str, repo: str, ref: str, is_github: bool, auth, timeout: float) -> str | None:
    """Cheap lookup of the latest commit sha for `ref`, used to skip re-downloading an unchanged repo."""
    try:
        if is_github:
            url = f"https://api.github.com/repos/{owner}/{repo}/commits/{ref}"
            response = client.get(url, auth=auth, timeout=timeout, headers={"Accept": "application/vnd.github+json"})
            if response.status_code == 200:
                return response.json().get("sha")
            return None

        response = client.get(f"{api_base}/api/v1/repos/{owner}/{repo}/branches/{ref}", auth=auth, timeout=timeout)
        if response.status_code == 200:
            return (response.json().get("commit") or {}).get("id")

        response = client.get(f"{api_base}/api/v1/repos/{owner}/{repo}/tags/{ref}", auth=auth, timeout=timeout)
        if response.status_code == 200:
            return (response.json().get("commit") or {}).get("sha")
    except Exception as e:
        logger.debug(f"Could not resolve latest commit for {owner}/{repo}#{ref}: {e}")

    return None


def _archive_url(scheme: str, netloc: str, owner: str, repo: str, ref: str, is_github: bool) -> str:
    if is_github:
        return f"https://codeload.github.com/{owner}/{repo}/zip/refs/heads/{ref}"
    return f"{scheme}://{netloc}/{owner}/{repo}/archive/{ref}.zip"


def _extract_single_root(zip_path: str, dest_dir: str) -> str | None:
    """Extract `zip_path` into `dest_dir`, unwrapping the single top-level folder GitHub/Gitea archives add."""
    if os.path.isdir(dest_dir):
        shutil.rmtree(dest_dir, ignore_errors=True)
    os.makedirs(dest_dir, exist_ok=True)

    with zipfile.ZipFile(zip_path) as zf:
        names = [n for n in zf.namelist() if n.strip()]
        if not names:
            return None
        top_levels = {name.split("/", 1)[0] for name in names}
        zf.extractall(dest_dir)

    if len(top_levels) == 1:
        wrapped = os.path.join(dest_dir, next(iter(top_levels)))
        if os.path.isdir(wrapped):
            return wrapped

    return dest_dir


def _extract_archive(content: bytes, dest_dir: str) -> str | None:
    """Write the zip `content` next to `dest_dir`, extract it there and drop the temporary zip."""
    tmp_zip = dest_dir + ".zip"
    try:
        os.makedirs(os.path.dirname(dest_dir), exist_ok=True)
        with open(tmp_zip, "wb") as fh:
            fh.write(content)
        return _extract_single_root(tmp_zip, dest_dir)
    finally:
        try:
            os.remove(tmp_zip)
        except OSError:
            pass


def _promote_pending(cache_dir: str, manifest_path: str, manifest: dict) -> dict:
    """Swap a staged update (`<cache_dir>.new`) in as the live copy; only called when no module of this run can be using it."""
    pending = manifest.get("pending")
    stage_dir = cache_dir + _STAGE_SUFFIX
    if not pending:
        return manifest

    updated = {k: v for k, v in manifest.items() if k != "pending"}
    if os.path.isdir(stage_dir):
        try:
            shutil.rmtree(cache_dir, ignore_errors=True)
            os.replace(stage_dir, cache_dir)
            updated.update(
                base_path=os.path.normpath(os.path.join(cache_dir, pending.get("rel", "."))),
                commit_sha=pending.get("commit_sha"),
                fetched_at=time.time(),
            )
            logger.info(f"imp_service update applied (commit {str(updated['commit_sha'])[:7]})")
        except OSError as e:
            logger.warning(f"Could not apply staged imp_service update '{stage_dir}': {e}")

    with _lock:
        _write_manifest(manifest_path, updated)
    return updated


def _request_timeout() -> int:
    return config_manager.config.get_int("REQUESTS", "timeout")


def _stage_update(archive_url: str, auth, cache_dir: str, manifest_path: str, remote_sha: str | None, label: str) -> None:
    """Background: download the new commit into `<cache_dir>.new`; it goes live at the next start or on an explicit refresh."""
    stage_dir = cache_dir + _STAGE_SUFFIX
    try:
        with create_client(headers=get_headers()) as client:
            response = client.get(archive_url, auth=auth, timeout=_request_timeout())
            response.raise_for_status()

        extracted = _extract_archive(response.content, stage_dir)
        if extracted is None:
            logger.warning(f"imp_service source '{label}' archive was empty")
            return

        with _lock:
            manifest = _read_manifest(manifest_path) or {}
            if remote_sha and remote_sha == manifest.get("commit_sha"):
                # An explicit refresh went live with this commit while the download was running
                shutil.rmtree(stage_dir, ignore_errors=True)
                return
            manifest["pending"] = {"commit_sha": remote_sha, "rel": os.path.relpath(extracted, stage_dir)}
            _write_manifest(manifest_path, manifest)
        logger.info(f"imp_service source '{label}': new commit downloaded, active at next start or with Refresh external import")
    except Exception as e:
        logger.warning(f"Background update of imp_service source '{label}' failed: {e}")
    finally:
        _staging.discard(cache_dir)


def _start_staging(archive_url: str, auth, cache_dir: str, manifest_path: str, remote_sha: str | None, label: str) -> None:
    with _lock:
        if cache_dir in _staging:
            return
        _staging.add(cache_dir)
    threading.Thread(
        target=_stage_update,
        args=(archive_url, auth, cache_dir, manifest_path, remote_sha, label),
        name="imp-service-stage",
        daemon=True,
    ).start()


def _resolve(source: str, force_refresh: bool = False) -> tuple[str | None, bool, str | None]:
    """Returns (local directory, updated now, commit sha) for an imp_service git `source`."""
    try:
        parsed = urlparse(source)
    except Exception as e:
        logger.error(f"Invalid imp_service git source: {e}")
        return None, False, None

    repo_info = _repo_from_path(parsed.path)
    if repo_info is None:
        logger.error(f"imp_service git source '{redact(source)}' must point to '<host>/<owner>/<repo>'")
        return None, False, None
    owner, repo = repo_info

    netloc = parsed.hostname or ""
    if parsed.port:
        netloc = f"{netloc}:{parsed.port}"
    clean_url = urlunparse((parsed.scheme, netloc, f"/{owner}/{repo}", "", "", ""))
    is_github = netloc.lower() in ("github.com", "www.github.com")
    api_base = f"{parsed.scheme}://{netloc}"

    username, password = parsed.username, parsed.password
    auth = (username, password or "") if username else None

    ref = parsed.fragment.strip() or None
    timeout = _request_timeout()

    with create_client(headers=get_headers()) as client:
        if ref is None:
            ref = _default_branch(client, api_base, owner, repo, is_github, auth, timeout)

        cache_dir = _cache_dir(netloc, owner, repo, ref)
        manifest_path = cache_dir + ".json"
        manifest = _read_manifest(manifest_path)
        have_cache = bool(manifest) and os.path.isdir(cache_dir) and os.path.isdir(manifest.get("base_path", ""))

        with _lock:
            first_access = cache_dir not in _checked_this_run
            _checked_this_run.add(cache_dir)

        # A staged update goes live only while nothing of this run has imported the old copy (first access) or on an explicit refresh
        promoted = False
        if have_cache and manifest.get("pending") and (first_access or force_refresh):
            promoted = True
            manifest = _promote_pending(cache_dir, manifest_path, manifest)
            have_cache = os.path.isdir(manifest.get("base_path", ""))

        archive_url = _archive_url(parsed.scheme, netloc, owner, repo, ref, is_github)

        if have_cache:
            age = time.time() - manifest.get("fetched_at", 0)
            if not force_refresh and not first_access and age < _REFRESH_INTERVAL:
                return manifest["base_path"], False, manifest.get("commit_sha")

            remote_sha = _remote_commit_sha(client, api_base, owner, repo, ref, is_github, auth, timeout)

            if remote_sha and remote_sha == manifest.get("commit_sha"):
                logger.debug(f"imp_service source '{clean_url}#{ref}' unchanged upstream (commit {remote_sha[:7]}), reusing cache")
                with _lock:
                    manifest["fetched_at"] = time.time()
                    _write_manifest(manifest_path, manifest)
                return manifest["base_path"], promoted, remote_sha

            if not force_refresh:
                # Keep serving the current copy: the newer commit is fetched in the background
                already_staged = remote_sha and remote_sha == (manifest.get("pending") or {}).get("commit_sha")
                if (remote_sha or age >= _REFRESH_INTERVAL) and not already_staged:
                    logger.info(f"imp_service source '{owner}/{repo}' ({ref}): newer commit found, downloading in background")
                    _start_staging(archive_url, auth, cache_dir, manifest_path, remote_sha, clean_url)
                return manifest["base_path"], False, manifest.get("commit_sha")
        else:
            remote_sha = _remote_commit_sha(client, api_base, owner, repo, ref, is_github, auth, timeout)

        logger.info(f"Fetching imp_service source '{owner}/{repo}' ({ref}) from {netloc}")

        try:
            response = client.get(archive_url, auth=auth, timeout=timeout)
            response.raise_for_status()
        except Exception as e:
            logger.error(f"Failed to download imp_service source '{clean_url}' ({ref}): {e}")
            if have_cache:
                logger.warning(f"Falling back to previously cached copy of '{clean_url}'")
                return manifest["base_path"], promoted, manifest.get("commit_sha")
            return None, False, None

    try:
        extracted = _extract_archive(response.content, cache_dir)
        if extracted is None:
            logger.error(f"imp_service source '{clean_url}' archive was empty")
            return None, False, None

        with _lock:
            _write_manifest(manifest_path, {"base_path": extracted, "ref": ref, "commit_sha": remote_sha, "fetched_at": time.time()})
            shutil.rmtree(cache_dir + _STAGE_SUFFIX, ignore_errors=True)  # a staged update is older than this copy
        return extracted, True, remote_sha
    except Exception as e:
        logger.error(f"Failed to extract imp_service source '{clean_url}': {e}")
        return None, False, None


def resolve_remote_source(source: str, force_refresh: bool = False) -> str | None:
    return _resolve(source, force_refresh)[0]


def refresh_remote_sources(sources: list[str]) -> list[dict]:
    results = []
    for source in sources:
        if not source.strip() or not is_remote_source(source):
            continue
        entry = {"source": redact(source), "updated": False, "commit": None, "error": None}
        try:
            path, updated, commit = _resolve(source, force_refresh=True)
            entry.update(updated=updated, commit=(commit or "")[:7] or None)
            if path is None:
                entry["error"] = "download failed"
        except Exception as e:
            entry["error"] = str(e)
        results.append(entry)
    return results

