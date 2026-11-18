# In-app self-update logic.

import json
import logging
import os
import re
import shutil
import subprocess
import sys
import threading

from django.conf import settings

logger = logging.getLogger(__name__)

DOCKER_SOCKET = "/var/run/docker.sock"
PROJECT_MOUNT = "/project"
_UPDATE_STASH = os.path.join(os.environ.get("DJANGO_DB_DIR", "/app/data"), ".vibravid_update.json")


def _looks_poisoned(path: str) -> bool:
    """True if a compose path label points at the helper mount instead of a host path."""
    return (not path) or path == PROJECT_MOUNT or path.startswith(PROJECT_MOUNT + "/")


def _read_update_stash() -> dict:
    try:
        with open(_UPDATE_STASH, encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _write_update_stash(workdir: str, config_files: str) -> None:
    try:
        os.makedirs(os.path.dirname(_UPDATE_STASH), exist_ok=True)
        with open(_UPDATE_STASH, "w", encoding="utf-8") as fh:
            json.dump({"workdir": workdir, "config_files": config_files}, fh)
    except Exception as exc:
        logger.debug("Could not persist update stash: %s", exc)


def _is_docker() -> bool:
    if os.path.exists("/.dockerenv"):
        return True
    try:
        with open("/proc/1/cgroup") as fh:
            data = fh.read()
        return "docker" in data or "containerd" in data or "kubepods" in data
    except Exception:
        return False


def detect_mode() -> str:
    """Return one of: 'installer', 'docker', 'source'."""
    try:
        from VibraVid.setup import get_is_binary_installation

        if get_is_binary_installation():
            return "installer"
    except Exception:
        pass

    if _is_docker():
        return "docker"

    return "source"


def _self_container_id() -> str | None:
    """Best-effort discovery of the current container's id."""
    try:
        with open("/proc/self/mountinfo") as fh:
            for line in fh:
                m = re.search(r"/containers/([0-9a-f]{64})/", line)
                if m:
                    return m.group(1)
    except Exception:
        pass

    cid = os.environ.get("HOSTNAME", "")
    if len(cid) >= 12 and all(c in "0123456789abcdef" for c in cid.lower()):
        return cid
    try:
        with open("/proc/self/cgroup") as fh:
            for line in fh:
                parts = line.strip().split("/")
                for part in reversed(parts):
                    if len(part) >= 12 and all(c in "0123456789abcdef" for c in part):
                        return part
    except Exception:
        pass
    return None


def _remap_under(workdir: str, path: str, mount: str) -> str:
    """Map a host path under workdir to its location under the helper mount.

    Works for both POSIX (/opt/vibravid/...) and Windows (C:\\Users\\...) host
    paths: we strip the workdir prefix, normalise separators to POSIX and
    re-root under ``mount`` (e.g. /project/docker-compose.yml).
    """
    w = workdir.rstrip("\\/")
    if path.startswith(w):
        rel = path[len(w) :]
    else:
        rel = "/" + os.path.basename(path.replace("\\", "/"))
    rel = rel.replace("\\", "/").lstrip("/")
    return f"{mount}/{rel}" if rel else mount


def _update_docker() -> dict:
    if not os.path.exists(DOCKER_SOCKET):
        return {
            "success": False,
            "needs_manual": True,
            "message": (
                "Docker socket not mounted. Add "
                "'- /var/run/docker.sock:/var/run/docker.sock' to the "
                "container volumes and recreate it, or update manually with "
                "'docker compose pull && docker compose up -d'."
            ),
        }

    docker = shutil.which("docker")
    if not docker:
        return {
            "success": False,
            "needs_manual": True,
            "message": "The 'docker' CLI is not available in the container.",
        }

    cid = _self_container_id()
    if not cid:
        return {"success": False, "message": "Unable to determine the current container."}

    # Read compose labels + image ref of the running container.
    try:
        logger.info(f"Inspecting container {cid} for compose labels and image reference...")
        raw = subprocess.check_output(
            [docker, "inspect", "--format", "{{json .Config.Labels}}\n{{.Config.Image}}", cid],
            text=True,
            timeout=20,
            stderr=subprocess.STDOUT,
            encoding="utf-8",
            errors="replace",
        )
    except subprocess.CalledProcessError as exc:
        msg = (exc.output or "").strip()
        if "permission denied" in msg.lower():
            return {
                "success": False,
                "needs_manual": True,
                "message": "Permission denied on the Docker socket: the container user is not in the socket's group.",
            }
        return {"success": False, "message": f"docker inspect failed: {msg}"}
    except Exception as exc:
        return {"success": False, "message": f"docker inspect failed: {exc}"}

    labels_line, _, image_ref = raw.partition("\n")
    image_ref = image_ref.strip()
    try:
        labels = json.loads(labels_line) or {}
    except Exception:
        labels = {}

    project = labels.get("com.docker.compose.project")
    workdir = labels.get("com.docker.compose.project.working_dir")
    config_files = labels.get("com.docker.compose.project.config_files")
    if not project:
        return {
            "success": False,
            "needs_manual": True,
            "message": (
                "The container was not started with docker compose; update manually with 'docker compose pull && docker compose up -d'."
            ),
        }
    if not image_ref:
        return {"success": False, "message": "Unable to determine the container image."}

    # A previous in-app update may have poisoned the working_dir label to
    # PROJECT_MOUNT. Prefer the host path persisted on a clean run; fall back to
    # the label only when it still looks like a real host path.
    stash = _read_update_stash()
    if _looks_poisoned(workdir) and stash.get("workdir"):
        workdir = stash["workdir"]
        config_files = config_files or stash.get("config_files")

    if _looks_poisoned(workdir) or not config_files:
        return {
            "success": False,
            "needs_manual": True,
            "message": (
                "The container's compose labels were corrupted by a previous "
                "update (working_dir='/project'). Run "
                "'docker compose up -d' once from the project folder on the "
                "host to restore them, then the in-app update will work again."
            ),
        }

    _write_update_stash(workdir, config_files)
    project_mount = PROJECT_MOUNT
    targets = [_remap_under(workdir, cf.strip(), project_mount) for cf in config_files.split(",") if cf.strip()]
    if not targets:
        return {"success": False, "message": "No compose file found."}

    cfg_flags = " ".join(f"-f '{t}'" for t in targets)
    compose = f"docker compose -p '{project}' --project-directory '{project_mount}' {cfg_flags}"
    script = f"sleep 2; {compose} pull && {compose} up -d --remove-orphans"

    helper = [
        docker, "run", "-d", "--rm",
        "-v", f"{DOCKER_SOCKET}:{DOCKER_SOCKET}",
        "--mount", f"type=bind,source={workdir},target={project_mount}",
        "-w", project_mount,
        "--entrypoint", "sh",
        image_ref,
        "-c",
        script,
    ]

    logger.info(f"Launching Docker self-update helper: {helper}")
    try:
        subprocess.run(
            helper, check=True, timeout=30, capture_output=True, text=True,
            encoding="utf-8", errors="replace",
        )
    except subprocess.CalledProcessError as exc:
        return {"success": False, "message": f"Failed to start updater: {(exc.stderr or exc.stdout or '').strip()}"}
    except Exception as exc:
        return {"success": False, "message": f"Failed to start updater: {exc}"}

    logger.info("Docker self-update helper launched (image=%s)", image_ref)
    return {"success": True, "message": "Update started: pulling the image and recreating the container."}


def _update_installer() -> dict:
    try:
        from VibraVid.utils.upload.update import auto_update
    except Exception as exc:
        return {"success": False, "message": f"auto_update not available: {exc}"}

    # auto_update() downloads the new executable and calls sys.exit() to
    # relaunch; run it detached so we can still answer the HTTP request.
    threading.Thread(target=auto_update, daemon=True).start()
    return {"success": True, "message": "Update download started; the app will restart."}


def _repo_root() -> str | None:
    path = os.path.abspath(str(getattr(settings, "BASE_DIR", os.getcwd())))
    for _ in range(6):
        if os.path.isdir(os.path.join(path, ".git")):
            return path
        parent = os.path.dirname(path)
        if parent == path:
            break
        path = parent
    return None


def _delayed_reexec() -> None:
    import time

    time.sleep(2)
    os.execv(sys.executable, [sys.executable] + sys.argv)


def _update_source() -> dict:
    git = shutil.which("git")
    repo = _repo_root()
    if not git or not repo:
        return {"success": False, "needs_manual": True, "message": "Source installation without git: update manually."}

    # No remote -> nothing to pull (common for source downloads).
    logger.info(f"Checking git remotes for repo {repo}...")
    try:
        remotes = subprocess.check_output(
            [git, "-C", repo, "remote"], text=True, timeout=15,
            encoding="utf-8", errors="replace",
        ).strip()
    except Exception as exc:
        return {"success": False, "message": f"git remote failed: {exc}"}
    if not remotes:
        return {
            "success": False,
            "needs_manual": True,
            "message": ("No git remote configured. Add one with 'git remote add origin <url>' or update manually."),
        }

    try:
        out = subprocess.run(
            [git, "-C", repo, "pull", "--ff-only"], check=True, timeout=180, capture_output=True, text=True,
            encoding="utf-8", errors="replace",
        )
    except subprocess.CalledProcessError as exc:
        return {"success": False, "message": f"git pull failed: {(exc.stderr or exc.stdout or '').strip()}"}
    except Exception as exc:
        return {"success": False, "message": f"git pull failed: {exc}"}

    logger.info("Source update: %s", (out.stdout or "").strip())
    threading.Thread(target=_delayed_reexec, daemon=True).start()
    return {"success": True, "message": "Code updated; restarting the process."}


def perform_update() -> dict:
    mode = detect_mode()
    logger.info("Self-update requested (mode=%s)", mode)
    if mode == "installer":
        return _update_installer()
    if mode == "docker":
        return _update_docker()
    return _update_source()
