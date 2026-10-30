# 09.04.26

import logging
import subprocess
import time
from pathlib import Path

from VibraVid.core.decryptor import Decryptor
from VibraVid.core.manifest.mpd import DashParser
from VibraVid.core.muxing.helper.video import binary_merge_segments
from VibraVid.core.ui.bar_manager import console
from VibraVid.core.velora.util.formatting import (
    fmt_dur as _fmt_dur,
)
from VibraVid.core.velora.util.formatting import (
    format_size as _fmt_size,
)
from VibraVid.core.velora.util.formatting import (
    format_speed as _fmt_speed,
)
from VibraVid.utils import config_manager
from VibraVid.utils.http_client import create_client

from ..decryptor._segment_crypto import decrypt_aes128_file
from ._decrypt_pipeline.live_merge import _LiveMerger
from ._live_ad_break import LiveAdBreak
from ._live_align import LiveStartAligner
from ._live_mux import LiveMux
from .util._hls import hls_base_url, parse_hls_live_playlist
from .util._stream_helpers import detect_seg_ext, is_valid_frag_init, merged_segment_ext, repair_init_segment
from .util._tfdt_rebase import FragmentContinuity, first_fragment_tfdt, rebase_fragment_timestamps, track_timescale

logger = logging.getLogger("manual")
REQUEST_TIMEOUT: int = config_manager.config.get_int("REQUESTS", "timeout")
MAX_EMPTY_POLLS: int = config_manager.config.get_int("DOWNLOAD", "live_max_empty_polls")

# A big first batch (recording from the start of an event) is downloaded, decrypted and handed to the merge / live mux in groups of this many segments,
# so the file starts to be written after the first group instead of after the whole backlog.
LIVE_BACKLOG_CHUNK: int = 120

# An ad break shorter than this is not worth an INFO line.
LIVE_AD_BREAK_LOG_SECONDS: float = 30.0


def _sleep_interruptible(seconds: float, stop_check, poll: float = 0.25) -> None:
    """Sleep *seconds* but wake early when stop_check() returns True."""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if stop_check():
            break
        time.sleep(min(poll, max(0.0, deadline - time.monotonic())))


def _repair_init_file(path: Path, headers: dict | None) -> None:
    """Overwrite *path* in place if its content isn't a valid ftyp+moov init segment."""
    try:
        data = path.read_bytes()
    except OSError:
        return
    
    redirect_url = repair_init_segment(data)
    if not redirect_url:
        return
    
    try:
        logger.debug(f"Live DASH: init segment wasn't a real MP4, retrying via extracted URL: {redirect_url}")
        with create_client(headers=headers or {}, timeout=REQUEST_TIMEOUT, follow_redirects=True) as c:
            r = c.get(redirect_url)
            r.raise_for_status()
            data = r.content
        if not is_valid_frag_init(data):
            logger.warning(f"Live DASH: init segment still not a valid ftyp+moov after following extracted redirect URL: {redirect_url}")
            return
        
        path.write_bytes(data)
        logger.info(f"Live DASH: init segment repaired via extracted redirect URL -> {path.name}")
    except Exception as exc:
        logger.warning(f"Live DASH: init segment repair failed, keeping original (decrypt may fail): {exc}")


def _emit_live_progress(
    bar_manager, task_key: str, seg_done: int, total_bytes: int, speed_bps: float, elapsed_dur: float = 0.0
) -> None:
    duration_display = f"{_fmt_dur(elapsed_dur)}/~" if elapsed_dur > 0 else ""
    bar_manager.handle_progress_line(
        {
            "task_key": task_key,
            "pct": min(98, seg_done),
            "segments": f"{seg_done}/~",
            "size": f"{_fmt_size(total_bytes)}/~",
            "speed": _fmt_speed(speed_bps),
            "duration": duration_display,
        }
    )


def _make_batch_progress_cb(bar_manager, task_key: str, segs_before: int, bytes_before: int, elapsed_before: float, avg_dur: float):
    """Progress callback for one live batch: offsets the batch counters by what the previous batches already downloaded."""
    def _cb(done, total_, cur_bytes, speed_bps, speed_label=None):
        _emit_live_progress(
            bar_manager,
            task_key,
            segs_before + done,
            bytes_before + cur_bytes,
            speed_bps,
            elapsed_before + done * avg_dur,
        )

    return _cb


def _reset_live_timestamps(out_path: Path) -> None:
    """Rewrite *out_path* in place (stream-copy) so presentation timestamps start at 0."""
    from VibraVid.setup import get_ffmpeg_path

    # Keep the original extension so ffmpeg can infer the output container.
    tmp = out_path.with_name(f"{out_path.stem}.tsreset{out_path.suffix}")
    cmd = [
        get_ffmpeg_path(),
        "-fflags",
        "+genpts+igndts+discardcorrupt",
        "-avoid_negative_ts",
        "make_zero",
        "-i",
        str(out_path),
        "-c",
        "copy",
        "-y",
        str(tmp),
    ]

    try:
        logger.info(f"Live DASH: resetting timestamps to zero for {out_path.name} with cmd: {' '.join(cmd)}")
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        if result.returncode != 0 or not tmp.exists() or tmp.stat().st_size == 0:
            logger.warning(f"Live DASH: timestamp reset skipped (ffmpeg rc={result.returncode}) — keeping original {out_path.name}")
            tmp.unlink(missing_ok=True)
            return
        tmp.replace(out_path)
        logger.info(f"Live DASH: reset timestamps to zero -> {out_path.name}")
    except Exception as exc:
        logger.warning(f"Live DASH: timestamp reset failed ({exc}) — keeping original {out_path.name}")
        tmp.unlink(missing_ok=True)


def _emit_merge_progress(bar_manager, task_key: str, seg_done: int, merge_size: int) -> None:
    bar_manager.handle_progress_line(
        {
            "task_key": task_key,
            "pct": 100,
            "segments": f"{seg_done}/{seg_done}",
            "size": f"{_fmt_size(merge_size)}/{_fmt_size(merge_size)}",
            "speed": "Merge",
        }
    )


class LiveDownloadMixin:
    @staticmethod
    def _most_recent_segments(segs: list, seconds: float) -> list:
        """The tail of *segs* (nearest the live edge) that covers *seconds*; all of *segs* when they cover less."""
        tail: list = []
        acc = 0.0
        for seg in reversed(segs):
            tail.append(seg)
            acc += getattr(seg, "duration", 0.0)
            if acc >= seconds:
                break
        tail.reverse()
        return tail

    def _live_mux_session(self) -> "LiveMux | None":
        """The live mux shared by this download's live DASH tracks, created (or ruled out) once."""
        with self._live_mux_lock:
            if not self._live_mux_decided:
                self._live_mux_decided = True
                self._live_mux = LiveMux.create(self)
            return self._live_mux

    def _live_start_aligner(self) -> "LiveStartAligner | None":
        """The start aligner shared by this download's live DASH tracks, created (or ruled out) once."""
        with self._live_mux_lock:
            if not self._live_aligner_decided:
                self._live_aligner_decided = True
                self._live_aligner = LiveStartAligner.create(self)
            return self._live_aligner

    def _live_download_batch(
        self, dl_batch: list[dict], stream_dir: Path, headers: dict, stream, progress_cb=None
    ) -> list[Path]:
        self._run_dl(
            dl_batch,
            stream_dir,
            headers,
            progress_cb=progress_cb,
            stream=stream,
            event_cb=None,
            default_ext="mp4",
        )

        # Reconstruct the expected path for each entry in dl_batch.
        ordered: list[Path] = []
        for seg in dl_batch:
            seg_ext = detect_seg_ext(seg.get("url", ""), default="mp4")
            if seg_ext == "m4s":
                seg_ext = "mp4"
            ordered.append(stream_dir / f"seg_{seg['number']:05d}.{seg_ext}")

        return ordered

    def _download_hls_live_stream(
        self,
        stream,
        bar_manager,
        live_decryption: bool = False,
        *,
        first_content: str | None = None,
        base_url: str | None = None,
    ) -> None:
        """
        Download a live HLS stream by polling the variant playlist and downloading each new batch of segments as they appear.
        """
        playlist_url: str = stream.playlist_url
        all_headers: dict = self._build_headers()
        task_key: str = self._stream_task_key(stream)
        stream_dir: Path = self._make_stream_dir(stream, "hls")

        key_cache: dict[str, bytes] = {}
        all_paths: list[Path] = []  # ordered: [init, media_1, media_2, ...]
        seen_seg_keys: set = set()

        # 0 is reserved for the init segment; media segments start at 1.
        seg_index: int = 1
        seg_done: int = 0
        total_bytes: int = 0
        elapsed_dur: float = 0.0

        # Range support: seg_done/elapsed_dur only count segments actually downloaded
        seg_start, seg_end = self.max_segments if isinstance(self.max_segments, tuple) else (0, self.max_segments)
        time_start, time_end = self.max_time if isinstance(self.max_time, tuple) else (0.0, self.max_time)
        total_seg_seen: int = 0
        total_time_seen: float = 0.0

        init_downloaded: bool = False
        last_fresh_segs: list[dict] = []

        def _fetch_key(key_url: str, iv: str | None = None) -> bytes:
            if key_url in key_cache:
                return key_cache[key_url]

            with create_client(headers=all_headers, timeout=REQUEST_TIMEOUT, follow_redirects=True) as c:
                r = c.get(key_url)
                r.raise_for_status()
                key_data = r.content

            if len(key_data) != 16:
                logger.warning(f"AES-128 key length {len(key_data)} bytes (expected 16) for {key_url}")
            key_cache[key_url] = key_data
            logger.info(f"Live HLS AES-128 key fetched{iv}:{key_data.hex()}")
            return key_data

        def _decrypt_segment(fp: Path, seg_meta: dict) -> None:
            """Decrypt a single AES-128 segment in-place (atomic replace)."""
            enc = seg_meta.get("enc") or {}
            if str(enc.get("method") or "NONE").upper() != "AES-128":
                return

            key_data = enc.get("key_bytes")
            if key_data is None:
                key_url = enc.get("key_url")
                if not key_url:
                    raise RuntimeError(f"Missing AES-128 key URL for {fp.name}")
                key_data = _fetch_key(key_url, enc.get("iv"))

            seg_number = int(seg_meta.get("number", 0))
            logger.debug(f"AES-128 live-decrypt seg={fp.name} iv={enc.get('iv')}")

            tmp = fp.with_suffix(fp.suffix + ".dec")
            decrypt_aes128_file(fp, tmp, key_data, enc.get("iv"), seg_number)

            for attempt in range(1, 6):
                try:
                    if fp.exists():
                        fp.unlink()
                    tmp.replace(fp)
                    break
                except OSError:
                    if attempt >= 5:
                        raise
                    time.sleep(0.05 * attempt)
            logger.debug(f"AES-128 live-decrypted -> {fp.name}")

        def _process_batch(dl_batch: list[dict], batch_paths: list[Path]) -> int:
            """
            Decrypt and accumulate segments into all_paths.
            Returns total bytes successfully processed in this batch.
            """
            nonlocal seg_done, total_bytes, elapsed_dur
            batch_bytes = 0

            for fp, seg_meta in zip(batch_paths, dl_batch, strict=False):
                if not fp.exists() or fp.stat().st_size == 0:
                    enc_method = str((seg_meta.get("enc") or {}).get("method") or "NONE").upper()
                    if enc_method != "NONE":
                        logger.warning(f"Live HLS: empty or missing segment {fp.name} — {enc_method} key was never fetched (segment download failed before decrypt)")
                    else:
                        logger.warning(f"Live HLS: empty or missing segment {fp.name}")
                    continue
                try:
                    _decrypt_segment(fp, seg_meta)
                except Exception as exc:
                    logger.error(f"Live HLS decrypt error for {fp.name}: {exc}")

                sz = fp.stat().st_size if fp.exists() else 0
                batch_bytes += sz
                total_bytes += sz
                elapsed_dur += seg_meta.get("duration", 0.0)
                all_paths.append(fp)
                seg_done += 1

            return batch_bytes

        if base_url is None:
            base_url = hls_base_url(playlist_url)

        current_content: str | None = first_content
        target_duration: int = 6
        empty_polls: int = 0
        poll_failures: int = 0

        _emit_live_progress(bar_manager, task_key, seg_done, total_bytes, 0.0)
        logger.info(f"Live HLS download started | url={playlist_url}")

        while not self._stop_check():
            if current_content is None:
                try:
                    with create_client(headers=all_headers, timeout=REQUEST_TIMEOUT, follow_redirects=True) as c:
                        resp = c.get(playlist_url)
                        resp.raise_for_status()
                        current_content = resp.text
                    poll_failures = 0
                except Exception as exc:
                    # When a live stream ends, its playlist URL typically starts returning errors (403/404 — token/session gone).
                    poll_failures += 1
                    logger.error(f"Live HLS poll failed (failures={poll_failures}/{MAX_EMPTY_POLLS}): {exc}")
                    if poll_failures >= MAX_EMPTY_POLLS:
                        logger.info(f"Live HLS: {MAX_EMPTY_POLLS} consecutive poll failures — stream appears ended, stopping")
                        break
                    _sleep_interruptible(target_duration, self._stop_check)
                    continue

            new_segs, new_init_url, target_duration, media_sequence, is_ended = parse_hls_live_playlist(
                current_content, base_url, enc_override=getattr(self, "hls_enc_override", None)
            )

            # ── Init segment: download exactly once as seg_00000 ──────────────
            if new_init_url and not init_downloaded:
                logger.info(f"Live HLS: downloading init segment {new_init_url}")
                init_batch = [
                    {
                        "url": new_init_url,
                        "number": 0,
                        "seg_type": "init",
                        "enc": {"method": "NONE"},
                    }
                ]

                init_paths = self._live_download_batch(init_batch, stream_dir, all_headers, stream)
                if init_paths and init_paths[0].exists() and init_paths[0].stat().st_size > 0:
                    all_paths.append(init_paths[0])
                    logger.info(f"Live HLS: init segment ready ({init_paths[0].name})")
                else:
                    logger.warning("Live HLS: init segment download produced no valid file")
                # seg_index stays at 1 — media segments start from seg_00001
                init_downloaded = True

            # ── Filter already-seen segments
            fresh_segs: list[dict] = []
            for offset, seg in enumerate(new_segs):
                dedup_key = (media_sequence + offset, seg["url"])
                if dedup_key not in seen_seg_keys:
                    seen_seg_keys.add(dedup_key)
                    fresh_segs.append(seg)

            # ── Download & process fresh segments
            if fresh_segs:
                empty_polls = 0

                # Check limit before building the batch
                if seg_end is not None and seg_done >= (seg_end - seg_start):
                    logger.info(f"Live HLS: max_segments range reached ({seg_done} downloaded) — stopping")
                    break
                if time_end is not None and elapsed_dur >= (time_end - time_start):
                    logger.info(f"Live HLS: max_time range reached ({_fmt_dur(elapsed_dur)}) — stopping")
                    break

                # Skip segments that fall before the start offset (still counted toward total_seg_seen/total_time_seen, but not downloaded).
                kept_segs: list[dict] = []
                for seg in fresh_segs:
                    total_seg_seen += 1
                    total_time_seen += seg.get("duration", 0.0)
                    if total_seg_seen <= seg_start or total_time_seen <= time_start:
                        continue
                    kept_segs.append(seg)
                fresh_segs = kept_segs

                if fresh_segs:
                    last_fresh_segs = fresh_segs

                    # Clamp to remaining quota
                    if seg_end is not None:
                        remaining = (seg_end - seg_start) - seg_done
                        fresh_segs = fresh_segs[:remaining]

                    # Assign stable global numbers to every segment in this batch.
                    dl_batch: list[dict] = []
                    for seg in fresh_segs:
                        dl_batch.append(
                            {
                                "url": seg["url"],
                                "number": seg_index,
                                "seg_type": "media",
                                "enc": seg.get("enc") or {"method": "NONE"},
                                "duration": seg.get("duration", 0.0),
                            }
                        )
                        seg_index += 1

                    _batch_avg_dur = sum(s.get("duration", 0.0) for s in dl_batch) / max(len(dl_batch), 1)
                    _segs_before = seg_done
                    _bytes_before = total_bytes
                    _elapsed_before = elapsed_dur

                    _batch_progress_cb = _make_batch_progress_cb(bar_manager, task_key, _segs_before, _bytes_before, _elapsed_before, _batch_avg_dur)

                    batch_t0 = time.monotonic()

                    # _live_download_batch returns paths ordered by seg number
                    batch_paths = self._live_download_batch(
                        dl_batch, stream_dir, all_headers, stream, progress_cb=_batch_progress_cb
                    )
                    elapsed = max(time.monotonic() - batch_t0, 0.001)

                    batch_bytes = _process_batch(dl_batch, batch_paths)
                    speed_bps = batch_bytes / elapsed

                    # Emit a final progress update for the batch (in case the last segment was small and the progress callback didn't get called).
                    _emit_live_progress(bar_manager, task_key, seg_done, total_bytes, speed_bps, elapsed_dur)
                    if seg_done % 10 == 0 or seg_done < 10:
                        logger.info(f"Live HLS: +{len(fresh_segs)} segs | total={seg_done} | {_fmt_size(total_bytes)} | {speed_bps / 1024:.1f} KB/s | dur={_fmt_dur(elapsed_dur)}")

                if seg_end is not None and seg_done >= (seg_end - seg_start):
                    logger.info(f"Live HLS: max_segments range reached ({seg_done} downloaded) — stopping")
                    break

                if time_end is not None and elapsed_dur >= (time_end - time_start):
                    logger.info(f"Live HLS: max_time range reached ({_fmt_dur(elapsed_dur)}) — stopping")
                    break

            if not fresh_segs:
                empty_polls += 1
                logger.info(f"Live HLS: no new segments (empty_polls={empty_polls}/{MAX_EMPTY_POLLS})")
                if empty_polls >= MAX_EMPTY_POLLS:
                    logger.info(f"Live HLS: {MAX_EMPTY_POLLS} consecutive polls with no new segments — stream appears ended, stopping")
                    break

            if is_ended:
                logger.info("Live HLS stream finished (#EXT-X-ENDLIST)")
                break

            if self._stop_check():
                logger.info("Live HLS: stop requested — exiting poll loop")
                break

            _sleep_interruptible(max(1, target_duration // 2), self._stop_check)
            current_content = None

        # ── Merge ─────────────────────────────────────────────────────────────
        if not all_paths:
            logger.error("Live HLS: no segments were downloaded — nothing to merge")
            return

        sample_url = next(
            (s["url"] for s in last_fresh_segs if s.get("seg_type") != "init"),
            last_fresh_segs[0]["url"] if last_fresh_segs else "",
        )
        ext = merged_segment_ext(sample_url, default="ts")

        out_path = self.output_dir / self._out_filename(stream, ext)
        merge_size = sum(p.stat().st_size for p in all_paths if p.exists())

        logger.info(f"Live HLS binary merge | segs={len(all_paths)} | {_fmt_size(merge_size)} -> {out_path.name}")
        _emit_merge_progress(bar_manager, task_key, seg_done, merge_size)
        binary_merge_segments(all_paths, out_path, merge_logger=logger)

        # Reset absolute fragment timestamps (e.g. the huge PCR/PTS base offset
        # left by 'startover'/live HLS archives). Without this the merged file
        # keeps an absurd absolute start time that corrupts its duration.
        if out_path.suffix.lower() in (".mp4", ".m4s", ".m4a", ".ts"):
            _reset_live_timestamps(out_path)

        if not (out_path.exists() and out_path.stat().st_size > 0):
            logger.error(f"Live HLS binary merge produced an empty file: {out_path}")
            return

        # Post-merge decryption for SAMPLE-AES/CBCS (mirrors the non-live generic path in downloader.py)
        stream_is_encrypted = getattr(getattr(stream, "drm", None), "method", None) is not None
        if (not live_decryption) and self.key and stream_is_encrypted:
            post_merge_path = out_path.with_suffix(out_path.suffix + ".dec")

            try:
                decryptor = Decryptor()
                if decryptor.decrypt(
                    str(out_path), self.key, str(post_merge_path), stream_type=stream.type, progress_cb=bar_manager.decrypt_progress_cb(task_key), key_fallback=self.key_fallback
                ):
                    try:
                        out_path.unlink(missing_ok=True)
                        post_merge_path.rename(out_path)
                        bar_manager.handle_progress_line({"task_key": task_key, "pct": 100})
                    except Exception as exc:
                        logger.error(f"Live HLS: decrypt rename failed: {exc}")
                        post_merge_path.unlink(missing_ok=True)

                else:
                    kid_hint = ", ".join(stream.drm.get_all_kids()) if stream.drm else ""
                    logger.warning(f"Live HLS: post-merge decryption failed for {out_path.name} (kid={kid_hint or 'unknown'})")
                    console.print(f"[bold red][!] WARNING[/bold red] Live HLS decryption failed for [yellow]{stream.type}[/yellow]" + (f" (KID(s): [magenta]{kid_hint}[/magenta])" if kid_hint else ""))
                    post_merge_path.unlink(missing_ok=True)

            except Exception as exc:
                logger.error(f"Live HLS: post-merge decryption error: {exc}")
                post_merge_path.unlink(missing_ok=True)

        logger.info(f"Live HLS merge complete | segs={len(all_paths)} -> {out_path.name} ({_fmt_size(out_path.stat().st_size)})")

    def _download_dash_live_stream(
        self, stream, bar_manager, live_decryption: bool = False, *, mpd_url: str, headers: dict
    ) -> None:
        """
        Download a live DASH stream by polling the dynamic MPD and downloading each new batch of segments as they appear.
        """
        task_key: str = self._stream_task_key(stream)
        stream_dir: Path = self._make_stream_dir(stream, "dash")

        seen_urls: set[str] = set()
        all_paths: list[Path] = []
        seg_index: int = 0
        seg_done: int = 0
        total_bytes: int = 0
        last_media_url: str = ""
        elapsed_dur: float = 0.0
        init_downloaded: bool = False

        # Range support — see _download_hls_live_stream
        seg_start, seg_end = self.max_segments if isinstance(self.max_segments, tuple) else (0, self.max_segments)
        time_start, time_end = self.max_time if isinstance(self.max_time, tuple) else (0.0, self.max_time)
        total_seg_seen: int = 0
        total_time_seen: float = 0.0
        init_path: Path | None = None
        min_update_period: float = 4.0
        tail_pending: bool = self.live_tail_seconds is not None

        # Live merge
        merge_order: list[int] = []
        merger: _LiveMerger | None = None
        merge_out: Path | None = None

        # Live mux
        live_mux = self._live_mux_session()
        mux_send = None

        # All tracks start from the same instant
        aligner = self._live_start_aligner()
        align_start: float | None = None
        align_decided: bool = False
        track_scale: int | None = None

        # Checked once per track (this track's first media segment) to fail fast on a wrong key.
        _key_sanity_pending: bool = True
        tfdt_base: int | None = None
        tfdt_base_decided: bool = False
        merge_init_clear: bool = False
        continuity = FragmentContinuity()

        _decryptor = None
        if live_decryption and self.key:
            try:
                _decryptor = Decryptor()
                logger.info(f"Live DASH: CENC live-decrypt enabled for {stream.type}")
            except Exception as exc:
                logger.warning(f"Live DASH: Decryptor unavailable — segments will remain encrypted: {exc}")

        def _decrypt_seg(fp: Path, is_init: bool = False) -> bool:
            """Returns True if segment is usable (decrypted or no-decrypt-needed), False if failed."""
            nonlocal init_path, _key_sanity_pending, tfdt_base, tfdt_base_decided
            if is_init:
                init_path = fp
                logger.debug(f"Live DASH: init segment cached -> {fp.name}")
                return True

            if not _decryptor or not self.key:
                return True

            run_key_sanity = _key_sanity_pending
            _key_sanity_pending = False
            if not tfdt_base_decided:
                tfdt_base_decided = True
                if _decryptor.supports_tfdt_base and init_path and init_path.exists():
                    tfdt_base = first_fragment_tfdt(fp)
                    logger.info(f"Live DASH: {stream.type} timeline zeroed by flux (tfdt base {tfdt_base})" if tfdt_base is not None else f"Live DASH: {stream.type} segment has no tfdt, timeline rebased afterwards")
            
            dec_tmp = fp.with_suffix(fp.suffix + ".dec")
            try:
                ok, message, _data = _decryptor.decrypt_segment_live(
                    encrypted_path=str(fp),
                    decrypted_path=str(dec_tmp),
                    raw_keys=self.key,
                    init_path=str(init_path) if init_path and init_path.exists() else None,
                    key_sanity=run_key_sanity,
                    tfdt_base=tfdt_base,
                )

                if not ok and message and "key is wrong" in message and self._recover_wrong_key(
                    self._stream_kids(stream),
                    lambda keys: _decryptor.decrypt_segment_live(
                        encrypted_path=str(fp),
                        decrypted_path=str(dec_tmp),
                        raw_keys=keys,
                        init_path=str(init_path) if init_path and init_path.exists() else None,
                        key_sanity=True,
                        tfdt_base=tfdt_base,
                    )[:2],
                ):
                    ok = True

                if not ok:
                    logger.debug(f"Live DASH: decrypt failed for {fp.name}: {message}")
                    if message and "key is wrong" in message:
                        logger.error(f"Live DASH: key sanity check confirmed the stored key is wrong for {fp.name} -- the vault entry for this KID is likely stale/corrupt: {message}")
                        self._register_wrong_key(self._stream_kids(stream), self.license_url, getattr(stream.drm, "pssh", None))
                    
                    dec_tmp.unlink(missing_ok=True)
                    return False

                if not dec_tmp.exists():
                    logger.error(f"Live DASH: decrypt produced no output for {fp.name}")
                    return False

                for attempt in range(1, 6):
                    try:
                        fp.unlink(missing_ok=True)
                        dec_tmp.replace(fp)
                        break
                    except OSError:
                        if attempt >= 5:
                            raise
                        time.sleep(0.05 * attempt)
                logger.debug(f"Live DASH: CENC decrypted -> {fp.name}")
                return True

            except Exception as exc:
                logger.error(f"Live DASH: decrypt error for {fp.name}: {exc}")
                dec_tmp.unlink(missing_ok=True)
                return False

        def _live_merge(fp: Path, key: int, url: str) -> None:
            """Append segment *fp* (position *key*) to the track file, creating it from the init segment on the first one."""
            nonlocal merger, merge_out, mux_send, merge_init_clear
            if merger is None:
                ext = detect_seg_ext(url, default="mp4")
                merge_out = self.output_dir / self._out_filename(stream, "mp4" if ext == "m4s" else ext)
                merger = _LiveMerger(merge_out, merge_order)
                if init_path is not None and init_path.exists():
                    merge_order.append(0)
                    merge_init = init_path
                    if _decryptor is not None and self.key:
                        clear_init = init_path.with_name(f"{init_path.stem}.clear{init_path.suffix}")
                        if _decryptor.clear_init(init_path, clear_init):
                            merge_init, merge_init_clear = clear_init, True
                    merger.submit(0, merge_init)
                if live_mux is not None:
                    mux_send = live_mux.join(stream, init_path, self._stop_check, rebased=tfdt_base is not None)
            merge_order.append(key)
            merger.submit(key, fp)

            continuity.observe(key, first_fragment_tfdt(fp))
            if mux_send is not None:
                try:
                    mux_send(fp.read_bytes())
                except Exception as exc:
                    live_mux.abort(f"piping {stream.type} failed: {exc}")
                    mux_send = None

        def _fetch_and_parse_mpd() -> tuple[list | None, float, bool]:
            nonlocal min_update_period, manifest_has_ads
            try:
                # quiet=True: the live loop re-parses the MPD every few seconds;
                # without it the full stream list is re-logged on every poll.
                parser = DashParser(mpd_url, headers=headers, quiet=True)
                if not parser.fetch_manifest():
                    return None, min_update_period, False

                streams = parser.parse_streams()
                manifest_has_ads = bool(getattr(parser, "ad_periods_skipped", 0))
                meta = parser.live_meta
                upd_period = meta["min_update_period"]
                is_static = meta["is_ended"]

                for s in streams:
                    if s.type == stream.type and s.id == stream.id:
                        return s.segments, upd_period, is_static

                candidates = [s for s in streams if s.type == stream.type]
                if candidates:
                    best = min(candidates, key=lambda s: abs((s.bitrate or 0) - (stream.bitrate or 0)))
                    logger.debug(f"Live DASH: id mismatch — falling back to bitrate match id={best.id} bw={best.bitrate}")
                    return best.segments, upd_period, is_static

                logger.warning("Live DASH: no matching representation in refreshed MPD")
                return None, upd_period, is_static

            except Exception as exc:
                logger.error(f"Live DASH: MPD fetch/parse failed: {exc}")
                return None, min_update_period, False

        current_segments = list(stream.segments)
        is_ended = False
        empty_polls: int = 0
        manifest_has_ads: bool = False
        ad_break = LiveAdBreak()
        poll_failures: int = 0

        _emit_live_progress(bar_manager, task_key, seg_done, total_bytes, 0.0, 0.0)
        logger.info(f"Live DASH download started | stream={stream} | url={mpd_url}")

        while not self._stop_check():
            if current_segments is None:
                segs, min_update_period, is_ended = _fetch_and_parse_mpd()
                if segs is None:
                    # A sustained run of failed MPD polls (e.g. the manifest URL starts returning 403/404 once the event ends).
                    poll_failures += 1
                    logger.warning(f"Live DASH: empty MPD poll (failures={poll_failures}/{MAX_EMPTY_POLLS}) — backing off {min_update_period:.0f} s")
                    if poll_failures >= MAX_EMPTY_POLLS:
                        logger.info(f"Live DASH: {MAX_EMPTY_POLLS} consecutive poll failures — stream appears ended, stopping")
                        break
                    _sleep_interruptible(min_update_period or 4.0, self._stop_check)
                    current_segments = None
                    continue

                poll_failures = 0
                current_segments = segs

            init_segs = [s for s in current_segments if s.seg_type == "init"]
            media_segs = [s for s in current_segments if s.seg_type == "media"]

            if init_segs and not init_downloaded:
                init_seg = init_segs[0]
                if init_seg.url not in seen_urls:
                    logger.info(f"Live DASH: downloading init segment | url={init_seg.url}")
                    init_entry: dict = {
                        "url": init_seg.url,
                        "number": 0,
                        "seg_type": "init",
                        "enc": {"method": "NONE"},
                    }

                    if init_seg.byte_range:
                        init_entry["headers"] = {"Range": f"bytes={init_seg.byte_range}"}

                    init_paths = self._live_download_batch([init_entry], stream_dir, headers, stream)
                    if init_paths and init_paths[0].exists():
                        req_headers = dict(headers)
                        req_headers.update(init_entry.get("headers") or {})
                        _repair_init_file(init_paths[0], req_headers)
                        _decrypt_seg(init_paths[0], is_init=True)
                        all_paths.extend(init_paths)
                        seg_index = 1
                        logger.info(f"Live DASH: init segment ready | {init_paths[0].name}")

                    seen_urls.add(init_seg.url)
                init_downloaded = True

            fresh_segs: list = []
            for seg in media_segs:
                if seg.url not in seen_urls:
                    seen_urls.add(seg.url)
                    fresh_segs.append(seg)
                    last_media_url = seg.url

            if fresh_segs:
                empty_polls = 0
                if ad_break.is_started():
                    # Most polls between two segments see the stitched ad periods, so only a break that really held the recording is worth a line.
                    long_break = ad_break.elapsed() >= LIVE_AD_BREAK_LOG_SECONDS
                    (logger.info if long_break else logger.debug)(f"Live DASH: the ad break is over after {ad_break.elapsed():.0f}s, content is back")
                ad_break.reset()

                if seg_end is not None and seg_done >= (seg_end - seg_start):
                    break

                if time_end is not None and elapsed_dur >= (time_end - time_start):
                    break

                # Skip segments that fall before the start offset (still counted toward total_seg_seen/total_time_seen, but not downloaded).
                kept_segs: list = []
                for seg in fresh_segs:
                    total_seg_seen += 1
                    total_time_seen += getattr(seg, "duration", 0.0)
                    if total_seg_seen <= seg_start or total_time_seen <= time_start:
                        continue

                    kept_segs.append(seg)
                fresh_segs = kept_segs

            if fresh_segs:
                if seg_end is not None:
                    remaining = (seg_end - seg_start) - seg_done
                    if remaining <= 0:
                        break
                    fresh_segs = fresh_segs[:remaining]

                # Clamp the batch to the remaining max_time budget so we don't
                # grab the entire DVR back-window in one shot.
                # Keep the MOST RECENT segments (tail, nearest the live edge)
                if time_end is not None and elapsed_dur < (time_end - time_start):
                    budget = (time_end - time_start) - elapsed_dur
                    clamped = self._most_recent_segments(fresh_segs, budget)
                    if len(clamped) < len(fresh_segs):
                        logger.info(f"Live DASH: clamping batch to most recent {len(clamped)}/{len(fresh_segs)} segs (~{sum(getattr(s, 'duration', 0.0) for s in clamped):.0f}s) for max_time budget {budget:.0f}s")
                    fresh_segs = clamped

                # No max_time: the recording runs until the live really ends, so the
                # first poll only goes back as far as the caller asked (e.g. the start
                # of the event) instead of grabbing the whole DVR back-window.
                elif time_end is None and tail_pending:
                    tail_pending = False
                    window = sum(getattr(s, "duration", 0.0) for s in fresh_segs)
                    tail = self._most_recent_segments(fresh_segs, self.live_tail_seconds)
                    if len(tail) < len(fresh_segs):
                        logger.info(f"Live DASH: starting {self.live_tail_seconds:.0f}s behind the live edge: {len(tail)}/{len(fresh_segs)} segs of the DVR window")
                    else:
                        logger.warning(f"Live DASH: the DVR window ({window:.0f}s) is shorter than the {self.live_tail_seconds:.0f}s asked: recording from the start of the window")
                    fresh_segs = tail

                dl_batch: list[dict] = []
                for seg in fresh_segs:
                    entry: dict = {
                        "url": seg.url,
                        "number": seg_index,
                        "seg_type": "media",
                        "enc": {"method": "NONE"},
                    }
                    if seg.byte_range:
                        entry["headers"] = {"Range": f"bytes={seg.byte_range}"}
                    dl_batch.append(entry)
                    seg_index += 1

                _batch_avg_dur = sum(getattr(s, "duration", 0.0) for s in fresh_segs) / max(len(fresh_segs), 1)

                logger.debug(f"Live DASH: batch first URL: {getattr(fresh_segs[0], 'url', '?')[:120]}")
                batch_t0 = time.monotonic()
                batch_bytes = 0
                decrypt_fails = 0
                raw_bytes = 0
                batch_size = len(dl_batch)

                for first in range(0, batch_size, LIVE_BACKLOG_CHUNK):
                    # A stop (Ctrl+C, end of the live) between two groups keeps what was already merged and skips the rest.
                    if first and self._stop_check():
                        logger.info(f"Live DASH: stop requested after {first}/{batch_size} segs of this batch")
                        break

                    chunk = dl_batch[first:first + LIVE_BACKLOG_CHUNK]
                    chunk_segs = fresh_segs[first:first + LIVE_BACKLOG_CHUNK]
                    chunk_durs = sum(getattr(seg, "duration", 0.0) for seg in fresh_segs[first:first + LIVE_BACKLOG_CHUNK])
                    _batch_progress_cb = _make_batch_progress_cb(bar_manager, task_key, seg_done, total_bytes, elapsed_dur, _batch_avg_dur)
                    chunk_paths = self._live_download_batch(chunk, stream_dir, headers, stream, progress_cb=_batch_progress_cb)

                    for fp, dl_entry, chunk_seg in zip(chunk_paths, chunk, chunk_segs, strict=True):
                        if not fp.exists() or fp.stat().st_size == 0:
                            logger.warning(f"Live DASH: empty or missing segment {fp.name}")
                            continue

                        # Start every track from the same instant: the first segment of this track tells when it starts, the tracks agree on the latest start,
                        # and a track that began earlier drops the segments that end before it.
                        if track_scale is None and init_path is not None and init_path.exists():
                            track_scale = track_timescale(init_path)
                        scale = track_scale
                        frag_tfdt = first_fragment_tfdt(fp) if scale else None
                        if not align_decided:
                            align_decided = True
                            if aligner is not None and frag_tfdt is not None:
                                align_start = aligner.common_start(stream, frag_tfdt / scale, self._stop_check)
                                if align_start is not None and align_start - frag_tfdt / scale > 0.001:
                                    logger.info(f"Live DASH: {stream.type} starts {align_start - frag_tfdt / scale:.2f}s before the other track(s): dropping the segments before the common start")
                            elif aligner is not None:
                                aligner.leave(stream)
                        if align_start is not None and frag_tfdt is not None and frag_tfdt / scale + getattr(chunk_seg, "duration", 0.0) / 2 <= align_start:
                            fp.unlink(missing_ok=True)
                            continue

                        raw_bytes += fp.stat().st_size
                        ok = _decrypt_seg(fp, is_init=False)
                        if not ok:
                            decrypt_fails += 1
                            continue

                        sz = fp.stat().st_size if fp.exists() else 0
                        batch_bytes += sz
                        total_bytes += sz
                        all_paths.append(fp)
                        seg_done += 1
                        _live_merge(fp, dl_entry["number"], dl_entry["url"])

                    elapsed_dur += chunk_durs

                elapsed = max(time.monotonic() - batch_t0, 0.001)
                if decrypt_fails:
                    avg_raw = raw_bytes / max(decrypt_fails, 1)
                    hint = (
                        "CDN served stub/error pages — live event may have ended or token expired"
                        if avg_raw < 16 * 1024
                        else "segments may be corrupt or keys mismatched"
                    )
                    logger.warning(f"Live DASH: {decrypt_fails}/{batch_size} segments failed decrypt (avg {_fmt_size(int(avg_raw))}/seg — {hint})")

                speed_bps = batch_bytes / elapsed
                _emit_live_progress(bar_manager, task_key, seg_done, total_bytes, speed_bps, elapsed_dur)
                logger.info(f"Live DASH: +{len(fresh_segs)} segs | total={seg_done} | {_fmt_size(total_bytes)} | {speed_bps / 1024:.1f} KB/s | dur={_fmt_dur(elapsed_dur)}")

                if seg_end is not None and seg_done >= (seg_end - seg_start):
                    logger.info(f"Live DASH: max_segments range reached ({seg_done} downloaded) — stopping")
                    break

                if time_end is not None:
                    budget = time_end - time_start
                    logger.debug(f"Live DASH: max_time check elapsed={elapsed_dur:.1f}s budget={budget:.0f}s")
                    if elapsed_dur >= budget:
                        logger.info(f"Live DASH: max_time range reached ({_fmt_dur(elapsed_dur)}) — stopping")
                        break

            if not fresh_segs:
                was_in_break = ad_break.is_started()
                if ad_break.holds(manifest_has_ads):
                    if not was_in_break:
                        logger.debug("Live DASH: no new content and the manifest has advertisement periods: ad break, waiting for the content to come back")
                else:
                    empty_polls += 1
                    logger.info(f"Live DASH: no new segments (empty_polls={empty_polls}/{MAX_EMPTY_POLLS})")
                    if empty_polls >= MAX_EMPTY_POLLS:
                        logger.info(f"Live DASH: {MAX_EMPTY_POLLS} consecutive polls with no new segments — stream appears ended, stopping")
                        break

            if is_ended:
                logger.info("Live DASH: MPD type=static — stream finished")
                break

            if self._stop_check():
                logger.info("Live DASH: stop requested — exiting poll loop")
                break

            poll_sleep = max(1.0, min_update_period / 2.0)
            logger.debug(f"Live DASH: sleeping {poll_sleep:.1f} s before next poll")
            _sleep_interruptible(poll_sleep, self._stop_check)
            current_segments = None

        # Close this track's pipe; the last track out waits for ffmpeg to finish the file.
        live_muxed = False
        if aligner is not None:
            aligner.leave(stream)
        if live_mux is not None:
            live_mux.leave(stream)
            if live_mux.result:
                self.streaming_mux_result = live_mux.result
                live_muxed = True

        if not all_paths:
            logger.error("Live DASH: no segments were downloaded — nothing to merge")
            if _decryptor is not None:
                _decryptor.close_flux_daemon()
            return

        if seg_done == 0:
            logger.error("Live DASH: every media segment failed — no usable media, skipping merge. The CDN likely served stub/error responses (live event ended, DVR window expired, or geo/token rejected). Re-run while the event is actually live.")
            console.print("[red]Live DASH: no usable media — the stream's segments were rejected by the CDN (event ended / token expired). Re-run while the event is live.")
            if _decryptor is not None:
                _decryptor.close_flux_daemon()
            return

        if merger is not None:
            merger.close()
        if merger is not None and merge_out is not None and not merger.failed and merger.complete:
            out_path = merge_out
            merge_size = out_path.stat().st_size if out_path.exists() else 0
            logger.info(f"Live DASH live merge | segs={len(all_paths)} | {_fmt_size(merge_size)} -> {out_path.name}")
            _emit_merge_progress(bar_manager, task_key, seg_done, merge_size)
        else:
            ext = detect_seg_ext(last_media_url, default="mp4")
            if ext == "m4s":
                ext = "mp4"

            out_path = self.output_dir / self._out_filename(stream, ext)
            merge_size = sum(p.stat().st_size for p in all_paths if p.exists())

            logger.info(f"Live DASH binary merge (live merge unavailable) | segs={len(all_paths)} | {_fmt_size(merge_size)} -> {out_path.name}")
            _emit_merge_progress(bar_manager, task_key, seg_done, merge_size)
            binary_merge_segments(all_paths, out_path, merge_logger=logger)

        # Reset absolute fragment timestamps (e.g. the huge PCR/PTS base offset
        # left by 'startover'/live HLS archives). Without this the merged file
        # keeps an absurd absolute start time that corrupts its duration.
        # Subtracting the first tfdt in place is a few small writes; then PROCESS.normalize_timestamps decides
        # whether the track also gets the ffmpeg re-mux that fixes its container duration (as for every other fMP4 track).
        # When the rebase cannot be done the ffmpeg reset stays as the fallback.
        # Not needed when the live mux already wrote the final file: this track file is then only a leftover.
        if out_path.suffix.lower() in (".mp4", ".m4s", ".m4a", ".ts") and not live_muxed:
            if tfdt_base is not None or rebase_fragment_timestamps(out_path, logger=logger):
                flux_progressive = merge_init_clear and continuity.is_contiguous() and out_path == merge_out and _decryptor is not None
                if not continuity.is_contiguous():
                    logger.info(f"Live DASH: {stream.type} fragments are not contiguous, container fixed with ffmpeg (flux would close the gaps)")
                self._normalize_out_path(out_path, False, flux_path=_decryptor.flux_path if flux_progressive else None)
            else:
                _reset_live_timestamps(out_path)

        if not (out_path.exists() and out_path.stat().st_size > 0):
            logger.error(f"Live DASH binary merge produced an empty file: {out_path}")

        if _decryptor is not None:
            _decryptor.close_flux_daemon()
