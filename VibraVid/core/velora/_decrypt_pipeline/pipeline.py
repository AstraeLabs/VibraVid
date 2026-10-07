# 01.04.25

import threading
import time
from collections import Counter
from pathlib import Path

from rich.text import Text

from VibraVid.core.decryptor import Decryptor
from VibraVid.core.muxing.helper.video import (
    binary_merge_segments,
    concat_demux_merge_segments,
)
from VibraVid.core.ui.bar_manager import DownloadBarManager
from VibraVid.utils import config_manager

from ..util._stream_helpers import (
    build_retry_segments,
    collect_failed_segments,
    detect_seg_ext,
    merged_segment_ext,
)
from ..util._subtitle_segments import merge_vtt_files
from ..util.formatting import format_size as _fmt_size
from ..util.formatting import normalize_path_key
from .constants import (
    _DECRYPT_ERROR_RATIO_LIMIT,
    MAX_TOKEN_REFRESH_ROUNDS,
    REQUEST_TIMEOUT,
    RETRY_COUNT,
    STREAMING_MUX_MIN_WAIT_SECONDS,
    TOKEN_REFRESH_BACKOFF_SECONDS,
    TOKEN_REFRESH_STALL_ROUNDS,
    logger,
)
from .context import _SegmentDownloadContext
from .curl_fallback import _run_curl_cffi_fallback
from .dash import DashDecryptMixin
from .events import DownloadEventMixin, _sniff_vtt_content
from .hls import HlsDecryptMixin
from .ism import IsmDecryptMixin
from .live_merge import _LiveMerger
from .progress import ProgressMixin
from .segment_io import SegmentIOMixin
from .sniff import (
    _reads_as_self_initializing_mp4,
    _skip_post_decrypt,
)
from .streaming_mux import StreamingMuxMixin, _estimate_livemux_wait_seconds
from .worker import WorkerMixin


class DecryptPipelineMixin(
    StreamingMuxMixin,
    SegmentIOMixin,
    HlsDecryptMixin,
    DashDecryptMixin,
    IsmDecryptMixin,
    WorkerMixin,
    ProgressMixin,
    DownloadEventMixin,
):
    def _download_stream_generic(
        self,
        dl_segs: list[dict],
        stream,
        protocol: str,
        default_ext: str,
        bar_manager: DownloadBarManager,
        live_decryption: bool = False,
        seg_url_refresh_fn=None,
    ) -> None:
        """
        Download every segment of one stream, then merge (and decrypt) them into the track file.
        """
        task_key = self._stream_task_key(stream)
        poisoned = self._kids_poisoned(self._stream_kids(stream))
        if poisoned:
            self._skip_stream_wrong_key(stream, poisoned, bar_manager, task_key)
            return

        ctx = self._new_segment_context(dl_segs, stream, protocol, default_ext, bar_manager, task_key, live_decryption)
        self._start_decrypt_workers(ctx)
        paths = self._fetch_segments(ctx, seg_url_refresh_fn)
        live_merge_ok = self._stop_decrypt_workers(ctx)
        self._report_download_outcome(ctx, paths)

        protocol_lower = ctx.protocol_lower
        total = ctx.total
        _live_out_path = ctx.live_out_path
        if ctx.needs_ism_live and ctx.ism_init is not None:
            _protected_init_path, _clean_init_path = ctx.ism_init
            if _clean_init_path.exists():
                paths.append(_clean_init_path)

        poisoned = self._kids_poisoned(self._stream_kids(stream))
        if poisoned:
            self._skip_stream_wrong_key(stream, poisoned, bar_manager, task_key)
            return

        if self._stop_check() or not paths or self._abort_event.is_set():
            self._record_track_done(task_key, None)
            return

        # Derive the merged-file extension from a *media* segment
        sample_url = next(
            (s["url"] for s in dl_segs if s.get("seg_type") != "init"),
            dl_segs[0]["url"] if dl_segs else "",
        )
        ext = merged_segment_ext(sample_url, default=default_ext)

        # Reuse the live-merge target's filename when one was already assigned above
        out_path = _live_out_path if _live_out_path is not None else self.output_dir / self._out_filename(stream, ext)

        # ----- ISM POST‑PROCESSING (batch path) -----
        _stream_is_encrypted = stream.drm is not None and stream.drm.is_encrypted()
        if protocol_lower == "ism" and self.key and _stream_is_encrypted and not ctx.needs_ism_live:
            success = self._ism_postproc(paths, out_path, stream, bar_manager, task_key, total)
            if not success:
                logger.error("ISM post‑processing failed")
                self._record_track_done(task_key, None)
            return

        self._merge_track(ctx, paths, out_path, live_merge_ok)

        # Signal completion unconditionally (success or failure)
        self._record_track_done(task_key, out_path if out_path.exists() and out_path.stat().st_size > 0 else None)

    def _stream_progress_label(self, stream, task_key: str) -> str:
        """Rich label shown on this track's progress bar (``Vid ...`` / ``Aud ...`` / ``Sub ...``)."""
        if stream.type == "video":
            _plain = self._video_labels_by_task_key.get(task_key) or self._video_label
            _progress_label = f"[bold cyan]Vid[/bold cyan] {_plain}" if _plain else ""
        elif stream.type == "audio":
            _plain = self._audio_labels_by_task_key.get(task_key) or self._audio_labels.get(
                (stream.language or "und").lower(), ""
            )
            _progress_label = f"[bold cyan]Aud[/bold cyan] {_plain}" if _plain else ""
        elif stream.type == "subtitle":
            _plain = self._sub_labels_by_task_key.get(task_key, "")
            _progress_label = f"[bold cyan]Sub[/bold cyan] {_plain}" if _plain else ""
        else:
            _progress_label = ""
        return _progress_label

    def _new_segment_context(
        self, dl_segs: list[dict], stream, protocol: str, default_ext: str, bar_manager: DownloadBarManager, task_key: str, live_decryption: bool
    ) -> _SegmentDownloadContext:
        """Per-call state shared by every phase: paths, segment metadata, callbacks, decryptors and which live-decrypt mode applies."""
        _progress_label = self._stream_progress_label(stream, task_key)

        total = len(dl_segs)
        stream_dir = self._make_stream_dir(stream, protocol)
        all_headers = self._build_headers()
        protocol_lower = protocol.lower()

        ctx = _SegmentDownloadContext(
            stream=stream,
            protocol=protocol,
            protocol_lower=protocol_lower,
            bar_manager=bar_manager,
            task_key=task_key,
            dl_segs=dl_segs,
            total=total,
            stream_dir=stream_dir,
            all_headers=all_headers,
            progress_label=_progress_label,
        )
        ctx.default_ext = default_ext
        ctx.live_decryption = live_decryption
        ctx.stop = lambda: self._stream_stop(ctx)
        ctx.progress_cb = lambda *a, **kw: self._progress(ctx, *a, **kw)
        ctx.event_cb = lambda ev: self._handle_download_event(ctx, ev)

        segment_meta_by_path = {}
        for seg in dl_segs:
            seg_ext = detect_seg_ext(seg.get("url", ""), default=default_ext)
            if seg_ext == "m4s":
                seg_ext = "mp4"
            seg_path = stream_dir / f"seg_{seg['number']:05d}.{seg_ext}"
            segment_meta_by_path[normalize_path_key(str(seg_path))] = seg
        ctx.segment_meta_by_path = segment_meta_by_path

        # No dedicated init segment on the wire: every segment is
        # self-initializing (its own ftyp+moov), so there's no separate init to wait for
        ctx.no_dedicated_init = protocol_lower in ("dash", "hls") and not any(s.get("seg_type") == "init" for s in dl_segs)

        # Full ordering key sequence for _LiveMerger. ISM's init is never a real
        # dl_seg (it's synthesized from the first fragment, see
        # _build_ism_init_from_fragment) so it isn't in dl_segs at all -- give
        # it key -1, sorting before every real segment number (>=0).
        ctx.expected_live_order = sorted(s["number"] for s in dl_segs) + ([-1] if protocol_lower == "ism" else [])
        ctx.expected_live_order.sort()

        ctx.total_duration = sum(s.get("duration", 0.0) for s in dl_segs if s.get("seg_type") != "init")
        ctx.media_segs_only = [s for s in dl_segs if s.get("seg_type") != "init"]
        _acc = 0.0
        for _s in ctx.media_segs_only:
            _acc += _s.get("duration", 0.0)
            ctx.seg_dur_cumulative.append(_acc)

        ctx.dash_decryptor = Decryptor() if protocol_lower in ("dash", "hls") and live_decryption and self.key else None
        ctx.ism_decryptor = Decryptor() if protocol_lower == "ism" and live_decryption and self.key else None

        self._compute_decrypt_needs(ctx, live_decryption)
        return ctx

    def _start_decrypt_workers(self, ctx: _SegmentDownloadContext) -> None:
        """When segments are decrypted / merged as they arrive: start the live merger, the streaming mux (video) and the decrypt workers."""
        stream = ctx.stream
        dl_segs = ctx.dl_segs
        protocol = ctx.protocol
        default_ext = ctx.default_ext
        live_decryption = ctx.live_decryption

        ctx.mux_join_timeout = STREAMING_MUX_MIN_WAIT_SECONDS + 5
        if (
            ctx.needs_hls_decrypt
            or ctx.needs_dash_live
            or ctx.needs_ism_live
            or ctx.needs_hls_live
            or ctx.needs_dash_clear_merge
            or ctx.needs_ism_clear_merge
            or ctx.needs_hls_clear_merge
        ):
            # Live decrypt (dash/ism/hls) all funnel through one shared
            # Decryptor() -> one _FluxDaemon, whose .decrypt() holds a lock
            # across the write+readline round-trip -- so only one job is
            # ever actually in flight regardless of pool size. Extra threads
            # here don't add decrypt throughput, just queue contention;
            # DECRYPT_WORKER_COUNT only matters for the non-live batch path.
            worker_count = 1
            live_kind = (
                "live DASH" if ctx.needs_dash_live
                else "live ISM" if ctx.needs_ism_live
                else "live HLS SAMPLE-AES" if ctx.needs_hls_live
                else "live-merge-only DASH (clear)" if ctx.needs_dash_clear_merge
                else "live-merge-only ISM (clear)" if ctx.needs_ism_clear_merge
                else "live-merge-only HLS (clear)" if ctx.needs_hls_clear_merge
                else "AES-128"
            )
            logger.debug(f"{protocol.upper()} decrypt worker pool started ({worker_count}x, {live_kind})")

            # Stream each segment straight into the final output as soon as
            # it's ready (decrypted, or just downloaded for clear content)
            # and it's its turn, instead of a separate binary-merge pass
            # over the whole track once every download is done -- see
            # _LiveMerger. Falls back to the normal binary_merge_segments()
            # path below if anything's missing by the time downloads
            # finish (failed segment, unexpected ordering key, etc).
            _live_sample_url = next(
                (s["url"] for s in dl_segs if s.get("seg_type") != "init"), dl_segs[0]["url"] if dl_segs else ""
            )
            _live_ext = merged_segment_ext(_live_sample_url, default=default_ext)
            ctx.live_out_path = self.output_dir / self._out_filename(stream, _live_ext)
            ctx.live_merger = _LiveMerger(ctx.live_out_path, ctx.expected_live_order, on_chunk=None)

            # Named-pipe relay: audio streams wire their live merger directly.
            # Subtitle streams are excluded — they go to disk only.
            if stream.type == "audio":
                relay = getattr(self, "_relay_mux_writer", None)
                if relay is not None:
                    fn = relay.get(id(stream)) if isinstance(relay, dict) else relay
                    if fn is not None:
                        ctx.live_merger.attach_feeder(fn)

            if stream.type == "video":
                _video_duration_cap = None
                if self.max_segments or self.max_time:
                    _dl_video_dur = sum(s.get("duration", 0.0) for s in dl_segs if s.get("seg_type") != "init")
                    if _dl_video_dur > 0:
                        _video_duration_cap = _dl_video_dur

                # Runs the wait-for-other-tracks + ffmpeg launch in the background so it
                # never blocks this video stream's own segment download below (started
                # right after this call, unconditionally) -- see _launch_streaming_mux_async().
                ctx.mux_setup_thread = self._launch_streaming_mux_async(
                    _live_ext, stream, _video_duration_cap, ctx.live_merger, ctx.feeder_box
                )

                # The background thread waits on each non-video track sequentially (see
                # _try_start_streaming_mux_inner), each with its own dynamic timeout --
                # so the worst case for this join is the SUM of those timeouts, not one
                # fixed value.
                _other_for_wait = [
                    s
                    for s in getattr(self, "streams", []) or []
                    if getattr(s, "selected", False) and not getattr(s, "is_external", False) and s.type in ("audio", "subtitle")
                ]
                ctx.mux_join_timeout = sum(_estimate_livemux_wait_seconds(s) for s in _other_for_wait) + STREAMING_MUX_MIN_WAIT_SECONDS

            for _ in range(worker_count):
                t = threading.Thread(target=self._decrypt_worker, args=(ctx, live_decryption), daemon=True)
                t.start()
                ctx.decrypt_threads.append(t)

    def _fetch_segments(self, ctx: _SegmentDownloadContext, seg_url_refresh_fn) -> list[Path]:
        """Get every segment on disk: inline ones from the manifest, the rest from the network, then the curl_cffi and token-refresh retries."""
        stream = ctx.stream
        dl_segs = ctx.dl_segs
        protocol = ctx.protocol
        default_ext = ctx.default_ext
        stream_dir = ctx.stream_dir
        all_headers = ctx.all_headers
        task_key = ctx.task_key
        _stop, _progress_cb, _event_cb = ctx.stop, ctx.progress_cb, ctx.event_cb

        # Segments whose bytes travel inside the manifest itself (e.g. a base64 init segment) have no URL to request
        inline_paths: list[Path] = []
        net_segs = dl_segs
        if any(seg.get("inline_data") for seg in dl_segs):
            net_segs = []
            for seg in dl_segs:
                data = seg.get("inline_data")
                if not data:
                    net_segs.append(seg)
                    continue

                seg_ext = detect_seg_ext(seg.get("url", ""), default=default_ext)
                if seg_ext == "m4s":
                    seg_ext = "mp4"

                inline_path = stream_dir / f"seg_{seg['number']:05d}.{seg_ext}"
                inline_path.write_bytes(data)
                inline_paths.append(inline_path)
                logger.debug(f"Inline segment written from manifest -> {inline_path.name} ({len(data)} B)")

            logger.info(f"{protocol.upper()}: {len(inline_paths)} inline segment(s) from manifest, {len(net_segs)} to download")

        paths = list(inline_paths)
        if net_segs:
            logger.info(
                f"{protocol.upper()} download started | id={stream.id!r} | type={stream.type} | {task_key} | segs={len(net_segs)}"
            )
            paths += self._run_dl(
                net_segs,
                stream_dir,
                all_headers,
                _progress_cb,
                stream=stream,
                event_cb=_event_cb,
                default_ext=default_ext,
                stop_check=_stop,
            )

        self._curl_fallback_pass(ctx, paths, net_segs)
        self._token_refresh_pass(ctx, paths, seg_url_refresh_fn)
        return paths

    def _curl_fallback_pass(self, ctx: _SegmentDownloadContext, paths: list[Path], net_segs: list[dict]) -> None:
        """Retry (via curl_cffi) the segments the primary backend gave up on; recovered paths are appended to ``paths``."""
        dl_segs = ctx.dl_segs
        stream_dir = ctx.stream_dir
        default_ext = ctx.default_ext
        all_headers = ctx.all_headers
        task_key = ctx.task_key
        total = ctx.total
        _progress_label = ctx.progress_label
        _stop, _progress_cb, _event_cb = ctx.stop, ctx.progress_cb, ctx.event_cb

        # curl_cffi fallback: once the primary backend (native Velora binary, or curl_cffi itself if that's already the primary) has exhausted its own max_retry attempts for a segment
        if net_segs and not _stop():
            still_failed = collect_failed_segments(dl_segs, paths, stream_dir, default_ext)
            if still_failed:
                seg_by_number_fb = {s["number"]: s for s in dl_segs}
                fallback_tasks = []
                for n, _ in still_failed:
                    seg = seg_by_number_fb.get(n)
                    if seg is None:
                        continue

                    seg_ext = detect_seg_ext(seg.get("url", ""), default=default_ext)
                    if seg_ext == "m4s":
                        seg_ext = "mp4"

                    fallback_tasks.append(
                        {
                            "url": seg["url"],
                            "path": str(stream_dir / f"seg_{n:05d}.{seg_ext}"),
                            "headers": seg.get("headers") or {},
                            "task_key": task_key,
                            "label": _progress_label,
                            "display_label": _progress_label,
                        }
                    )

                fallback_plan = {
                    "headers": all_headers,
                    "retry_count": RETRY_COUNT,
                    "timeout_seconds": float(REQUEST_TIMEOUT),
                    "max_speed_bytes_per_sec": int(config_manager.config.get_float("DOWNLOAD", "max_speed_mbps", default=0.0) * 1_000_000),
                }

                logger.warning(f"{len(fallback_tasks)} segment(s) exhausted the primary backend's retries -- falling back to curl_cffi for up to {RETRY_COUNT} more attempt(s) each")
                done_before_fallback = len(paths)
                bytes_before_fallback = ctx.last_total_bytes
                recovered_paths = _run_curl_cffi_fallback(
                    fallback_tasks,
                    fallback_plan,
                    done_before_fallback,
                    bytes_before_fallback,
                    total,
                    _progress_cb,
                    _event_cb,
                    _stop,
                )
                paths.extend(recovered_paths)

                if recovered_paths:
                    logger.info(f"curl_cffi fallback recovered {len(recovered_paths)}/{len(fallback_tasks)} segment(s)")

    def _token_refresh_pass(self, ctx: _SegmentDownloadContext, paths: list[Path], seg_url_refresh_fn) -> None:
        """Re-download still-missing segments with freshly signed URLs (expired CDN token, transient 5xx); results are appended to ``paths``."""
        stream = ctx.stream
        dl_segs = ctx.dl_segs
        stream_dir = ctx.stream_dir
        default_ext = ctx.default_ext
        all_headers = ctx.all_headers
        _stop, _progress_cb, _event_cb = ctx.stop, ctx.progress_cb, ctx.event_cb

        # Token-refresh retry: when segments fail (e.g. the CDN manifest token expired mid-download -> HTTP 403, or a transient CDN-side 503 that clears up after a short wait).
        if seg_url_refresh_fn and not _stop():
            seg_by_number = {s["number"]: s for s in dl_segs}
            failed = collect_failed_segments(dl_segs, paths, stream_dir, default_ext)
            rounds = 0
            stall_rounds = 0

            while failed and rounds < MAX_TOKEN_REFRESH_ROUNDS and not _stop():
                rounds += 1

                if TOKEN_REFRESH_BACKOFF_SECONDS > 0:
                    backoff = min(TOKEN_REFRESH_BACKOFF_SECONDS * rounds, 20.0)
                    logger.info(f"Token refresh round {rounds}: waiting {backoff:.1f}s before retrying (transient CDN errors often clear up on their own)")
                    self._interruptible_sleep(backoff)
                    if self._stop_check():
                        break

                failed_numbers = [n for n, _ in failed]
                fresh_map = seg_url_refresh_fn(failed_numbers)
                retry_segs = build_retry_segments(failed_numbers, seg_by_number, fresh_map)
                if not retry_segs:
                    break

                logger.warning(f"Token refresh round {rounds}: retrying {len(retry_segs)} segment(s) with a fresh token")
                retry_paths = self._run_dl(
                    retry_segs,
                    stream_dir,
                    all_headers,
                    _progress_cb,
                    stream=stream,
                    event_cb=_event_cb,
                    default_ext=default_ext,
                    stop_check=_stop,
                )
                paths.extend(retry_paths)
                new_failed = collect_failed_segments(dl_segs, paths, stream_dir, default_ext)

                if len(new_failed) >= len(failed):  # no progress this round -> token still dead / host moved
                    stall_rounds += 1
                    failed = new_failed
                    if stall_rounds >= TOKEN_REFRESH_STALL_ROUNDS:
                        logger.warning(f"Token refresh: no progress after {stall_rounds} consecutive round(s), giving up on {len(failed)} segment(s)")
                        break
                    continue

                stall_rounds = 0
                failed = new_failed

    @staticmethod
    def _join_interruptible(thread, timeout: float, poll: float = 0.25) -> bool:
        """Join *thread* in short slices so Ctrl+C stays deliverable; True when it finished in time."""
        import time as _time

        deadline = _time.monotonic() + max(0.0, timeout)
        while True:
            if not thread.is_alive():
                return True
            remaining = deadline - _time.monotonic()
            if remaining <= 0:
                return not thread.is_alive()
            thread.join(timeout=min(poll, remaining))

    def _stop_decrypt_workers(self, ctx: _SegmentDownloadContext) -> bool:
        """Drain and join the workers, close the decryptors and the live merger; returns True when the live merge produced the whole track."""
        stream = ctx.stream
        protocol = ctx.protocol

        if ctx.decrypt_threads:
            for _ in ctx.decrypt_threads:
                ctx.decrypt_queue.put(None)
            for t in ctx.decrypt_threads:
                self._join_interruptible(t, timeout=30.0)

        if ctx.mux_setup_thread is not None:
            self._join_interruptible(ctx.mux_setup_thread, timeout=min(ctx.mux_join_timeout, 60.0))
        streaming_feeder = ctx.feeder_box[0]

        if ctx.dash_decryptor is not None:
            ctx.dash_decryptor.close_flux_daemon()
        if ctx.ism_decryptor is not None:
            ctx.ism_decryptor.close_flux_daemon()

        _live_merge_ok = False
        if ctx.live_merger is not None:
            _live_merge_ok = ctx.live_merger.complete
            ctx.live_merger.close()
            if _live_merge_ok:
                logger.debug(f"{protocol.upper()} live merge complete -> binary-merge pass skipped for this track")
            else:
                logger.debug(f"{protocol.upper()} live merge incomplete (missing/failed segment) -> falling back to the normal merge pass")

        if stream.type == "video" and streaming_feeder is not None:
            self._finish_streaming_mux(streaming_feeder, live_merge_ok=_live_merge_ok)

        return _live_merge_ok

    def _report_download_outcome(self, ctx: _SegmentDownloadContext, paths: list[Path]) -> None:
        """Log the segments that failed and raise when the track cannot be trusted (aborted decrypt, too many decrypt errors)."""
        stream = ctx.stream
        dl_segs = ctx.dl_segs
        stream_dir = ctx.stream_dir
        default_ext = ctx.default_ext
        task_key = ctx.task_key
        total = ctx.total

        if paths is not None:
            _stream_label_rich = (
                (self._video_labels_by_task_key.get(task_key) or self._video_label)
                if stream.type == "video"
                else self._audio_labels_by_task_key.get(task_key)
                or self._audio_labels.get((stream.language or "und").lower(), stream.language or "und")
                if stream.type == "audio"
                else stream.language or "und"
            )

            _plain_label = Text.from_markup(_stream_label_rich).plain.strip() or task_key
            failed = collect_failed_segments(dl_segs, paths, stream_dir, default_ext)
            # A wrong-key abort stops downloads on purpose: the "missing"
            # segments below are intentional, not CDN failures -- reporting
            # them as Dio Cancaro/missing would be pure noise on top of the
            # Terminating/decrypt-failure lines.
            wrong_key_abort = bool(ctx.decrypt_aborted_reason and "wrong key" in ctx.decrypt_aborted_reason)
            if failed and not wrong_key_abort:
                failed_numbers = {n for n, _ in failed}
                aes_failed = sum(
                    1
                    for seg in dl_segs
                    if seg["number"] in failed_numbers
                    and str((seg.get("enc") or {}).get("method") or "NONE").upper() == "AES-128"
                )
                aes_note = (
                    f" ({aes_failed} had an AES-128 key pending — never fetched, segment missing before decrypt)"
                    if aes_failed
                    else ""
                )
                if ctx.seg_errors:
                    top = "; ".join(
                        f"{m} (x{n})" for m, n in Counter(e.strip() for e in ctx.seg_errors if e.strip()).most_common(3)
                    )
                    logger.warning(f"{_plain_label}: {len(failed)}/{total} segment(s) failed to download — most common error(s): {top}{aes_note}")
                else:
                    logger.warning(f"{_plain_label}: {len(failed)}/{total} segment(s) failed to download{aes_note}")

                if ctx.media_segs_only and min(s["number"] for s in ctx.media_segs_only) in failed_numbers:
                    logger.warning(
                        f"{_plain_label}: the FIRST media segment (right after init) is missing — the merged file is "
                        "likely unparsable (fMP4 trun/tfhd continuity breaks), not just a small A/V gap. "
                        "Expect the duration probe to fail and this track to be dropped from the mux."
                    )

                with self._failed_segments_lock:
                    self._failed_segments.append((_plain_label, failed))

        if ctx.decrypt_aborted_reason is not None:
            # A genuinely permanent condition (e.g. no content key for this
            # track's KID at all) -- no segment could ever decrypt, so there's
            # nothing to gain by continuing.
            raise RuntimeError(ctx.decrypt_aborted_reason)
        elif ctx.decrypt_errors:
            # Per-segment decrypt failures (already retried once with a fresh
            # download inside _decrypt_dash_segment/_decrypt_hls_segment/etc.,
            # and the affected segments dropped) -- fine as a warning and a
            # small A/V gap, but past _DECRYPT_ERROR_RATIO_LIMIT the track is
            # mostly garbage (e.g. a CDN edge serving broken bytes for nearly
            # every segment) and merging it just produces an unplayable file
            # silently reported as a success.
            error_ratio = (len(ctx.decrypt_errors) / total) if total else 1.0
            if error_ratio > _DECRYPT_ERROR_RATIO_LIMIT:
                raise RuntimeError(f"too many segment decrypt failures ({len(ctx.decrypt_errors)}/{total}, {error_ratio:.0%}) -- first: {ctx.decrypt_errors[0]}")

            logger.warning(f"{len(ctx.decrypt_errors)} segment decrypt error(s) on this track (first: {ctx.decrypt_errors[0]}) -- continuing with the segments that did decrypt")

    def _merge_track(self, ctx: _SegmentDownloadContext, paths: list[Path], out_path: Path, live_merge_ok: bool) -> None:
        """Merge the downloaded segments into ``out_path``, decrypt the merged file when needed and finish the progress bar."""
        paths, is_plain_subtitle, stream_is_encrypted, already_decrypted_per_segment = self._merge_segments(ctx, paths, out_path, live_merge_ok)
        decrypted_ok, decrypt_already_reported = self._normalize_and_decrypt(
            ctx, out_path, is_plain_subtitle, stream_is_encrypted, already_decrypted_per_segment
        )
        self._report_merge_result(ctx, paths, out_path, decrypted_ok, decrypt_already_reported, live_merge_ok)

    def _merge_segments(self, ctx: _SegmentDownloadContext, paths: list[Path], out_path: Path, live_merge_ok: bool) -> tuple:
        """Concatenate the segments (WebVTT cue-merge, live-merge result or binary merge); returns ``(paths, is_plain_subtitle, stream_is_encrypted, already_decrypted_per_segment)``."""
        stream = ctx.stream
        bar_manager = ctx.bar_manager
        task_key = ctx.task_key
        total = ctx.total
        protocol_lower = ctx.protocol_lower
        _live_merge_ok = live_merge_ok

        # Standard merge for HLS/DASH (and ISM when live-decrypted per-segment)
        is_plain_subtitle = (
            stream is not None
            and getattr(stream, "type", "") == "subtitle"
            and not getattr(stream, "is_wvtt_mp4", False)
        )

        merge_total_size = sum(p.stat().st_size for p in paths if p.exists())
        _merge_t0 = time.monotonic()
        if _live_merge_ok:
            # Live merge already wrote out_path in order as segments arrived
            # -- there's no separate merge pass left to run below (see the
            # `elif _live_merge_ok` branch).
            logger.debug(f"Live merge already complete -> {out_path.name} ({len(paths)} segs, {_fmt_size(merge_total_size)})")
        else:
            logger.info(f"Merge starting -> {out_path.name} ({len(paths)} segs, {_fmt_size(merge_total_size)})")
            bar_manager.handle_progress_line(
                {
                    "task_key": task_key,
                    "pct": 100,
                    "segments": f"{total}/{total}",
                    "size": f"{_fmt_size(merge_total_size)}/{_fmt_size(merge_total_size)}",
                    "speed": "Merge",
                }
            )

        is_webvtt_sub = self._is_webvtt_subtitle(paths, out_path, is_plain_subtitle)
        _drm = getattr(stream, "drm", None)
        stream_is_encrypted = (_drm is not None) and (_drm.method is not None or (protocol_lower != "hls" and _drm.is_encrypted()))
        paths, already_decrypted_per_segment = self._decrypt_self_initializing_segments(ctx, paths, out_path, is_plain_subtitle, stream_is_encrypted)
        self._write_merged_file(ctx, paths, out_path, is_webvtt_sub, _live_merge_ok, _merge_t0)
        self._remerge_spliced_ts(ctx, paths, out_path, is_plain_subtitle)

        if already_decrypted_per_segment:
            for p in paths:
                p.unlink(missing_ok=True)

        return paths, is_plain_subtitle, stream_is_encrypted, already_decrypted_per_segment

    def _is_webvtt_subtitle(self, paths: list[Path], out_path: Path, is_plain_subtitle: bool) -> bool:
        """Whether a plain-text subtitle track is WebVTT (by extension or by sniffing the segments), so cues are merged instead of bytes."""
        _is_webvtt_sub = False
        _detect_reason = "no paths"

        if is_plain_subtitle and paths:
            _ext_says_vtt = out_path.suffix.lower() == ".vtt"
            _content_says_vtt = False

            for p in paths:
                try:
                    raw = p.read_bytes()[:512]
                    if _sniff_vtt_content(raw):
                        _content_says_vtt = True
                        break
                except Exception as exc:
                    logger.warning(f"[merge_detect] could not sniff {getattr(p, 'name', p)}: {exc}")
                    continue

            _is_webvtt_sub = _ext_says_vtt or _content_says_vtt
            _detect_reason = f"ext={_ext_says_vtt}, content={_content_says_vtt}"

        logger.info(f"[merge_detect] {out_path.name}: is_webvtt_sub={_is_webvtt_sub} ({_detect_reason}), {len(paths)} segment(s)")
        return _is_webvtt_sub

    def _decrypt_self_initializing_segments(
        self, ctx: _SegmentDownloadContext, paths: list[Path], out_path: Path, is_plain_subtitle: bool, stream_is_encrypted: bool
    ) -> tuple[list[Path], bool]:
        """HLS BYTERANGE packagings with one ftyp+moov per segment: decrypt each segment before the merge; returns ``(paths, already_decrypted_per_segment)``."""
        stream = ctx.stream
        live_decryption = ctx.live_decryption
        already_decrypted_per_segment = False

        # Some HLS BYTERANGE packagings make every media segment a
        # self-initializing MP4 document (its own ftyp+moov, not a bare
        # moof+mdat fragment sharing the EXT-X-MAP init).
        if (
            not is_plain_subtitle
            and not live_decryption
            and self.key
            and stream_is_encrypted
            and not _skip_post_decrypt()
        ):
            existing = [p for p in paths if p.exists() and p.stat().st_size > 0]
            ftyp_count = sum(1 for p in existing if _reads_as_self_initializing_mp4(p))
            if len(existing) > 1 and ftyp_count > 1:
                logger.info(f"{out_path.name}: {ftyp_count}/{len(existing)} segment(s) are self-initializing MP4 documents — decrypting each individually before merge instead of once after")
                decrypted_paths: list[Path] = []
                per_segment_ok = True
                for p in existing:
                    dec_p = p.with_suffix(p.suffix + ".dec")
                    try:
                        if Decryptor().decrypt(str(p), self.key, str(dec_p), stream_type=stream.type):
                            decrypted_paths.append(dec_p)
                        else:
                            per_segment_ok = False
                            logger.warning(f"{out_path.name}: per-segment decrypt failed for {p.name}")
                            break
                    except Exception as exc:
                        per_segment_ok = False
                        logger.warning(f"{out_path.name}: per-segment decrypt error for {p.name}: {exc}")
                        break

                if per_segment_ok:
                    paths = decrypted_paths
                    already_decrypted_per_segment = True
                else:
                    for dec_p in decrypted_paths:
                        dec_p.unlink(missing_ok=True)

        return paths, already_decrypted_per_segment

    def _write_merged_file(
        self, ctx: _SegmentDownloadContext, paths: list[Path], out_path: Path, is_webvtt_sub: bool, live_merge_ok: bool, merge_t0: float
    ) -> None:
        """Write ``out_path``: WebVTT cue-merge, nothing when the live merge already wrote it, otherwise a binary merge of ``paths``."""
        _is_webvtt_sub = is_webvtt_sub
        _live_merge_ok = live_merge_ok
        _merge_t0 = merge_t0

        if _is_webvtt_sub:
            merged = merge_vtt_files(paths, merge_logger=logger)
            n_headers = merged.count("WEBVTT")
            if n_headers != 1:
                logger.warning(f"[merge_vtt] {out_path.name}: expected 1 WEBVTT header after merge, found {n_headers}")
            out_path.write_text(merged, encoding="utf-8")
            logger.debug(f"WebVTT cue-merge completed -> {out_path.name}")
        elif _live_merge_ok:
            logger.debug(f"Live merge already wrote {out_path.name} in order -- binary-merge pass skipped")
            _png_wrapped = getattr(ctx.live_merger, "_png_wrapped", 0)
            _gzip_count = getattr(ctx.live_merger, "_gzip_count", 0)
            if _png_wrapped:
                logger.info(f"[live_merge] resolved {_png_wrapped} PNG-wrapped segment(s)")
            if _gzip_count:
                logger.info(f"[live_merge] decompressed {_gzip_count} gzip-compressed segment(s)")
        else:
            binary_merge_segments(paths, out_path, merge_logger=logger)
            logger.debug(f"Binary merge completed -> {out_path.name}")
        logger.info(f"Merge finished -> {out_path.name} in {time.monotonic() - _merge_t0:.1f}s")

    def _remerge_spliced_ts(self, ctx: _SegmentDownloadContext, paths: list[Path], out_path: Path, is_plain_subtitle: bool) -> None:
        """A raw-concatenated MPEG-TS that lost time at a PCR/PTS splice is re-merged through ffmpeg's concat demuxer."""
        # A raw-byte-concatenated MPEG-TS whose source has a genuine PCR/PTS discontinuity at a segment boundary
        if not is_plain_subtitle and ctx.total_duration > 0 and out_path.suffix.lower() == ".ts" and out_path.exists():
            from VibraVid.core.muxing.helper.audio.probe import get_video_duration

            merged_duration = get_video_duration(str(out_path))
            if merged_duration and merged_duration < ctx.total_duration * 0.95:
                logger.warning(f"{out_path.name}: merged duration {merged_duration:.1f}s is well short of the manifest's {ctx.total_duration:.1f}s -- likely a mid-stream splice the raw concat can't express. Re-merging via ffmpeg's concat demuxer.")
                if concat_demux_merge_segments(paths, out_path, merge_logger=logger):
                    _fixed_duration = get_video_duration(str(out_path))
                    logger.info(f"{out_path.name}: concat-demuxer re-merge -> {_fixed_duration:.1f}s" if _fixed_duration else f"{out_path.name}: concat-demuxer re-merge done")

    def _normalize_and_decrypt(
        self,
        ctx: _SegmentDownloadContext,
        out_path: Path,
        is_plain_subtitle: bool,
        stream_is_encrypted: bool,
        already_decrypted_per_segment: bool,
    ) -> tuple[bool, bool]:
        """Reset fragment timestamps and decrypt the merged file; returns ``(decrypted_ok, decrypt_already_reported)``."""
        self._normalize_timestamps(ctx, out_path, is_plain_subtitle, stream_is_encrypted, already_decrypted_per_segment)
        return self._decrypt_merged_file(ctx, out_path, is_plain_subtitle, stream_is_encrypted, already_decrypted_per_segment)

    def _normalize_out_path(self, out_path: Path, is_plain_subtitle: bool) -> None:
        """Reset absolute fragment timestamps of an fMP4 track (must run AFTER decryption)."""
        if is_plain_subtitle or out_path.suffix.lower() not in (".mp4", ".m4s", ".m4a"):
            return

        from VibraVid.core.muxing.helper.video import normalize_timestamps

        norm_path = normalize_timestamps(out_path, logger)
        if norm_path is None:
            return

        try:
            out_path.unlink(missing_ok=True)
            norm_path.rename(out_path)
        except OSError as exc:
            logger.error(f"[normalize] rename-back failed, keeping un-normalized file: {exc}")
            norm_path.unlink(missing_ok=True)

    def _normalize_timestamps(
        self, ctx: _SegmentDownloadContext, out_path: Path, is_plain_subtitle: bool, stream_is_encrypted: bool, already_decrypted_per_segment: bool
    ) -> None:
        """Rebase the fragment timestamps in place when they are live-decrypted, otherwise normalize the file or defer the fix to the join pass."""
        protocol_lower = ctx.protocol_lower
        live_decryption = ctx.live_decryption

        # Live-decrypted fragments keep their original absolute tfdt
        _is_live_fragmented = (
            protocol_lower in ("dash", "ism", "hls")
            and live_decryption
            and stream_is_encrypted
            and not is_plain_subtitle
            and out_path.suffix.lower() in (".mp4", ".m4s", ".m4a")
        )
        if _is_live_fragmented:
            # Cheapest possible fix first: subtract the first fragment's tfdt from
            # every fragment's tfdt in place (a handful of small seeks+writes, no
            # resize, no ffmpeg) -- see _tfdt_rebase.py.
            from ..util._tfdt_rebase import rebase_fragment_timestamps

            if not rebase_fragment_timestamps(out_path, logger=logger):
                if config_manager.config.get("PROCESS", "engine", default="ffmpeg").lower() == "ffmpeg":
                    self._needs_join_ts_fix = True
                else:
                    self._normalize_out_path(out_path, is_plain_subtitle)
        elif already_decrypted_per_segment or not ((not live_decryption) and self.key and stream_is_encrypted):
            self._normalize_out_path(out_path, is_plain_subtitle)

    def _decrypt_merged_file(
        self,
        ctx: _SegmentDownloadContext,
        out_path: Path,
        is_plain_subtitle: bool,
        stream_is_encrypted: bool,
        already_decrypted_per_segment: bool,
    ) -> tuple[bool, bool]:
        """Post-merge decrypt of the whole track; returns ``(decrypted_ok, decrypt_already_reported)``."""
        stream = ctx.stream
        bar_manager = ctx.bar_manager
        task_key = ctx.task_key
        live_decryption = ctx.live_decryption

        decrypted_ok = already_decrypted_per_segment
        decrypt_already_reported = False
        if already_decrypted_per_segment:
            # Each segment was already decrypted individually before the merge above — the merged file is plaintext already.
            logger.info(f"Decrypt already done per-segment -> {out_path.name}")
        elif _skip_post_decrypt() and stream_is_encrypted:
            logger.info(f"skip_post_decrypt: leaving {out_path.name} encrypted (raw merged track kept for testing)")
        elif (
            (not live_decryption)
            and self.key
            and stream_is_encrypted
            and out_path.exists()
            and out_path.stat().st_size > 0
            and not is_plain_subtitle
        ):
            post_merge_path = out_path.with_suffix(out_path.suffix + ".dec")

            # Continue this track's own progress bar for the decrypt phase: keep the track
            # label, just swap the status (the "@ Merge" text) for the decrypt method/backend
            # ("@ Merge" -> "@ CTR"; segment count and size stay as the merge left them).
            _decrypt_cb = bar_manager.decrypt_progress_cb(task_key)

            _decrypt_t0 = time.monotonic()
            try:
                decryptor = Decryptor()
                if decryptor.decrypt(
                    str(out_path), self.key, str(post_merge_path), stream_type=stream.type, progress_cb=_decrypt_cb
                ):
                    decrypted_ok = True
                    logger.info(f"Decrypt finished -> {out_path.name} in {time.monotonic() - _decrypt_t0:.1f}s")
                    self._swap_in_decrypted_file(out_path, post_merge_path, is_plain_subtitle)
                else:
                    decrypt_already_reported = True
                    self._record_decrypt_failure(ctx, out_path, post_merge_path, _decrypt_t0)

            except Exception as exc:
                logger.error(f"Decrypt error -> {out_path.name} after {time.monotonic() - _decrypt_t0:.1f}s: {exc}")

        return decrypted_ok, decrypt_already_reported

    def _swap_in_decrypted_file(self, out_path: Path, post_merge_path: Path, is_plain_subtitle: bool) -> None:
        """Replace the encrypted merged file with its decrypted copy, then fix timestamps (deferred to the join pass when it can apply them)."""
        try:
            out_path.unlink(missing_ok=True)
            post_merge_path.rename(out_path)
            
            # Same fragmented-mp4 tfdt issue as the live-decrypt path above: defer the
            # -avoid_negative_ts/-fflags +genpts fix into the Join Media ffmpeg pass
            # instead of a second full-file remux here, when that pass can apply it.
            if (
                not is_plain_subtitle
                and out_path.suffix.lower() in (".mp4", ".m4s", ".m4a")
                and config_manager.config.get("PROCESS", "engine", default="ffmpeg").lower() == "ffmpeg"
            ):
                self._needs_join_ts_fix = True
            else:
                self._normalize_out_path(out_path, is_plain_subtitle)
        except Exception as exc:
            logger.error(f"rename failed: {exc}")
            if post_merge_path.exists():
                try:
                    post_merge_path.unlink()
                except Exception:
                    pass

    def _record_decrypt_failure(self, ctx: _SegmentDownloadContext, out_path: Path, post_merge_path: Path, decrypt_t0: float) -> None:
        """Log a failed post-merge decrypt, mark the bar and remember it for the end-of-download report."""
        stream = ctx.stream
        bar_manager = ctx.bar_manager
        task_key = ctx.task_key
        _decrypt_t0 = decrypt_t0

        kid_hint = ", ".join(stream.drm.get_all_kids()) if stream.drm else ""
        track_label_str = f"{stream.type} {stream.resolution or stream.language or ''}".strip()
        logger.warning(f"Decrypt failed -> {out_path.name} after {time.monotonic() - _decrypt_t0:.1f}s (kid={kid_hint or 'unknown'})")
        bar_manager.handle_progress_line({"task_key": task_key, "speed": "Failed"})
        with self._decrypt_failures_lock:
            self.decrypt_failures.append(
                {
                    "label": track_label_str,
                    "track": out_path.name,
                    "message": f"required KID(s): {kid_hint or 'unknown'}",
                }
            )
        if post_merge_path.exists():
            try:
                post_merge_path.unlink()
            except Exception:
                pass

    def _report_merge_result(
        self, ctx: _SegmentDownloadContext, paths: list[Path], out_path: Path, decrypted_ok: bool, decrypt_already_reported: bool, live_merge_ok: bool
    ) -> None:
        """Final state of the track's progress bar (done / merged / decrypt failed) and the log line for the merged file."""
        protocol = ctx.protocol
        bar_manager = ctx.bar_manager
        task_key = ctx.task_key
        total = ctx.total
        _progress_cb = ctx.progress_cb
        _live_merge_ok = live_merge_ok

        if out_path.exists() and out_path.stat().st_size > 0:
            logger.debug(f"{protocol.upper()} merged {len(paths):>4} segs -> {out_path.name} ({out_path.stat().st_size // 1024} KB)")

            if decrypted_ok:
                # Finalize the bar at 100%, keeping segment/size/status as-is.
                bar_manager.handle_progress_line({"task_key": task_key, "pct": 100})
            elif decrypt_already_reported:
                _progress_cb(total, total, out_path.stat().st_size, 0.0, speed_label="Failed")
            elif _live_merge_ok:
                # Live merge already wrote out_path segment-by-segment as they
                # arrived (see the identical guard around line 925) -- no
                # separate merge pass ran here either, so "Merge" would be
                # mislabeling work that never happened. Just finalize pct.
                bar_manager.handle_progress_line({"task_key": task_key, "pct": 100})
            else:
                _progress_cb(total, total, out_path.stat().st_size, 0.0, speed_label="Merge")
        else:
            logger.error(f"{protocol.upper()} binary merge produced empty file: {out_path}")
