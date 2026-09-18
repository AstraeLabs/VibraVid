# 09.06.24

import gc
import logging
import os
import signal
import threading
import time
import uuid
from collections import deque
from collections.abc import Callable
from functools import partial
from pathlib import Path
from typing import Any

from VibraVid.core.downloader._media_tokens import MEDIA_PLACEHOLDERS, strip_media_tokens
from VibraVid.core.muxing import embed_poster, inject_chapters
from VibraVid.core.muxing.helper.video import get_media_metadata
from VibraVid.core.ui.bar_manager import DownloadBarManager, console
from VibraVid.core.utils.codec import format_bitrate, format_disposition_flags
from VibraVid.core.ui.tracker import context_tracker, download_tracker
from VibraVid.utils import config_manager, internet_manager, os_manager
from VibraVid.utils.hooks import execute_hooks
from VibraVid.utils.http_client import create_client, get_userAgent
from VibraVid.utils.storage_upload.hook import is_cached, try_fetch, upload_after
from VibraVid.utils.vault.vault_1 import claudio_vault

from .util._drm_probe import PROBE_BYTES, PROBE_BYTES_FAST, DRMProbe
from .util._interrupt import InterruptHandler
from .util._live_frag_mp4 import LiveFragMp4Decryptor
from .util._post_decrypt import PostDownloadDecryptor

logger = logging.getLogger(__name__)

SKIP_DOWNLOAD = config_manager.config.get_bool("DOWNLOAD", "skip_download")
DELAY_SS = config_manager.config.get_int("DOWNLOAD", "delay_after_download")
SPEED_WINDOW_SECONDS = 1.0
LIVE_DECRYPT_MIN_SIZE = 25 * 1024 * 1024


class MP4FileDownloader:
    _probe = DRMProbe()
    _decryptor = PostDownloadDecryptor()

    def __init__(
        self,
        url: str,
        path: str,
        referer: str | None = None,
        headers: dict | None = None,
        download_id: str | None = None,
        site_name: str | None = None,
        label: str = "MP4",
        key: Any = None,
        max_percentage: float | None = None,
        chapters: list | None = None,
        poster_url: str | None = None,
        check_content_type: bool = True,
        sanitize_path: bool = True,
        close_tracking: bool = True,
        bar_mgr: "DownloadBarManager | None" = None,
        suppress_key_log: bool = False,
        expected_language: str | None = None,
        expected_forced: bool = False,
        on_clear_chunk: "Callable[[bytes], None] | None" = None,
        on_clear_abandon: "Callable[[], None] | None" = None,
    ) -> None:
        """
        Initialize the MP4FileDownloader.

        Args:
            url: The URL of the MP4 file to download.
            path: The local path where the file will be saved.
            referer: The referer header for the request.
            headers: Additional headers for the request.
            download_id: A unique identifier for the download.
            site_name: The name of the site from which the file is being downloaded.
            label: A label for the download task.
            key: The decryption key for the file.
            max_percentage: The maximum percentage of the file to download.
            chapters: A list of chapters to include in the download.
            poster_url: Poster/still image URL to embed in the final file. Default: context_tracker.poster_url.
            check_content_type: Whether to check the content type of the response.
            sanitize_path: Whether to sanitize the local path.
            close_tracking: Whether to close the GUI tracker entry on completion.
            bar_mgr: An already-entered, externally-owned DownloadBarManager to add this download's task

        Returns:
            None
        """
        self.url = str(url).strip()
        self.path = os_manager.get_sanitize_path(path) if sanitize_path else str(path)
        self._final_name_template = self.path
        self.path = self._strip_media_tokens(self.path)
        self.referer = referer
        self.headers = headers
        self._shared_bar_mgr = bar_mgr
        self._suppress_key_log = suppress_key_log
        self._expected_language = expected_language
        self._expected_forced = expected_forced
        self._on_clear_chunk = on_clear_chunk
        self._on_clear_abandon = on_clear_abandon
        self._task_key = label
        self.label = label
        self.key = key
        self.check_content_type = check_content_type
        self.close_tracking = close_tracking
        self.max_percentage = self._normalize_max_percentage(max_percentage)
        self.chapters = chapters if chapters is not None else context_tracker.chapters
        self.poster_url = context_tracker.poster_url or poster_url or context_tracker.fallback_poster_url
        context_tracker.poster_url = self.poster_url

        # Merge explicit args with context-level defaults
        self.download_id = download_id or context_tracker.download_id or str(uuid.uuid4())
        self.site_name = site_name or context_tracker.site_name
        self.media_type = context_tracker.media_type or "Film"

        # Internal state (reset per download() call)
        self._temp_path: str = f"{self.path}.temp"
        self._interrupt: InterruptHandler = InterruptHandler()
        self._total: int | None = None
        self._downloaded: int = 0
        self._incomplete_err: Any = False
        self._speed_window: deque[tuple[float, int]] = deque()

        # Live fMP4 decrypt (see util._live_frag_mp4): decrypts fragment-by-fragment
        self._live_frag: LiveFragMp4Decryptor | None = None
        self._live_decrypt_done: bool = False

        # In-flight DRM probe state
        self._probe_buf: bytearray = bytearray()
        self._probe_done: bool = False
        self._probe_encrypted: bool = False

        # Best-effort early media-metadata probe
        self._early_metadata: dict | None = None

    @staticmethod
    def _normalize_max_percentage(value: float | None) -> float:
        try:
            value_f = float(value)
        except (TypeError, ValueError):
            return 100.0

        if value_f <= 0:
            return 100.0
        if value_f > 100:
            return 100.0
        return value_f

    def download(self) -> tuple:
        """
        Execute the full pipeline.  Returns ``(path | None, interrupted: bool, error: Optional[str])``.
        """
        if not self._preflight():
            return None, False, None

        self._start_gui_tracking()
        headers = self._build_headers()
        out_dir = os.path.dirname(self.path)
        if out_dir:
            os.makedirs(out_dir, exist_ok=True)

        self._install_signal_handler()

        owns_bar = self._shared_bar_mgr is None
        bar_mgr = self._shared_bar_mgr or DownloadBarManager(self.download_id)

        def _run(progress_bars) -> tuple:
            try:
                progress_bars.add_prebuilt_tasks([(self._task_key, self.label)])
            except Exception:
                pass

            client = create_client(headers=headers)
            try:
                if self.check_content_type and not self._check_content_type(client, headers):
                    return None, False, None

                # Cheap early DRM probe (100 KB Range) — runs for every download
                # so audio (check_content_type=False) also gets the fast preflight
                # instead of the heavier 1 MB in-flight scan. When it detects
                # encryption it sets _probe_done, which skips the 1 MB in-flight probe.
                self._preflight_probe(client, headers)

                self._stream_to_disk(client, headers, bar_mgr)

            finally:
                client.close()

            return self._finalise(bar_mgr)

        if owns_bar:
            with bar_mgr as progress_bars:
                return _run(progress_bars)
        return _run(bar_mgr)

    def _preflight(self) -> bool:
        if SKIP_DOWNLOAD:
            console.print("[yellow]Download skipped due to configuration.")
            return False

        if os.path.exists(self.path):
            console.print("[yellow]File already exists.")
            return False

        if not (self.url.lower().startswith("http://") or self.url.lower().startswith("https://")):
            logger.error(f"Invalid URL: {self.url}")
            console.print(f"[red]Invalid URL: {self.url}")
            return False

        return True

    def _start_gui_tracking(self) -> None:
        if not self.download_id:
            return

        download_tracker.start_download(
            self.download_id,
            os.path.basename(self.path),
            self.site_name or "Unknown",
            self.media_type,
            path=os.path.abspath(self.path),
        )
        download_tracker.update_status(self.download_id, "Downloading ...")

    def _complete_tracking(self, success: bool, path: str | None = None, error: str | None = None) -> None:
        """Close the GUI entry, unless the caller took over the lifecycle (close_tracking=False)."""
        if not self.download_id or not self.close_tracking:
            return
        download_tracker.complete_download(self.download_id, success=success, path=path, error=error)

    def _build_headers(self) -> dict:
        headers: dict = {}
        if self.referer:
            headers["Referer"] = self.referer
        if self.headers:
            headers.update(self.headers)
        else:
            headers["User-Agent"] = get_userAgent()

        # Drop any inbound Range header: it usually survives from copied browser
        # requests (a seek) and would silently truncate the file — we always want
        # the full asset and manage ranges ourselves (probe).
        stripped = [k for k in headers if k.lower() == "range"]
        for k in stripped:
            headers.pop(k, None)
        if stripped:
            logger.warning(f"Ignoring inbound Range header ({', '.join(stripped)}) — downloading full file.")

        return headers

    def _install_signal_handler(self) -> None:
        try:
            if threading.current_thread() is threading.main_thread():
                prev = signal.getsignal(signal.SIGINT)
                signal.signal(signal.SIGINT, partial(self._interrupt.handle, original_handler=prev))
        except Exception:
            pass

    def _check_content_type(self, client, headers: dict) -> bool:
        try:
            head = client.head(self.url)
            head.raise_for_status()
            content_type = (head.headers.get("content-type") or "").lower()
        except Exception:
            content_type = ""

        if "text/html" not in content_type and "application/json" not in content_type:
            return True  # looks like a binary/media response → proceed

        logger.error("HEAD indicates non-video content type; inspecting body")
        try:
            resp = client.get(self.url)
            resp.raise_for_status()
            preview_text = resp.content[:2000].decode("utf-8", errors="replace")
            logger.info(f"Body preview: {preview_text}")
        except Exception as exc:
            logger.error(f"Fallback GET failed: {exc}")

        return False

    def _preflight_probe(self, client, headers: dict) -> None:
        """Cheap Range probe (PROBE_BYTES_FAST) run before the real download starts."""
        try:
            raw = self._probe.fetch(self.url, headers, client, size=PROBE_BYTES_FAST)
        except Exception as exc:
            logger.debug(f"Preflight probe failed (non-fatal): {exc}")
            return

        self._probe_done = True
        if not raw:
            return

        try:
            encrypted, scheme, is_widevine, kid, pssh_b64, metadata = self._probe.inspect_full(raw)
        except Exception as exc:
            logger.debug(f"Preflight DRM probe failed (non-fatal): {exc}")
            encrypted, scheme, is_widevine, kid, pssh_b64, metadata = False, None, False, None, None, {}

        if not encrypted:
            logger.info("Preflight probe: no encryption markers found — clear stream, skipping in-flight probe.")
        else:
            self._resolve_from_probe(encrypted, scheme, is_widevine, kid, pssh_b64)

        try:
            self._apply_early_metadata(metadata)
        except Exception as exc:
            logger.debug(f"Early metadata probe failed (non-fatal): {exc}")

    def _feed_probe(self, chunk: bytes) -> None:
        """Accumulate the first ~1 MB of the *live* download and inspect them in-flight
        (no second request). Runs the DRM check exactly once, then releases the buffer."""
        if self._probe_done or not chunk:
            return

        self._probe_buf += chunk
        if len(self._probe_buf) >= PROBE_BYTES:
            self._finish_probe()

    def _finish_probe(self) -> None:
        if self._probe_done:
            return
        self._probe_done = True

        raw = bytes(self._probe_buf[:PROBE_BYTES])
        self._probe_buf = bytearray()  # release memory regardless of outcome
        if not raw:
            return

        try:
            self._evaluate_probe(raw)
        except Exception as exc:
            logger.debug(f"In-flight DRM probe failed (non-fatal): {exc}")

    def _apply_early_metadata(self, metadata: dict) -> bool:
        """Apply expected_language/expected_forced overrides to the flux-derived metadata
        (from inspect_full) and build the progress-bar label from it.
        """
        if not any(metadata.get(k) for k in ("quality", "video_codec", "audio_codec", "sub_codec", "sub_language")):
            logger.debug("Early metadata probe inconclusive (moov atom not in first bytes) -- will resolve after download.")
            return False

        if self._expected_language:
            lang_up = self._expected_language.upper()
            if metadata.get("audio_tracks") or metadata.get("audio_codec"):
                metadata["language"] = lang_up
            if metadata.get("subtitle_tracks") or metadata.get("sub_codec"):
                metadata["sub_language"] = lang_up

        if self._expected_forced and metadata.get("sub_codec"):
            metadata["sub_forced"] = True

        self._early_metadata = metadata
        logger.info(f"Early metadata probe succeeded (in-flight): {metadata}")

        label = self._build_rich_label(metadata)
        if label:
            self.label = label
        return True

    @staticmethod
    def _build_rich_label(metadata: dict) -> str:
        """Rich-markup label for the progress bar, matching the "Vid [H.264, AAC] 480p 1.1 Mbps" /
        "Sub [vtt] en-US" style built by MediaDownloader._prepare_labels/_sub_stream_label for
        HLS/DASH streams (VibraVid/core/velora/base.py)"""
        vcodec = metadata.get("video_codec")
        quality = metadata.get("quality")
        if vcodec or quality:
            parts = []
            if vcodec:
                parts.append(f"[yellow]\\[{vcodec}][/yellow]")
            if quality:
                parts.append(f"[white]{quality}[/white]")
            bitrate = format_bitrate(metadata.get("video_bitrate"))
            if bitrate:
                parts.append(f"[blue]{bitrate}[/blue]")
            return f"[bold cyan]Vid[/bold cyan] {' '.join(parts)}"

        acodec = metadata.get("audio_codec")
        lang = metadata.get("language")
        if acodec or lang:
            parts = []
            if acodec:
                parts.append(f"[yellow]\\[{acodec}][/yellow]")
            if lang:
                parts.append(f"[bold white]{lang}[/bold white]")
            return f"[bold cyan]Aud[/bold cyan] {' '.join(parts)}"

        scodec = metadata.get("sub_codec")
        slang = metadata.get("sub_language")
        if scodec or slang:
            st = (metadata.get("subtitle_tracks") or [{}])[0]
            flags = format_disposition_flags(
                forced=bool(st.get("forced")) or bool(metadata.get("sub_forced")),
                sdh=bool(st.get("sdh")),
                cc=bool(st.get("cc")),
                default="DEFAULT" in (st.get("flags") or []),
            )
            parts = [f"[bold white]{slang}[/bold white]"] if slang else []
            if flags:
                parts.append(f"[bold red]{flags}[/bold red]")
            return f"[bold cyan]Sub[/bold cyan] [yellow]\\[{scodec or 'VTT'}][/yellow] {' '.join(parts)}"

        return "[bold cyan]MP4[/bold cyan]"

    def _evaluate_probe(self, raw: bytes) -> None:
        logger.info("Probing first 1 MB for DRM/encryption markers (in-flight)")
        encrypted, scheme, is_widevine, kid, pssh_b64, metadata = self._probe.inspect_full(raw)
        self._resolve_from_probe(encrypted, scheme, is_widevine, kid, pssh_b64)
        try:
            self._apply_early_metadata(metadata)
        except Exception as exc:
            logger.debug(f"Early metadata probe failed (non-fatal): {exc}")

    def _resolve_from_probe(
        self, encrypted: bool, scheme: str | None, is_widevine: bool, kid: str | None, pssh_b64: str | None
    ) -> None:
        """Shared outcome handling for both the fast preflight probe and the in-flight fallback"""
        if not encrypted:
            logger.info("Probe: no encryption markers found — clear stream.")
            return

        self._probe_encrypted = True
        drm_label = "Widevine" if is_widevine else (scheme or "unknown DRM")

        if not kid:
            if PostDownloadDecryptor.has_keys(self.key):
                logger.info(f"Probe: encrypted ({scheme or 'unknown'}, DRM=[{drm_label}]) — no KID found yet, keys present, will decrypt after download.")
            else:
                console.print(f"[yellow]Stream appears [red]encrypted[/red] ([cyan]{drm_label}[/cyan]), no KID found yet and no key provided.")
                logger.info(f"Probe: encrypted ({scheme or 'unknown'}, DRM=[{drm_label}]) — no KID, no manual key.")
            return

        from VibraVid.core.drm.manager import DRMManager

        mgr = DRMManager()
        resolved = mgr.resolve_flat_key(kid, pssh_b64, self.key, drm_type=scheme or "mp4")

        if resolved:
            resolved_key, source = resolved
            self.key = resolved_key
            if not self._suppress_key_log:
                if source == "manual":
                    mgr._display_keys([resolved_key], [], drm_label, pssh_b64, None, header=True, default_label="manual")
                else:
                    mgr._display_keys([resolved_key], [resolved_key], drm_label, pssh_b64, source, header=True)
            logger.info(f"Probe: encrypted ({scheme or 'unknown'}, DRM=[{drm_label}]) — key resolved (kid={kid}, source={source}).")
            return

        if PostDownloadDecryptor.has_keys(self.key):
            logger.info(f"Probe: encrypted ({scheme or 'unknown'}, DRM=[{drm_label}]) — keys present, will decrypt after download.")
            return

        console.print(f"[yellow]Stream appears [red]encrypted[/red] ([cyan]{drm_label}[/cyan]), no key in vault or provided.")
        logger.info(f"Probe: encrypted ({scheme or 'unknown'}, DRM=[{drm_label}]) — no manual key, none in vault.")

    def _stream_to_disk(self, client, headers: dict, bar_mgr: DownloadBarManager) -> None:
        response = client.get(self.url, stream=True)
        try:
            response.raise_for_status()
            self._total = self._parse_content_length(response)
            self._downloaded = 0
            self._incomplete_err = False
            self._probe_buf = bytearray()

            if self._total is None:
                logger.error("No Content-Length — streaming until connection closes.")

            live_worth_it = self._total is None or self._total >= LIVE_DECRYPT_MIN_SIZE
            with open(self._temp_path, "wb") as fh:
                # In-flight fragment decrypt is automatic: wire it whenever we have
                # keys and the stream is worth it; LiveFragMp4Decryptor inspects
                # the box structure and self-abandons (feed() -> False) for
                # anything that isn't a real self-initializing fMP4, letting the
                # post-download decrypt pass take over. `skip_post_decrypt` is the
                # debug override that disables every decrypt path.
                if live_worth_it and PostDownloadDecryptor.has_keys(self.key) and not context_tracker.skip_decrypt:
                    out_dir = Path(self._temp_path).resolve().parent
                    self._live_frag = LiveFragMp4Decryptor(fh, self.key, out_dir, on_chunk=self._on_clear_chunk)
                self._write_chunks(fh, response, bar_mgr, time.time(), bar_mgr)
        finally:
            response.close()

    @staticmethod
    def _parse_content_length(response) -> int | None:
        raw = response.headers.get("content-length")
        try:
            return int(raw) if raw is not None else None
        except Exception:
            return None


    def _write_chunks(self, fh, response, progress_bars, start_time: float, bar_mgr: DownloadBarManager) -> None:
        try:
            for chunk in response.iter_content(chunk_size=65536):
                if self._interrupt.force_quit or (self.download_id and download_tracker.is_stopped(self.download_id)):
                    console.print("\n[red]Force quitting... Saving partial download.")
                    if self.download_id and download_tracker.is_stopped(self.download_id):
                        self._incomplete_err = "cancelled"

                    break

                if chunk:
                    self._downloaded += len(chunk)
                    if self._live_frag is not None:
                        if not self._live_frag.feed(chunk):
                            fh.write(self._live_frag.finish())
                            self._live_frag.cleanup()
                            self._live_frag = None
                            if self._on_clear_abandon is not None:
                                try:
                                    self._on_clear_abandon()
                                except Exception:
                                    logger.debug("on_clear_abandon callback failed (non-fatal)", exc_info=True)
                    else:
                        fh.write(chunk)
                        if self._on_clear_chunk is not None and not self._probe_encrypted:
                            try:
                                self._on_clear_chunk(chunk)
                            except Exception:
                                logger.debug("on_clear_chunk callback failed (non-fatal)", exc_info=True)

                    self._feed_probe(chunk)
                    self._tick_progress(progress_bars, start_time, bar_mgr)

                    if self._should_stop_at_max_percentage():
                        self._incomplete_err = f"max_percentage_reached:{self.max_percentage:.2f}"
                        self._interrupt.kill_download = True
                        break

            if self._live_frag is not None and self._live_frag.active:
                fh.write(self._live_frag.finish())
                self._live_decrypt_done = True

        except KeyboardInterrupt:
            if not self._interrupt.force_quit:
                self._interrupt.kill_download = True

        except Exception as exc:
            self._incomplete_err = True
            self._interrupt.kill_download = True
            console.print(f"\n[red]Download error: {exc}. Saving partial download.")

        finally:
            if self._live_frag is not None:
                self._live_frag.cleanup(remove_init=not self._incomplete_err)
            if not self._probe_done:
                self._finish_probe()
            try:
                fh.flush()
                os.fsync(fh.fileno())
            except Exception:
                pass

    def _should_stop_at_max_percentage(self) -> bool:
        if self.max_percentage >= 100.0 or not self._total:
            return False
        return (self._downloaded / self._total * 100.0) >= self.max_percentage

    def _tick_progress(self, progress_bars, start_time: float, bar_mgr: DownloadBarManager) -> None:
        now = time.time()
        self._speed_window.append((now, self._downloaded))
        while len(self._speed_window) > 1 and now - self._speed_window[0][0] > SPEED_WINDOW_SECONDS:
            self._speed_window.popleft()

        window_start_at, window_start_bytes = self._speed_window[0]
        speed = (self._downloaded - window_start_bytes) / max(now - window_start_at, 0.001)
        speed_str = internet_manager.format_transfer_speed(speed) if speed > 0 else "-- B/s"
        downloaded_str = internet_manager.format_file_size(self._downloaded)
        percent = (self._downloaded / self._total * 100) if self._total else 0
        total_size_str = internet_manager.format_file_size(self._total) if self._total else "Unknown"
        pct_int = max(0, min(100, int(percent)))

        parsed = {
            "task_key": self._task_key,
            "pct": percent,
            "speed": speed_str,
            "size": f"{downloaded_str}/{total_size_str}",
            "segments": f"{pct_int}/100",
            "label": self.label,
            "display_label": self.label,
        }
        if self._early_metadata:
            parsed["quality"] = self._early_metadata.get("quality")
            parsed["language"] = self._early_metadata.get("language")

        try:
            if bar_mgr:
                bar_mgr.handle_progress_line(parsed)
        except Exception:
            try:
                download_tracker.update_progress(
                    self.download_id,
                    "video",
                    progress=parsed.get("pct"),
                    speed=parsed.get("speed"),
                    size=parsed.get("size"),
                    segments=parsed.get("segments"),
                )
            except Exception:
                pass

    def _run_decrypt(self, bar_mgr: DownloadBarManager) -> None:
        """Decrypt while continuing the same bar row in place."""

        def _decrypt_cb(parsed: dict[str, Any] | None) -> None:
            if not parsed:
                return
            bar_mgr.handle_progress_line(
                {
                    "task_key": self._task_key,
                    "pct": parsed.get("pct"),
                    "speed": parsed.get("status") or "Decrypt",
                }
            )

        self._decryptor.run(self.path, self.key, self.download_id, progress_cb=_decrypt_cb)

    def _finalise(self, bar_mgr: DownloadBarManager) -> tuple:

        # Temp file missing entirely
        if not os.path.exists(self._temp_path):
            console.print("[red]Download failed or file is empty.")
            self._complete_tracking(success=False, error="File missing or empty")
            return None, self._interrupt.kill_download, "File missing or empty"

        # Explicitly cancelled
        if self._incomplete_err == "cancelled":
            self._complete_tracking(success=False, error="cancelled")
            return None, True, "cancelled"

        # Explicit threshold stop requested by user/config
        if isinstance(self._incomplete_err, str) and self._incomplete_err.startswith("max_percentage_reached:"):
            if not self._rename_temp():
                return None, True, self._incomplete_err

            # Try decryption even on partial files when keys are available
            # (harmless no-op if the live fMP4 path already decrypted every
            # fragment it got through before the stop -- detect_encryption()
            # inside _run_decrypt correctly reports "not encrypted" on that
            # already-plaintext partial file)
            if context_tracker.skip_decrypt:
                logger.info(f"skip_post_decrypt: leaving {os.path.basename(self.path)} encrypted (kept for testing)")
            elif self._probe_encrypted or PostDownloadDecryptor.has_keys(self.key):
                self._run_decrypt(bar_mgr)

            self._resolve_media_tokens()

            self._complete_tracking(success=False, error=self._incomplete_err)
            return self.path, True, None

        # Atomic rename temp → final
        if not self._rename_temp():
            return None, self._interrupt.kill_download, None

        # Final file must exist now
        if not os.path.exists(self.path):
            console.print("[red]Download failed or file is empty.")
            self._complete_tracking(success=False, error="File missing or empty")
            return None, self._interrupt.kill_download, "File missing or empty"

        # Skip the size check when the live fMP4 path decrypted every fragment
        # in place: stripping CENC signaling (sinf/senc/saio/saiz) legitimately
        # shrinks the file below the still-encrypted Content-Length, which
        # would otherwise misreport a fully successful download as partial.
        if self._incomplete_err or (
            not self._live_decrypt_done and self._total and os.path.getsize(self.path) < self._total
        ):
            console.print("[yellow]Warning: download was incomplete (partial file saved).")

        # Post-download decryption (skipped if the live fMP4 path above already
        # decrypted every fragment as it arrived -- see _live_decrypt_done)
        if self._live_decrypt_done:
            logger.info(f"Live fragment decrypt already handled {os.path.basename(self.path)} -- skipping post-download decrypt pass")
        elif context_tracker.skip_decrypt:
            if self._probe_encrypted or PostDownloadDecryptor.has_keys(self.key):
                logger.info(f"skip_post_decrypt: leaving {os.path.basename(self.path)} encrypted (kept for testing)")
        elif PostDownloadDecryptor.has_keys(self.key):
            self._run_decrypt(bar_mgr)

        # Chapters, as the final muxing step (mirrors BaseDownloader._inject_chapters).
        if self.chapters:
            self.path, _ = inject_chapters(self.path, self.chapters)

        # Poster/still art, as the final muxing step (mirrors BaseDownloader._embed_poster).
        # The flag is checked here, not at resolution time -- see that method.
        from VibraVid.services._base.tmdb_artwork import embed_enabled

        if self.poster_url and embed_enabled():
            self.path, _ = embed_poster(self.path, self.poster_url)

        # Resolve media tokens (quality/codec/language) by probing the finished file.
        self._resolve_media_tokens()

        # Vault upload - must run before complete_download()
        upload_after(self.path)

        # GUI completion
        self._complete_tracking(success=True, path=os.path.abspath(self.path))

        # Analytics (fire-and-forget)
        claudio_vault.track_download_async(
            title=context_tracker.title or os.path.basename(self.path),
            media_type=self.media_type or "Film",
            service=self.site_name or "",
        )

        execute_hooks("post_run")
        if DELAY_SS > 0:
            console.print(f"\n[green]Sleeping {DELAY_SS} seconds before finishing...")
            time.sleep(DELAY_SS)

        return self.path, self._interrupt.kill_download, None

    _MEDIA_PLACEHOLDERS = MEDIA_PLACEHOLDERS

    @classmethod
    def _strip_media_tokens(cls, path: str) -> str:
        """Remove unresolved media-token placeholders from *path* (shared with BaseDownloader)."""
        return strip_media_tokens(path)

    def _resolve_media_tokens(self) -> None:
        """Probe the finished file and resolve media tokens (quality/codec/language) in self.path.

        MP4FileDownloader writes straight to the templated path, so placeholders
        like ``[%(quality)]`` survive unless we probe the muxed file here (the same
        way BaseDownloader._finalize does for segmented downloaders).
        """
        template = getattr(self, "_final_name_template", self.path)
        if not any(p in template for p in self._MEDIA_PLACEHOLDERS):
            return

        try:
            if self._early_metadata is not None:
                metadata = self._early_metadata
                logger.info(f"Reusing early-probed metadata for dynamic rename: {metadata}")
            else:
                metadata = get_media_metadata(self.path)
                logger.info(f"Metadata for dynamic rename: {metadata}")

            replacements = {
                "quality": metadata.get("quality", ""),
                "language": metadata.get("language", ""),
                "video_codec": metadata.get("video_codec", ""),
                "audio_codec": metadata.get("audio_codec", ""),
                "audio_flags": metadata.get("audio_flags", ""),
                "sub_flags": metadata.get("sub_flags", ""),
            }

            new_root = os.path.splitext(template)[0]
            cur_ext = os.path.splitext(self.path)[1]
            for key, val in replacements.items():
                placeholder = f"%({key})"
                if val:
                    new_root = new_root.replace(placeholder, str(val))
                else:
                    new_root = new_root.replace(f" [{placeholder}]", "").replace(f"[{placeholder}]", "")
                    new_root = new_root.replace(f" ({placeholder})", "").replace(f"({placeholder})", "")
                    new_root = new_root.replace(placeholder, "")

            new_root = new_root.replace("  ", " ").rstrip(" .")
            new_path = new_root + cur_ext

            if new_path != self.path:
                new_dir = os.path.dirname(new_path)
                if new_dir and not os.path.exists(new_dir):
                    os.makedirs(new_dir, exist_ok=True)

                # os.replace (not os.rename) so re-downloading overwrites on Windows.
                os.replace(self.path, new_path)
                self.path = new_path
                logger.info(f"Dynamic rename applied: {self.path}")

        except Exception as exc:
            console.print(f"[yellow]Warning: Dynamic rename failed: {exc}")

    def _rename_temp(self) -> bool:
        last_exc = None
        for attempt in range(10):
            try:
                os.replace(self._temp_path, self.path)
                return True
            except PermissionError as exc:
                last_exc = exc
                console.log(f"[yellow]Rename attempt {attempt + 1}/10 failed: {exc}")
                time.sleep(0.5)
                gc.collect()

        console.print(f"[red]Could not rename temp file after 10 retries: {last_exc}")
        return False


def MP4_Downloader(
    url: str,
    path: str,
    referer: str | None = None,
    headers: dict | None = None,
    download_id: str | None = None,
    site_name: str | None = None,
    label: str = "MP4",
    key: Any = None,
    max_percentage: float | None = None,
    chapters: list | None = None,
    poster_url: str | None = None,
    check_content_type: bool = True,
    sanitize_path: bool = True,
    close_tracking: bool = True,
    bar_mgr: "DownloadBarManager | None" = None,
    suppress_key_log: bool = False,
    expected_language: str | None = None,
    expected_forced: bool = False,
    on_clear_chunk: "Callable[[bytes], None] | None" = None,
    on_clear_abandon: "Callable[[], None] | None" = None,
) -> tuple:
    """Backward-compatible entry point — wraps ``MP4FileDownloader.download()``."""
    if context_tracker.resolve_only:
        from VibraVid.cli.command.queue import enqueue_down_from_context

        enqueue_down_from_context(url, path)
        return path, False, None

    if is_cached():
        console.print("[dim]Skipping — already in cache.")
        return path, False, None

    if try_fetch(path):
        return path, False, None

    result = MP4FileDownloader(
        url=url,
        path=path,
        referer=referer,
        headers=headers,
        download_id=download_id,
        site_name=site_name,
        label=label,
        key=key,
        max_percentage=max_percentage,
        chapters=chapters,
        poster_url=poster_url,
        check_content_type=check_content_type,
        sanitize_path=sanitize_path,
        close_tracking=close_tracking,
        bar_mgr=bar_mgr,
        suppress_key_log=suppress_key_log,
        expected_language=expected_language,
        expected_forced=expected_forced,
        on_clear_chunk=on_clear_chunk,
        on_clear_abandon=on_clear_abandon,
    ).download()

    return result