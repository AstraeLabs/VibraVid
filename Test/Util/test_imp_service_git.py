"""Offline contract of the git `imp_service` sources: quick check at start, background staging, explicit refresh."""

import importlib
import io
import os
import threading
import zipfile

import pytest

git_source = importlib.import_module("VibraVid.services._base.git_source")
SOURCE = "https://github.com/owner/repo#main"


def _zip(version: str) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        zf.writestr("repo-main/svc/__init__.py", f"indice = 1\n_useFor = 'Serie'\nVERSION = '{version}'\n")
    return buffer.getvalue()


class _Response:
    def __init__(self, payload=None, content=b""):
        self.payload, self.content, self.status_code = payload, content, 200

    def json(self):
        return self.payload

    def raise_for_status(self):
        return None


class _Remote:
    """GitHub stand-in: a latest commit sha and the zip served for it."""

    def __init__(self):
        self.sha, self.version, self.online, self.calls = "a" * 40, "v1", True, []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None

    def get(self, url, **kwargs):
        self.calls.append(url)
        if not self.online:
            raise ConnectionError("offline")
        if "/commits/" in url:
            return _Response({"sha": self.sha})
        if "codeload" in url:
            return _Response(content=_zip(self.version))
        return _Response({"default_branch": "main"})


@pytest.fixture
def remote(monkeypatch, tmp_path):
    fake = _Remote()
    monkeypatch.setattr(git_source.config_manager, "base_path", str(tmp_path))
    monkeypatch.setattr(git_source.config_manager.config, "get_int", lambda *a, **k: 15)
    monkeypatch.setattr(git_source, "create_client", lambda **kwargs: fake)
    monkeypatch.setattr(git_source, "_checked_this_run", set())
    monkeypatch.setattr(git_source, "_default_branches", {})
    return fake


def _version(base_path: str) -> str:
    with open(os.path.join(base_path, "svc", "__init__.py"), encoding="utf-8") as fh:
        return fh.read().split("VERSION = ")[1].strip().strip("'")


def _wait_staging():
    for thread in threading.enumerate():
        if thread.name == "imp-service-stage":
            thread.join(timeout=10)


def _new_process():
    git_source._checked_this_run.clear()


def test_first_use_downloads_synchronously(remote):
    path = git_source.resolve_remote_source(SOURCE)
    assert _version(path) == "v1"


def test_window_avoids_network_inside_a_run(remote):
    git_source.resolve_remote_source(SOURCE)
    remote.calls.clear()
    git_source.resolve_remote_source(SOURCE)
    assert remote.calls == []


def test_new_commit_is_staged_in_background_and_live_at_next_start(remote):
    path = git_source.resolve_remote_source(SOURCE)
    remote.sha, remote.version = "b" * 40, "v2"

    _new_process()
    assert _version(git_source.resolve_remote_source(SOURCE)) == "v1"  # start is never slowed down by the download
    _wait_staging()

    assert _version(git_source.resolve_remote_source(SOURCE)) == "v1"  # same run: files never change under imported modules

    _new_process()
    promoted = git_source.resolve_remote_source(SOURCE)
    assert _version(promoted) == "v2"
    assert promoted == path


def test_unchanged_commit_downloads_nothing(remote):
    git_source.resolve_remote_source(SOURCE)
    _new_process()
    remote.calls.clear()
    git_source.resolve_remote_source(SOURCE)
    assert not any("codeload" in url for url in remote.calls)


def test_offline_start_keeps_cached_copy(remote):
    git_source.resolve_remote_source(SOURCE)
    _new_process()
    remote.online = False
    assert _version(git_source.resolve_remote_source(SOURCE)) == "v1"


def test_refresh_applies_new_commit_immediately(remote):
    git_source.resolve_remote_source(SOURCE)
    remote.sha, remote.version = "c" * 40, "v3"

    [result] = git_source.refresh_remote_sources([SOURCE, "", "/local/folder"])
    assert result["updated"] is True and result["commit"] == "c" * 7 and result["error"] is None
    assert _version(git_source.resolve_remote_source(SOURCE)) == "v3"

    [again] = git_source.refresh_remote_sources([SOURCE])
    assert again["updated"] is False


def test_refresh_reports_failure(remote):
    remote.online = False
    [result] = git_source.refresh_remote_sources([SOURCE])
    assert result["error"]


def test_loader_skips_empty_imp_service_entries(monkeypatch, tmp_path):
    from VibraVid.services._base import site_loader

    monkeypatch.setattr(site_loader, "imp_sources", ["", "   "])
    assert site_loader.load_search_functions() == {}


# ── self-hosted Gitea behind http/ngrok ──────────────────────────────────────

@pytest.mark.parametrize("source", [
    "http://tester:pa?ss%23@host.example/admin/repo#main",
    "http://tester:pa%3Fss%2523@host.example/admin/repo#main",
])
def test_credentials_with_reserved_characters_stay_in_the_userinfo(source):
    parsed = git_source.urlparse(git_source._normalize_source(source))
    assert parsed.hostname == "host.example" and parsed.path == "/admin/repo" and parsed.fragment == "main"
    assert git_source.unquote(parsed.username) == "tester"
    assert git_source.redact(source) == "http://host.example/admin/repo"


def test_raw_and_encoded_question_mark_give_the_same_credentials():
    raw = git_source.urlparse(git_source._normalize_source("http://tester:asd1235e?1@host.example/admin/repo"))
    encoded = git_source.urlparse(git_source._normalize_source("http://tester:asd1235e%3F1@host.example/admin/repo"))
    assert git_source.unquote(raw.password) == git_source.unquote(encoded.password) == "asd1235e?1"


def test_non_github_archive_uses_the_api_route():
    url = git_source._archive_url("https", "git.example", "admin", "repo", "main", False)
    assert url == "https://git.example/api/v1/repos/admin/repo/archive/main.zip"


def test_repo_from_path_strips_dot_git_and_keeps_the_last_two_segments():
    assert git_source._repo_from_path("/admin/repo.git") == ("admin", "repo")
    assert git_source._repo_from_path("/git/admin/repo") == ("admin", "repo")
