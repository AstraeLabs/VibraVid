# 01.04.26

import json
import locale
import logging
import os
import shutil
import subprocess
import tempfile
import threading
import time
import uuid
from collections.abc import Callable
from typing import Any

from VibraVid.core.ui.bar_manager import console
from VibraVid.setup import get_ffmpeg_path, get_flux_path

from ._models import EncryptionInfo, detect_encryption_info
from ._subprocess_runner import _ENGINE_LOG_LEVELS, _log_engine_output_enabled, _strip_profile_lines, run_with_progress
from .keys_manager import KeysManager

logger = logging.getLogger(__name__)

_TRANSIENT_OPEN_ERROR_MARKERS = (
    "cannot open",
    "could not open",
    "failed to open",
    "permission denied",
    "sharing violation",
    "being used by another process",
)
_OPEN_RETRY_ATTEMPTS = 6
_OPEN_RETRY_BASE_DELAY = 0.4
_OPEN_RETRY_MAX_DELAY = 3.0


def _is_transient_open_error(stderr_text: str | None) -> bool:
    """Check if the stderr text contains any known transient open error markers."""
    text = (stderr_text or "").lower()
    return any(marker in text for marker in _TRANSIENT_OPEN_ERROR_MARKERS)


def _open_retry_delay(attempt: int) -> float:
    """Calculate the delay before retrying to open a file, using exponential backoff."""
    return min(_OPEN_RETRY_BASE_DELAY * (2**attempt), _OPEN_RETRY_MAX_DELAY)


def _ansi_encodable(path: str) -> bool:
    """Check if the given path can be encoded in the system's preferred ANSI encoding."""
    if os.name != "nt":
        return True
    try:
        path.encode(locale.getpreferredencoding(False))
        return True
    except (UnicodeEncodeError, LookupError):
        return False


class _AnsiSafePathGuard:
    def __init__(self, encrypted_path: str, output_path: str, forbid_comma: bool = False):
        self.encrypted_path = encrypted_path
        self.output_path = output_path
        self.safe_encrypted_path = encrypted_path
        self.safe_output_path = output_path
        self.forbid_comma = forbid_comma
        self._linked_input = False

    def _needs_alias(self, path: str) -> bool:
        return not _ansi_encodable(path) or (self.forbid_comma and "," in path)

    def __enter__(self) -> "_AnsiSafePathGuard":
        if self._needs_alias(self.encrypted_path):
            ext = os.path.splitext(self.encrypted_path)[1]
            alias = os.path.join(tempfile.gettempdir(), f"vv_dec_in_{uuid.uuid4().hex}{ext}")
            try:
                os.link(self.encrypted_path, alias)
            except OSError:
                shutil.copy2(self.encrypted_path, alias)
            self.safe_encrypted_path = alias
            self._linked_input = True
            logger.debug(f"Input path unsafe for this tool (ANSI/comma), aliased via {alias}")

        if self._needs_alias(self.output_path):
            ext = os.path.splitext(self.output_path)[1]
            self.safe_output_path = os.path.join(tempfile.gettempdir(), f"vv_dec_out_{uuid.uuid4().hex}{ext}")
            logger.debug(f"Output path unsafe for this tool (ANSI/comma), aliased via {self.safe_output_path}")

        return self

    def finalize(self) -> None:
        """Move the aliased output (if any) back to the real output path."""
        if self.safe_output_path != self.output_path and os.path.exists(self.safe_output_path):
            try:
                os.replace(self.safe_output_path, self.output_path)
            except OSError:
                shutil.copy2(self.safe_output_path, self.output_path)
                os.remove(self.safe_output_path)

    def __exit__(self, exc_type, exc, tb) -> None:
        if self._linked_input and self.safe_encrypted_path != self.encrypted_path:
            try:
                os.remove(self.safe_encrypted_path)
            except OSError:
                pass
        if self.safe_output_path != self.output_path:
            try:
                if os.path.exists(self.safe_output_path):
                    os.remove(self.safe_output_path)
            except OSError:
                pass


class _FluxDaemon:
    def __init__(self, flux_path: str):
        self._proc = subprocess.Popen(
            [flux_path, "--daemon"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
            encoding="utf-8",
            errors="replace",
            creationflags=(subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0),
        )
        self._lock = threading.Lock()
        self._dead = False
        self._stderr_lines: list[str] = []
        self._stderr_lock = threading.Lock()
        self._job_count = 0
        self._last_job_lines: list[str] = []
        self._last_job_had_fragments_info = False
        threading.Thread(target=self._drain_stderr, daemon=True).start()

    def _drain_stderr(self) -> None:
        try:
            for line in self._proc.stderr:
                with self._stderr_lock:
                    self._stderr_lines.append(line)
        except Exception:
            pass

    def _take_job_stderr(self) -> list[str]:
        with self._stderr_lock:
            lines, self._stderr_lines = self._stderr_lines, []
        return lines

    def _log_job_output(self, lines: list[str], tag: str) -> None:
        if not lines:
            return
        level = _ENGINE_LOG_LEVELS.get("FLUX", logging.INFO)
        for line in lines:
            stripped = line.rstrip()
            if stripped:
                logger.log(level, f"[flux daemon, {tag}] {stripped}")

    def decrypt(
        self,
        input_path: str,
        output_path: str,
        keys: list[str],
        fragments_info: str | None,
        aes128: bool = False,
        key_sanity: bool = False,
        ffmpeg_path: str | None = None,
    ) -> tuple[bool, str | None]:
        """Run one job through the daemon. Returns (ok, error_message)."""
        if self._dead or self._proc.poll() is not None:
            self._dead = True
            return False, "daemon not running"

        job_dict: dict[str, Any] = {
            "input": input_path,
            "output": output_path,
            "keys": keys,
            "fragments_info": fragments_info,
            "aes128": aes128,
        }
        if key_sanity:
            job_dict["key_sanity"] = True
            job_dict["ffmpeg_path"] = ffmpeg_path
        job = json.dumps(job_dict)

        with self._lock:
            try:
                self._proc.stdin.write(job + "\n")
                self._proc.stdin.flush()
                line = self._proc.stdout.readline()
            except (BrokenPipeError, OSError) as exc:
                self._dead = True
                return False, f"daemon pipe error: {exc}"

        if not line:
            self._dead = True
            stderr_tail = ""
            try:
                stderr_tail = (self._proc.stderr.read() or "")[-300:]
            except Exception:
                pass
            return False, f"daemon exited unexpectedly ({stderr_tail or 'no stderr'})"

        self._job_count += 1
        self._last_job_had_fragments_info = fragments_info is not None
        if fragments_info is not None:
            self._take_job_stderr()
        else:
            if self._job_count == 1:
                time.sleep(0.05)
            job_lines = self._take_job_stderr()
            self._last_job_lines = job_lines  # overwritten every call -- close() logs whatever's here as "last"
            if _log_engine_output_enabled() and self._job_count == 1:
                self._log_job_output(job_lines, "first job")

        try:
            resp = json.loads(line)
        except json.JSONDecodeError as exc:
            return False, f"bad daemon response line {line.strip()!r}: {exc}"

        if resp.get("ok"):
            return True, None
        return False, resp.get("error") or "unknown daemon error"

    def close(self) -> None:
        if _log_engine_output_enabled() and self._job_count > 1 and not self._last_job_had_fragments_info:
            self._log_job_output(self._last_job_lines, f"last job, #{self._job_count}")

        if self._dead or self._proc.poll() is not None:
            return
        try:
            with self._lock:
                self._proc.stdin.write("--quit\n")
                self._proc.stdin.flush()
            self._proc.wait(timeout=5)
        except Exception:
            try:
                self._proc.kill()
            except Exception:
                pass


class Decryptor:
    def __init__(self) -> None:
        self.flux_path = get_flux_path()
        self._flux_daemon: _FluxDaemon | None = None
        self._flux_daemon_failed = False

    @staticmethod
    def _redacted_cmd(cmd: list[str]) -> str:
        """Redact sensitive information (like keys) from the command for logging purposes."""
        redacted = []
        hide_next = False
        for token in cmd:
            if hide_next:
                redacted.append("<redacted>")
                hide_next = False
                continue
            if token in {"--key", "--keys"}:
                redacted.append(token)
                hide_next = True
                continue
            redacted.append(token)
        return " ".join(redacted)

    def detect_encryption(self, file_path: str) -> tuple:
        """Detect the encryption scheme and related information for the given file.
        Returns ``(encrypted: bool, kid, pssh_b64, scheme)``."""
        logger.debug(f"Detecting encryption: {os.path.basename(file_path)}")
        info = detect_encryption_info(file_path)

        if not info.encrypted:
            logger.info("No encryption indicators found")
            return False, None, None, None

        logger.debug(f"Encryption finalized: scheme={info.scheme}, kid={info.kid}")
        return True, info.kid, info.pssh_b64, info.scheme

    def _decrypt_flux_nonlive(
        self,
        encrypted_path: str,
        normalized_keys: list[tuple[str, str]],
        output_path: str,
        label: str,
        progress_cb: Callable[[dict[str, Any]], None] | None = None,
        status: str | None = None,
        stream_type: str = "video",
    ) -> bool:
        """Decrypt a non-live (static) encrypted file using `flux` (`-f progressive`) — subprocess only"""
        if not self.flux_path:
            return False

        try:
            pairs = normalized_keys
            if not pairs:
                return False

            with _AnsiSafePathGuard(encrypted_path, output_path) as guard:
                cmd = [
                    self.flux_path,
                    "-i",
                    guard.safe_encrypted_path,
                    "-o",
                    guard.safe_output_path,
                    "-f",
                    "progressive"
                ]
                for kid, key in pairs:
                    cmd.extend(["--key", f"{kid.lower()}:{key.lower()}"])

                logger.info(f"flux cmd: {self._redacted_cmd(cmd)}")
                _flux_t0 = time.monotonic()

                result = None
                for attempt in range(_OPEN_RETRY_ATTEMPTS):
                    result = run_with_progress(
                        cmd,
                        label,
                        guard.safe_encrypted_path,
                        guard.safe_output_path,
                        progress_cb=progress_cb,
                        status=status,
                        engine_name="FLUX",
                        stream_type=stream_type,
                    )
                    if result is True:
                        break
                    stderr_text = result[1] if isinstance(result, tuple) else str(result)
                    if attempt < _OPEN_RETRY_ATTEMPTS - 1 and _is_transient_open_error(stderr_text):
                        logger.warning(f"flux could not open input (attempt {attempt + 1}/{_OPEN_RETRY_ATTEMPTS}), retrying: {stderr_text}")
                        time.sleep(_open_retry_delay(attempt))
                        continue
                    break

                if result is not True:
                    stderr_text = result[1] if isinstance(result, tuple) else str(result)
                    logger.debug(f"flux declined/failed after {time.monotonic() - _flux_t0:.1f}s: {stderr_text}")
                    return False

                if not os.path.exists(guard.safe_output_path) or os.path.getsize(guard.safe_output_path) <= 0:
                    logger.debug(f"flux reported success but output is missing/empty after {time.monotonic() - _flux_t0:.1f}s — falling back")
                    return False

                guard.finalize()
                logger.info(f"flux finished -> {os.path.basename(output_path)} in {time.monotonic() - _flux_t0:.1f}s ({os.path.getsize(output_path)} bytes)")
                return True

        except Exception as e:
            logger.warning(f"flux decrypt failed, falling back: {e}")
            return False

    def _get_flux_daemon(self) -> "_FluxDaemon | None":
        """Get or start the persistent flux daemon for live decryption, if available."""
        if self._flux_daemon_failed:
            return None
        if self._flux_daemon is not None:
            return self._flux_daemon
        if not self.flux_path:
            self._flux_daemon_failed = True
            return None
        try:
            self._flux_daemon = _FluxDaemon(self.flux_path)
            logger.debug("flux daemon started for this track's live decrypt")
            return self._flux_daemon
        except Exception as exc:
            logger.warning(f"flux daemon failed to start, falling back to one-shot spawns: {exc}")
            self._flux_daemon_failed = True
            return None

    def close_flux_daemon(self) -> None:
        """Stop this Decryptor's persistent flux daemon (if any) — call once a track's live decrypt is done."""
        if self._flux_daemon is not None:
            self._flux_daemon.close()
            self._flux_daemon = None

    def _decrypt_flux_live(
        self,
        encrypted_path: str,
        decrypted_path: str,
        normalized_keys: list[tuple[str, str]],
        init_path: str | None = None,
        key_sanity: bool = False,
    ) -> tuple:
        """Decrypt a live (streaming) encrypted segment using `flux`.

        Args:
            encrypted_path: Path to the encrypted segment file.
            decrypted_path: Path where the decrypted segment will be saved.
            normalized_keys: List of tuples containing (KID, raw_key) pairs for decryption.
            init_path: Optional path to the initialization segment (if applicable).
            key_sanity: If True, enables a post-decrypt check for wrong keys.
        """
        logger.debug(f"decrypt_flux_live(): {os.path.basename(encrypted_path)} -> {os.path.basename(decrypted_path)}")

        if not self.flux_path:
            return False, "Error flux: not available", None

        if not normalized_keys:
            logger.error("flux live decryption requested without usable keys")
            return False, "Error flux: no usable keys", None

        ffmpeg_path = get_ffmpeg_path() if key_sanity else None
        try:
            with _AnsiSafePathGuard(encrypted_path, decrypted_path) as guard:
                daemon = self._get_flux_daemon()
                if daemon is not None:
                    keys_arg = [f"{kid.lower()}:{raw_key.lower()}" for kid, raw_key in normalized_keys]
                    daemon_init = init_path if (init_path and os.path.exists(init_path)) else None
                    ok, err = daemon.decrypt(
                        guard.safe_encrypted_path, guard.safe_output_path, keys_arg, daemon_init,
                        key_sanity=key_sanity, ffmpeg_path=ffmpeg_path,
                    )

                    if ok:
                        size = os.path.getsize(guard.safe_output_path) if os.path.exists(guard.safe_output_path) else 0
                        if size <= 0:
                            return False, "Error flux: output file missing or empty", None
                        guard.finalize()
                        logger.debug(f"flux live segment decrypted successfully via daemon: {size} bytes")
                        return True, "flux live segment decrypted", None

                    if key_sanity and err and "key is wrong" in err:
                        return False, f"Error flux: {err}", None

                    logger.debug(f"flux daemon job failed ({err}), falling back to one-shot spawn for this segment")

                cmd = [self.flux_path]
                if init_path and os.path.exists(init_path):
                    cmd.extend(["--fragments-info", init_path])
                
                if key_sanity and ffmpeg_path:
                    cmd.extend(["--key-sanity-check", "--ffmpeg-path", ffmpeg_path])

                cmd.extend(["-i", guard.safe_encrypted_path, "-o", guard.safe_output_path, "-f", "progressive"])
                for kid, raw_key in normalized_keys:
                    cmd.extend(["--key", f"{kid.lower()}:{raw_key.lower()}"])

                logger.debug(f"flux live cmd: {self._redacted_cmd(cmd)}")
                result = subprocess.run(
                    cmd,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.PIPE,
                    text=True,
                    timeout=180,
                    encoding="utf-8",
                    errors="replace",
                    creationflags=(subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0),
                )

                if result.returncode != 0:
                    msg = _strip_profile_lines(result.stderr).strip() if result.stderr else "Unknown error"
                    logger.error(f"flux live decryption failed: {msg}")
                    return False, f"Error flux: {msg}", None

                size = os.path.getsize(guard.safe_output_path) if os.path.exists(guard.safe_output_path) else 0
                if size <= 0:
                    return False, "Error flux: output file missing or empty", None

                guard.finalize()
                logger.debug(f"flux live segment decrypted successfully: {size} bytes")
                return True, "flux live segment decrypted", None

        except Exception as exc:
            logger.error(f"Exception flux live: {exc}")
            return False, f"Exception flux: {exc}", None


    def decrypt(
        self,
        encrypted_path: str,
        keys,
        output_path: str,
        stream_type: str = "video",
        progress_cb: Callable[[dict[str, Any]], None] | None = None,
        detected: "EncryptionInfo | None" = None,
    ) -> bool:
        """Decrypt the given encrypted file using the provided keys and output to the specified path.

        ``detected`` allows callers that already ran encryption detection (e.g.
        ``PostDownloadDecryptor``) to skip a redundant ``flux -d -j`` scan.
        """
        try:
            if detected is not None:
                encrypted, kid, _pssh, scheme = (
                    detected.encrypted,
                    detected.kid,
                    detected.pssh_b64,
                    detected.scheme,
                )
            else:
                encrypted, kid, _pssh, scheme = self.detect_encryption(encrypted_path)
            norm_keys = KeysManager.normalize(keys)

            forced = not encrypted
            if forced:
                if not norm_keys:
                    logger.info("File appears clear and no keys provided: copying")
                    shutil.copy(encrypted_path, output_path)
                    return True

                if stream_type == "subtitle":
                    shutil.copy(encrypted_path, output_path)
                    return True

            norm_keys = KeysManager.resolve_fixed_key(encrypted_path, kid, norm_keys)
            if not norm_keys:
                logger.error("No valid keys available for decryption")
                return False

            norm_keys = KeysManager.resolve_placeholder_kid(kid, norm_keys)

            if kid and not KeysManager.is_zero_kid(kid):
                available_kids = {k.lower() for k, _ in norm_keys}
                if kid.lower() not in available_kids:
                    logger.error(f"No key matches required KID {kid} (have: {', '.join(k[:8] for k in available_kids)}); refusing to decrypt with mismatched keys")
                    return False

            method_display = (scheme or "unknown").upper().replace("_", "-")
            filename = os.path.basename(encrypted_path)

            label = f"[cyan]Dec[/cyan] [green]{filename}[/green] [[magenta]{method_display}[/magenta]] - [yellow]flux[/yellow]"
            ok = self._decrypt_flux_nonlive(
                encrypted_path, norm_keys, output_path, label,
                progress_cb=progress_cb, status=method_display, stream_type=stream_type,
            )

            if ok:
                return True

            logger.warning(f"flux failed/produced unusable output for {filename}")

            if forced:
                logger.error("Forced decryption failed with no detected encryption scheme; refusing to copy encrypted content as decrypted output.")
                return False

            return False

        except Exception as exc:
            logger.error(f"Decryption error: {exc}")
            console.print(f"[red]Decryption error: {exc}")
            return False

    def decrypt_segment_live(
        self, encrypted_path: str, decrypted_path: str, raw_keys, init_path: str | None = None, key_sanity: bool = False
    ) -> tuple:
        """Decrypt a live (streaming) encrypted segment using the provided keys and return a tuple indicating success and an optional error message."""
        norm_keys = KeysManager.normalize(raw_keys)
        logger.debug(f"decrypt_segment_live(): {os.path.basename(encrypted_path)} -> {os.path.basename(decrypted_path)} [LIVE -> flux]")
        return self._decrypt_flux_live(encrypted_path, decrypted_path, norm_keys, init_path=init_path, key_sanity=key_sanity)
