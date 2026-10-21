# 24.01.24

import logging
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager

from pathvalidate import sanitize_filename, sanitize_filepath
from rich.console import Console
from rich.prompt import Prompt
from unidecode import unidecode

msg = Prompt()
console = Console()
logger = logging.getLogger(__name__)

_TRASH_SUFFIX = re.compile(r"\.trash-(\d+)-[0-9a-f]{8}$")
_WIN_LONG_PATH = 246


class OsManager:
    def __init__(self):
        from ..setup.binary_paths import binary_paths

        self.system = binary_paths._detect_system()
        self.max_length = self._get_max_length()

    def _get_max_length(self) -> int:
        """Get max filename length based on OS."""
        return 255 if self.system == "windows" else 4096

    def get_sanitize_file(self, filename: str, year: str = None) -> str:
        """Sanitize filename. Optionally append a year in format ' (YYYY)' if year is provided and valid."""
        if not filename:
            return filename

        # Extract and validate year if provided
        year_str = ""
        if year:
            y = str(year).split("-")[0].strip()
            if y.isdigit() and len(y) == 4:
                year_str = f" ({y})"

        # Decode and sanitize base filename
        decoded = unidecode(filename)
        sanitized = sanitize_filename(decoded)

        # Split name and extension
        name, ext = os.path.splitext(sanitized)

        # Append year if present
        name_with_year = name + year_str

        # Calculate available length for name considering the '...' and extension
        max_name_length = self.max_length - len("...") - len(ext)

        # Truncate name if it exceeds the max name length
        if len(name_with_year) > max_name_length:
            name_with_year = name_with_year[:max_name_length] + "..."

        # Ensure the final file name includes the extension
        return name_with_year + ext

    def get_sanitize_path(self, path: str) -> str:
        """Sanitize a complete path while preserving the native OS path separator."""
        if not path:
            return path

        # Decode unicode characters first (unidecode is safe on separators and drive letters — it only touches non-ASCII glyphs).
        decoded = unidecode(path)

        if self.system == "windows":
            normalised = decoded.replace("/", "\\")

            # Handle network paths (UNC or IP-based)  \\server\share\...
            if normalised.startswith("\\\\"):
                parts = normalised.split("\\")
                sanitized_parts = parts[:4]
                if len(parts) > 4:
                    sanitized_parts.extend([self.get_sanitize_file(part) for part in parts[4:] if part])
                return "\\".join(sanitized_parts)

            # Handle drive letters  C:\...
            if len(normalised) >= 2 and normalised[1] == ":":
                drive = normalised[:2]  # e.g. "C:"
                rest = normalised[2:].lstrip("\\")
                parts = [p for p in rest.split("\\") if p]
                sanitized_parts = [drive] + [self.get_sanitize_file(p) for p in parts]
                return "\\".join(sanitized_parts)

            # Regular relative path
            parts = [p for p in normalised.split("\\") if p]
            return "\\".join(self.get_sanitize_file(p) for p in parts)

        else:
            sanitized = sanitize_filepath(decoded)
            is_absolute = sanitized.startswith("/")
            parts = sanitized.replace("\\", "/").split("/")
            sanitized_parts = [self.get_sanitize_file(part) for part in parts if part]
            result = "/".join(sanitized_parts)
            if is_absolute:
                result = "/" + result
            return result

    def get_glob_path(self, path: str) -> str:
        """Escape path for glob to prevent issues with special characters like brackets."""
        import glob

        return glob.escape(path)

    def create_path(self, path: str, mode: int = 0o755) -> bool:
        """Create directory path with specified permissions."""
        try:
            os.makedirs(str(path), mode=mode, exist_ok=True)
            return True

        except Exception as e:
            logger.error(f"Path creation error: {e}")
            return False

    def fast_rmtree(self, path: str) -> None:
        """Remove *path* without blocking the caller for the full delete."""
        if not path or not os.path.isdir(path):
            return

        path = os.path.abspath(path)
        trash_path = f"{path}.trash-{os.getpid()}-{uuid.uuid4().hex[:8]}"
        if self.system == "windows" and len(trash_path) >= _WIN_LONG_PATH:
            logger.warning(f"fast_rmtree: {len(trash_path)}-character path is over the Windows limit, using the extended-length form: {trash_path!r}")

        src, dst = self._extended_path(path), self._extended_path(trash_path)
        last_exc: OSError | None = None
        for attempt in range(5):
            try:
                os.rename(src, dst)
                last_exc = None
                break
            except OSError as exc:
                last_exc = exc
                if attempt < 4:
                    time.sleep(0.1)

        if last_exc is not None:
            logger.warning(f"fast_rmtree: could not rename {path!r} aside ({last_exc}) after 5 attempts -- falling back to a direct (blocking) delete")
            if not self._rmtree_logged(src):
                logger.warning(f"fast_rmtree: {path!r} still exists after cleanup -- a file inside it is likely locked by another process")
            return

        self._delete_detached(dst)

    def _extended_path(self, path: str) -> str:
        """Windows extended-length form (``\\\\?\\``) of an absolute *path* that is too long for the regular API; unchanged elsewhere and for short paths."""
        if self.system != "windows" or len(path) < _WIN_LONG_PATH or path.startswith("\\\\?\\"):
            return path
        if path.startswith("\\\\"):
            return "\\\\?\\UNC\\" + path[2:]
        return "\\\\?\\" + path

    def _rmtree_logged(self, path: str) -> bool:
        """Blocking ``rmtree`` that clears read-only flags and retries, and logs every file it could not delete. Returns True once *path* is gone."""
        def _on_error(func, failed_path, exc) -> None:
            err = exc[1] if isinstance(exc, tuple) else exc  # onerror passes exc_info, onexc the exception
            if isinstance(err, FileNotFoundError):
                return  # already removed (e.g. by a concurrent detached delete)

            if isinstance(err, PermissionError):
                try:
                    if os.name != "nt":
                        os.chmod(os.path.dirname(failed_path), stat.S_IRWXU)
                    os.chmod(failed_path, stat.S_IRWXU if os.path.isdir(failed_path) else stat.S_IWRITE | stat.S_IREAD)
                    func(failed_path)
                    return
                except FileNotFoundError:
                    return
                except OSError as retry_exc:
                    err = retry_exc

            logger.warning(f"fast_rmtree: could not delete {failed_path!r}: {err} (errno={getattr(err, 'errno', None)}, winerror={getattr(err, 'winerror', None)}) -- the file is read-only, locked by another process (antivirus, indexer, Explorer preview) or access is denied")

        if sys.version_info >= (3, 12):
            shutil.rmtree(path, onexc=_on_error)
        else:
            shutil.rmtree(path, onerror=_on_error)
        return not os.path.isdir(path)

    def sweep_trash(self, parent_dir: str, min_age: float = 60.0) -> None:
        """Delete ``*.trash-<pid>-<id>`` folders that an earlier run's detached delete left in *parent_dir* (blocking, with every failure logged). Never raises."""
        try:
            if not parent_dir or not os.path.isdir(parent_dir):
                return

            now = time.time()
            for entry in os.scandir(parent_dir):
                match = _TRASH_SUFFIX.search(entry.name)
                # This process's own trash is already being deleted by its detached `rd` / `rm`
                if not match or int(match.group(1)) == os.getpid() or not entry.is_dir(follow_symlinks=False):
                    continue
                if now - entry.stat().st_mtime < min_age:
                    continue

                logger.info(f"fast_rmtree: removing leftover {entry.path!r} from an earlier run")
                if not self._rmtree_logged(entry.path):
                    logger.warning(f"fast_rmtree: leftover {entry.path!r} could not be fully removed")
        except Exception as exc:
            logger.warning(f"fast_rmtree: sweep of {parent_dir!r} failed: {exc}")

    def _in_kill_on_close_job(self) -> bool:
        """True when this process sits in a Windows Job object that kills its processes on close and forbids breakaway: a detached child would die with us."""
        if self.system != "windows":
            return False

        try:
            import ctypes
            from ctypes import wintypes

            class _BasicLimits(ctypes.Structure):
                _fields_ = [
                    ("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64),
                    ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                    ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                    ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD), ("SchedulingClass", wintypes.DWORD),
                ]

            class _ExtendedLimits(ctypes.Structure):
                _fields_ = [
                    ("BasicLimitInformation", _BasicLimits), ("IoInfo", ctypes.c_uint64 * 6),
                    ("ProcessMemoryLimit", ctypes.c_size_t), ("JobMemoryLimit", ctypes.c_size_t),
                    ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t),
                ]

            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel32.GetCurrentProcess.restype = wintypes.HANDLE
            kernel32.IsProcessInJob.argtypes = [wintypes.HANDLE, wintypes.HANDLE, ctypes.POINTER(wintypes.BOOL)]
            kernel32.QueryInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD, ctypes.c_void_p]

            in_job = wintypes.BOOL()
            if not kernel32.IsProcessInJob(kernel32.GetCurrentProcess(), None, ctypes.byref(in_job)) or not in_job.value:
                return False

            info = _ExtendedLimits()
            # JobObjectExtendedLimitInformation = 9; a NULL job handle means "the job of the calling process"
            if not kernel32.QueryInformationJobObject(None, 9, ctypes.byref(info), ctypes.sizeof(info), None):
                return False

            flags = info.BasicLimitInformation.LimitFlags
            kill_on_close, breakaway_ok, silent_breakaway_ok = 0x2000, 0x800, 0x1000
            return bool(flags & kill_on_close) and not flags & (breakaway_ok | silent_breakaway_ok)
        except Exception:
            return False

    def _delete_detached(self, path: str) -> None:
        """Delete *path* in a process detached from this one, so it survives this process exiting."""
        try:
            if self.system == "windows":
                # cwd is pinned: cmd.exe cannot start in a UNC directory and would fall back to C:\Windows
                kwargs = {"cwd": os.environ.get("SystemRoot") or None, "close_fds": True}
                cmd = ["cmd", "/c", "rd", "/s", "/q", path]
                flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP

                if self._in_kill_on_close_job():
                    logger.warning(f"fast_rmtree: running inside a Job object that kills child processes on exit -- the background delete of {path!r} may be interrupted")
                try:
                    # Leave a Job object that allows it, otherwise the "detached" delete dies with its parent
                    subprocess.Popen(cmd, creationflags=flags | subprocess.CREATE_BREAKAWAY_FROM_JOB, **kwargs)
                except OSError:
                    subprocess.Popen(cmd, creationflags=flags, **kwargs)  # the job forbids breakaway
            else:
                subprocess.Popen(["rm", "-rf", path], start_new_session=True, close_fds=True)
        except OSError as exc:
            logger.warning(f"fast_rmtree: could not spawn a detached delete for {path!r} ({exc}) -- deleting on a background thread instead")
            threading.Thread(target=shutil.rmtree, args=(path,), kwargs={"ignore_errors": True}, daemon=True).start()

    @staticmethod
    @contextmanager
    def temp_binary_file(data: bytes, suffix: str = "") -> Iterator[str]:
        """Write *data* to a temporary file, yield its path, delete it on exit."""
        tmp_path = None
        try:
            with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
                tmp.write(data)
                tmp_path = tmp.name
            yield tmp_path
        finally:
            if tmp_path:
                try:
                    os.unlink(tmp_path)
                except Exception:
                    pass

class InternetManager:
    def format_file_size(self, size_bytes) -> str:
        """Format *size_bytes* (int or float) as a human-readable size string."""
        try:
            nb = float(size_bytes)
        except (TypeError, ValueError):
            return "0"

        if nb <= 0:
            return "0"

        if nb >= 1024**4:
            return f"{nb / 1024**4:.2f}T"
        if nb >= 1024**3:
            return f"{nb / 1024**3:.2f}G"
        if nb >= 1024**2:
            return f"{nb / 1024**2:.1f}M"
        if nb >= 1024:
            return f"{nb / 1024:.0f}K"
        return f"{nb:.0f}"

    def format_transfer_speed(self, bytes_per_second) -> str:
        """
        Format *bytes_per_second* (int or float) as a human-readable speed string.
        Always appends ``/s``.  Returns ``"0 B/s"`` for zero or negative values.
        """
        try:
            bps = float(bytes_per_second)
        except (TypeError, ValueError):
            return "0/s"

        if bps <= 0:
            return "0/s"
        if bps >= 1024**3:
            return f"{bps / 1024**3:.2f}G/s"
        if bps >= 1024**2:
            return f"{bps / 1024**2:.2f}M/s"
        if bps >= 1024:
            return f"{bps / 1024:.0f}K/s"
        return f"{bps:.0f}/s"

    def format_time(self, seconds: float, add_hours: bool = False) -> str:
        """Format seconds to MM:SS or HH:MM:SS."""
        if seconds < 0 or seconds == float("inf"):
            return "00:00"

        minutes = int(seconds // 60)
        secs = int(seconds % 60)
        if add_hours:
            hours = int(minutes // 60)
            minutes = int(minutes % 60)
            return f"{hours:02d}:{minutes:02d}:{secs:02d}"
        return f"{minutes:02d}:{secs:02d}"


# Initialize
os_manager = OsManager()
internet_manager = InternetManager()
