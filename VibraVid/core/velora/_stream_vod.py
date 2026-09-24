# 01.04.25

import logging
import os
import struct
import threading
import time
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from VibraVid.core.decryptor import Decryptor, KeysManager
from VibraVid.core.manifest.stream import format_duration
from VibraVid.core.ui.bar_manager import DownloadBarManager, console
from VibraVid.setup import get_ffmpeg_path
from VibraVid.utils import config_manager
from VibraVid.utils.http_client import create_client, get_with_retry
from VibraVid.utils.os import os_manager

from .util._dash import build_dash_ranged_segments, build_dash_sidx_segments
from .util._hls import hls_base_url, parse_hls_variant_playlist
from .util._stream_helpers import detect_seg_ext, is_valid_frag_init, repair_init_segment, safe_name
from .util.formatting import format_size

logger = logging.getLogger("manual")
REQUEST_TIMEOUT = config_manager.config.get_int("REQUESTS", "timeout")


def _frag_init_probe(dl_segs: list[dict], headers: dict) -> tuple[bool, str | None]:
    """Fetch the stream's first init (or, failing that, first media) segment,
    check it's a real ftyp+moov fragmented-MP4 init rather than an opaque
    byte-range slice of one unsegmented file (see `is_valid_frag_init`), and
    opportunistically extract the KID from those same bytes."""
    candidate = next((s for s in dl_segs if s.get("seg_type") == "init"), None) or (dl_segs[0] if dl_segs else None)
    if not candidate:
        return False, None
    try:
        req_headers = dict(headers)
        req_headers.update(candidate.get("headers") or {})
        with create_client(headers=req_headers, timeout=REQUEST_TIMEOUT, follow_redirects=True) as c:
            r = c.get(candidate["url"])
            r.raise_for_status()
            data = r.content

            # Some CDNs answer an init-segment request with 200 OK + a small
            # JS/JSON body embedding the real signed URL instead of an HTTP
            # redirect
            redirect_url = repair_init_segment(data)
            if redirect_url:
                logger.debug(f"init probe got a non-MP4 body, retrying via extracted URL: {redirect_url}")
                r = c.get(redirect_url)
                r.raise_for_status()
                data = r.content
    except Exception as exc:
        logger.debug(f"live-decrypt init probe failed, assuming not live-safe: {exc}")
        return False, None

    if not is_valid_frag_init(data):
        return False, None

    kid = None
    try:
        with os_manager.temp_binary_file(data, suffix=".mp4") as tmp_path:
            kid = Decryptor().detect_encryption(tmp_path)[1]
    except Exception as exc:
        logger.debug(f"KID extraction from probed init failed: {exc}")

    return True, kid


class VodStreamMixin:
    def _apply_max_time(self, dl_segs: list[dict]) -> list[dict]:
        start, end = self.max_time if isinstance(self.max_time, tuple) else (0.0, self.max_time)
        if (not start or start <= 0) and end is None:
            return dl_segs

        acc = 0.0
        result = []
        for seg in dl_segs:
            if seg.get("seg_type") == "init":
                result.append(seg)
                continue

            acc += seg.get("duration", 0.0)
            if acc <= start:
                continue

            result.append(seg)
            if end is not None and acc >= end:
                break

        if len(result) < len(dl_segs):
            end_label = f"{end:.0f}s" if end is not None else "end"
            logger.info(f"Limiting download to [{start:.1f}s, {end_label}) of content")
        return result

    def _assign_segment_durations(self, stream, dl_segs: list[dict], headers: dict) -> None:
        """Populate each media segment's ``"duration"`` (seconds) for the ``--max-time``"""
        start, end = self.max_time if isinstance(self.max_time, tuple) else (0.0, self.max_time)
        if stream.is_live or ((not start or start <= 0) and end is None):
            return

        media = [s for s in dl_segs if s.get("seg_type") == "media"]
        if not media:
            return

        durations = self._segment_durations_from_sidx(dl_segs, headers)
        if durations:
            for seg, dur in zip(media, durations, strict=False):
                seg["duration"] = dur
            logger.info(f"max_time: per-segment durations from sidx ({len(durations)} segs, total {sum(durations):.0f}s)")
        elif stream.duration > 0:
            avg = stream.duration / len(media)
            logger.info(f"max_time: no sidx, using manifest average {avg:.3f}s/seg")
            for seg in media:
                seg["duration"] = avg

    def _segment_durations_from_sidx(self, dl_segs: list[dict], headers: dict) -> list[float] | None:
        """Exact per-segment durations from the file's ``sidx`` (segment index) box."""
        media = [s for s in dl_segs if s.get("seg_type") == "media"]
        init_seg = next((s for s in dl_segs if s.get("seg_type") == "init"), None)
        if init_seg is None or not media:
            return None
        try:
            with create_client(headers=headers, timeout=REQUEST_TIMEOUT, follow_redirects=True) as c:
                r = c.get(init_seg["url"], headers=init_seg.get("headers"))
                r.raise_for_status()
                data = r.content

            idx = data.find(b"sidx")
            if idx < 0:
                return None
            p = idx + 4
            version = data[p]  # version (1) + flags (3)
            p += 4
            p += 4  # reference_ID
            timescale = struct.unpack(">I", data[p : p + 4])[0]
            p += 4
            if timescale <= 0:
                return None
            p += 8 if version == 0 else 16  # earliest_presentation_time + first_offset
            p += 2  # reserved
            ref_count = struct.unpack(">H", data[p : p + 2])[0]
            p += 2

            durs: list[float] = []
            for _ in range(ref_count):
                p += 4  # reference type (1 bit) + size (31 bits)
                subdur = struct.unpack(">I", data[p : p + 4])[0]
                p += 4
                p += 4  # SAP
                durs.append(subdur / timescale)

            if len(durs) < len(media):
                return None
            return durs[: len(media)]
        except Exception as e:
            logger.debug(f"sidx parse failed: {e}")
            return None

    def _stream_task_key(self, stream) -> str:
        if stream.type == "video":
            return self._video_task_key

        if stream.type == "subtitle":
            lang = (stream.resolved_language or stream.language or "und").lower()
            return f"sub_{lang.split('-')[0]}{self._sub_discriminator(stream)}"

        lang = (stream.resolved_language or stream.language or "und").lower()
        return f"aud_{lang.split('-')[0]}"

    def _make_stream_dir(self, stream, protocol: str) -> Path:
        if stream.type == "video":
            name = f"v_{safe_name(stream.resolution or 'unknown')}"
        elif stream.type == "subtitle":
            lang = safe_name((stream.language or "und").lower())
            name = f"s_{lang}{self._sub_discriminator(stream)}"
        else:
            name = f"a_{safe_name((stream.language or 'und').lower())}"

        d = self._tmp_dir / name
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _has_matching_key(self, stream) -> bool:
        """True if this stream can be downloaded: unencrypted, no KID known yet from the
        manifest (resolved later, e.g. PSSH-only DASH), a bare placeholder key was supplied
        (resolved against the detected KID at decrypt time), or a provided key's KID matches
        one of the KIDs the manifest already told us this stream needs."""
        drm = getattr(stream, "drm", None)
        if drm is None or not drm.is_encrypted():
            return True

        if not KeysManager.normalize(self.key):
            return False

        required_kids = {k.lower() for k in drm.get_all_kids() if k}
        if not required_kids:
            return True

        return any(self._key_matches_kid(kid) for kid in required_kids)

    def _needs_kid_probe(self, stream) -> bool:
        """True when this stream is encrypted and we have keys to try, but the manifest didn't tell us the KID"""
        drm = getattr(stream, "drm", None)
        if drm is None or not drm.is_encrypted():
            return False
        if not KeysManager.normalize(self.key):
            return False
        return not any(k for k in drm.get_all_kids())

    def _key_matches_kid(self, kid: str) -> bool:
        """True if a provided key covers *kid*, or a bare placeholder key was supplied (resolved against the detected KID at decrypt time)."""
        norm_keys = KeysManager.normalize(self.key)
        if not norm_keys:
            return False
        if len(norm_keys) == 1 and norm_keys[0][0] == "1":
            return True
        return any(k == kid.lower() for k, _ in norm_keys)

    def _skip_stream_no_key(self, stream, required: str, bar_manager=None, task_key: str | None = None) -> None:
        """Log and report a track skipped pre-download for lack of a matching key. Never downloaded, so it's simply absent from the merge -- not a decrypt failure, doesn't block muxing the rest."""
        label = self._decrypt_track_label(stream)
        logger.error(f"Skipping {label}: no provided key matches required KID(s) {required}")
        console.print(f"[red]Skipping {label}:[/red] no key for required KID {required}")
        with self._decrypt_failures_lock:
            self.decrypt_failures.append(
                {"label": label, "track": label, "message": f"no key for required KID(s): {required}", "skipped": True}
            )
        self._finish_bar_task(bar_manager, task_key or self._stream_task_key(stream), "Skipped")
        self._record_track_done(self._stream_task_key(stream), None)

    def _skip_stream_wrong_key(self, stream, kid: str, bar_manager=None, task_key: str | None = None) -> None:
        """Log and report a track skipped mid-download because a key-sanity check proved the stored key was wrong for the KID. The download is terminated, the track is skipped, and the KID is poisoned so any other segment of any track sharing that KID will also be skipped."""
        label = self._decrypt_track_label(stream)
        logger.error(f"Terminating {label}: stored key proven wrong for KID {kid} -- skipping undecryptable track")
        with self._decrypt_failures_lock:
            self.decrypt_failures.append(
                {
                    "label": label,
                    "track": label,
                    "message": f"wrong key for KID: {kid}",
                    "kid": kid,
                    "skipped": True,
                    "stream_type": getattr(stream, "type", ""),
                }
            )

        self._finish_bar_task(bar_manager, task_key or self._stream_task_key(stream), "Failed")
        self._record_track_done(self._stream_task_key(stream), None)

    # Live trial: wait until the growing single-file segment passes
    _SIDX_CHUNK_BYTES = 16 * 1024 * 1024
    _TRIAL_FIRST_FLOOR = 64 * 1024 * 1024
    _TRIAL_STEP = 8 * 1024 * 1024
    _TRIAL_HEAD_CAP = 128 * 1024 * 1024
    _TRIAL_POLL_SECONDS = 1.0
    _TRIAL_STALL_POLLS = 30

    def _spawn_live_key_trial(self, url: str, stream, seg_number: int, seg_url: str) -> None:
        """Spawn a background thread to fetch the growing single-file segment, check for sample-encryption, and run key-sanity on the first senc-carrying fragment. If the stored key is wrong, poison the KID so all tracks sharing it terminate mid-download."""
        label = self._decrypt_track_label(stream)
        try:
            kids = self._stream_kids(stream)
        except Exception:
            return
        if self._kids_poisoned(kids) is not None:
            return

        try:
            estimated = int(getattr(stream, "estimated_size", 0) or 0)
        except Exception:
            estimated = 0
        threshold = max(self._TRIAL_FIRST_FLOOR, int(estimated * 0.10)) if estimated > 0 else self._TRIAL_FIRST_FLOOR

        seg_ext = detect_seg_ext(seg_url, default="mp4")
        if seg_ext == "m4s":
            seg_ext = "mp4"
        logger.info(f"live key-trial armed for {label} (head check once main file passes {threshold // (1024 * 1024)}MB)")

        def _run() -> None:
            try:
                norm_keys = KeysManager.normalize(self.key)
            except Exception:
                return
            if not norm_keys:
                return
            
            keys_arg = [f"{str(k).lower()}:{str(v).lower()}" for k, v in norm_keys]
            try:
                ffmpeg_path = get_ffmpeg_path()
                if not (ffmpeg_path and os.path.exists(ffmpeg_path)):
                    ffmpeg_path = None
            except Exception:
                ffmpeg_path = None

            try:
                stream_dir = self._make_stream_dir(stream, "dash")
            except Exception:
                return
            seg_path = stream_dir / f"seg_{seg_number:05d}.{seg_ext}"
            head_path = stream_dir / "_key_trial_head.mp4"
            in_path = stream_dir / "_key_trial_in.mp4"
            out_path = stream_dir / "_key_trial_out.mp4"

            def _cleanup() -> None:
                for p in (head_path, in_path, out_path):
                    try:
                        if p.exists():
                            p.unlink()
                    except OSError:
                        pass

            decryptor = Decryptor()
            try:
                # Phase 1: wait for the threshold (stat only: safe even if the
                # backend locks the file or preallocates it sparse).
                stalled = 0
                last_size = 0
                while True:
                    if self._stop_check():
                        return
                    if self._kids_poisoned(kids) is not None:
                        return  # someone else already proved it
                    
                    # velora writes to "<final>.part" and renames at the end.
                    size = 0
                    for p in (seg_path.with_name(seg_path.name + ".part"), seg_path):
                        try:
                            size = max(size, p.stat().st_size)
                        except OSError:
                            pass
                        
                    if size == last_size:
                        stalled += 1
                        if stalled >= self._TRIAL_STALL_POLLS:
                            logger.info(f"live key-trial stood down for {label}: main file stopped growing")
                            return
                    else:
                        stalled = 0
                        last_size = size
                    if size >= threshold:
                        break
                    time.sleep(self._TRIAL_POLL_SECONDS)

                logger.info(f"live key-trial fetching head for {label} (main at {size // (1024 * 1024)}MB)")
                t_start = time.monotonic()
                timings: list[str] = []

                def _mark(name: str, t0: float) -> float:
                    now = time.monotonic()
                    timings.append(f"{name}={(now - t0) * 1000:.0f}ms")
                    return now

                # Phase 2a: local snapshot of the bytes velora already wrote
                # ("<final>.part" while downloading, final name once done).
                cut: int | None = None
                head_len = 0
                t = t_start
                src_path = seg_path.with_name(seg_path.name + ".part")
                if not src_path.exists():
                    src_path = seg_path
                try:
                    want = min(src_path.stat().st_size, self._TRIAL_HEAD_CAP)
                    with open(src_path, "rb") as src, open(head_path, "wb") as dst:
                        while head_len < want:
                            buf = src.read(min(self._TRIAL_STEP, want - head_len))
                            if not buf:
                                break
                            dst.write(buf)
                            head_len += len(buf)
                except OSError as exc:
                    logger.debug(f"live key-trial local snapshot failed for {label}: {exc}")
                    _cleanup()
                    head_len = 0
                t = _mark(f"local_copy[{head_len // (1024 * 1024)}MB]", t)
                if head_len:
                    scanned = decryptor.scan_fragments(str(head_path))
                    t = _mark("local_scan", t)
                    if scanned is None:
                        logger.info(f"live key-trial inconclusive for {label}: flux scan unavailable")
                        return
                    tracks, cut = scanned
                    if not (any(int(t_.get("encrypted_fragments") or 0) > 0 for t_ in tracks) and cut):
                        cut = None

                # Phase 2b (fallback): independent head fetch + daemon scan per step.
                range_steps = 0
                with create_client(headers={"User-Agent": "VibraVid-trial"}, timeout=REQUEST_TIMEOUT, follow_redirects=True) as c:
                    while cut is None and head_len < self._TRIAL_HEAD_CAP:
                        range_steps += 1
                        if self._stop_check():
                            return
                        if self._kids_poisoned(kids) is not None:
                            return
                        try:
                            r = c.get(url, headers={"Range": f"bytes={head_len}-{head_len + self._TRIAL_STEP - 1}"})
                        except Exception as exc:
                            logger.debug(f"live key-trial prefetch failed for {label}: {exc}")
                            return
                        if r.status_code != 206 or not r.content:
                            logger.info(f"live key-trial inconclusive for {label}: no Range support (status={r.status_code})")
                            return
                        try:
                            with open(head_path, "ab") as fh:
                                fh.write(r.content)
                            head_len += len(r.content)
                        except OSError:
                            return
                        scanned = decryptor.scan_fragments(str(head_path))
                        if scanned is None:
                            logger.info(f"live key-trial inconclusive for {label}: flux scan unavailable")
                            return
                        tracks, cut = scanned
                        if any(int(t.get("encrypted_fragments") or 0) > 0 for t in tracks) and cut:
                            break
                        cut = None
                if range_steps:
                    t = _mark(f"range_fetch+scan[{range_steps} steps]", t)

                if not cut:
                    logger.info(f"live key-trial inconclusive for {label}: no senc in first {head_len // (1024 * 1024)}MB")
                    return

                try:
                    with open(head_path, "rb") as src:
                        in_path.write_bytes(src.read(cut))
                except OSError:
                    return
                t = _mark(f"prefix_cut[{cut // 1024}KB]", t)

                daemon = decryptor._get_flux_daemon()
                if daemon is None:
                    logger.info(f"live key-trial inconclusive for {label}: no daemon")
                    return
                ok, err = daemon.decrypt(
                    str(in_path), str(out_path), keys_arg, None,
                    key_sanity=True, ffmpeg_path=ffmpeg_path,
                )
                t = _mark("decrypt+sanity", t)
                logger.info(f"live key-trial timing for {label}: {' '.join(timings)} total={(t - t_start) * 1000:.0f}ms")
                if ok and out_path.exists() and out_path.stat().st_size > 0:
                    logger.info(f"live key-trial passed for {label} (prefix {cut // 1024}KB) -- download continues")
                    return

                if err and "key is wrong" in err:
                    logger.error(f"live key-trial: stored key is wrong for {label} -- terminating mid-download")
                    console.print(f"[red]Terminating {label} (live key-trial):[/red] wrong key, stopping download")
                    self._register_wrong_key(kids)
                    return

                logger.info(f"live key-trial inconclusive for {label} ({err or 'no verdict'}) -- download continues")
            finally:
                try:
                    decryptor.close_flux_daemon()
                except Exception:
                    pass
                _cleanup()

        t = threading.Thread(target=_run, daemon=True, name=f"key-trial-{getattr(stream, 'id', '?')}")
        t.start()

    @staticmethod
    def _finish_bar_task(bar_manager, task_key: str, status: str) -> None:
        """Mark a track's progress-bar row terminal so it never freezes at 0B."""
        if bar_manager is None or not task_key:
            return
        try:
            bar_manager.handle_progress_line({"task_key": task_key, "pct": 100, "speed": status})
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Dispatch per stream type
    # ------------------------------------------------------------------
    def _download_stream(self, stream, bar_manager: DownloadBarManager) -> None:
        if not self._has_matching_key(stream):
            required = ", ".join(stream.drm.get_all_kids()) if stream.drm else "unknown"
            self._skip_stream_no_key(stream, required, bar_manager)
            return

        poisoned = self._kids_poisoned(self._stream_kids(stream))
        if poisoned:
            self._skip_stream_wrong_key(stream, poisoned, bar_manager)
            return

        effective_live = self._session_live_decrypt

        if self.manifest_type == "HLS":
            if stream.is_live:
                playlist_url = stream.playlist_url
                all_headers = self._build_headers()
                first_content: str | None = None
                base_url: str | None = None

                try:
                    with create_client(headers=all_headers, timeout=REQUEST_TIMEOUT, follow_redirects=True) as c:
                        resp = get_with_retry(c, playlist_url)
                        first_content = resp.text

                    base_url = hls_base_url(playlist_url)
                except Exception as exc:
                    logger.error(f"Failed to fetch HLS playlist for live detection: {exc}")
                    return
                self._download_hls_live_stream(
                    stream, bar_manager, live_decryption=effective_live, first_content=first_content, base_url=base_url
                )
            else:
                self._download_hls_stream(stream, bar_manager, effective_live)

        if self.manifest_type == "DASH":
            if stream.is_live:
                self._download_dash_live_stream(
                    stream, bar_manager, live_decryption=effective_live, mpd_url=self.url, headers=self._build_headers()
                )
            else:
                self._download_dash_stream(stream, bar_manager, effective_live)

        if self.manifest_type == "ISM":
            self._download_ism_stream(stream, bar_manager, effective_live)

    def _download_hls_stream(self, stream, bar_manager: DownloadBarManager, live_decryption: bool = False) -> None:
        playlist_url = stream.playlist_url
        if not playlist_url:
            logger.error(f"HLS stream has no playlist_url: {stream}")
            return

        all_headers = self._build_headers()
        try:
            with create_client(headers=all_headers, timeout=REQUEST_TIMEOUT, follow_redirects=True) as c:
                resp = get_with_retry(c, playlist_url)
                playlist_content = resp.text
        except Exception as exc:
            logger.error(f"Failed to fetch HLS variant playlist: {exc}")
            return

        base_url = hls_base_url(playlist_url)
        media_segs, init_url = parse_hls_variant_playlist(
            playlist_content, base_url, enc_override=getattr(self, "hls_enc_override", None)
        )

        if not media_segs and not init_url:
            logger.error(f"HLS variant playlist has no segments: {playlist_url}")
            return

        total_dur = sum(seg.get("duration", 0.0) for seg in media_segs)
        stream.duration = total_dur
        resolved_parts = [f"segs={len(media_segs) + (1 if init_url else 0)}"]

        if stream.bitrate and total_dur > 0:
            resolved_parts.append(f"~{format_duration(total_dur)}")
            size = stream.compute_estimated_size()
            if size:
                resolved_parts.append(f"~{format_size(size)}")
        
        if stream.drm and stream.drm.is_encrypted():
            kid_disp = stream.drm.get_kid_display()
            if kid_disp:
                resolved_parts.append(f"KID={kid_disp}")
        logger.info(f"HLS resolved | id={stream.id!r} | {' | '.join(resolved_parts)}")

        dl_segs: list[dict] = []
        if init_url:
            dl_segs.append({"url": init_url, "number": 0, "seg_type": "init", "enc": {"method": "NONE"}})

        offset = len(dl_segs)
        for seg in media_segs:
            dl_segs.append(
                {
                    "url": seg["url"],
                    "number": seg["number"] + offset,
                    "seg_type": "media",
                    "enc": seg["enc"],
                    "duration": seg.get("duration", 0.0),
                    "headers": seg.get("headers", {}),
                }
            )

        if self._needs_kid_probe(stream):
            _, probed_kid = _frag_init_probe(dl_segs, all_headers)
            if probed_kid and not self._key_matches_kid(probed_kid):
                self._skip_stream_no_key(stream, probed_kid, bar_manager)
                return

        seg_start, seg_end = self.max_segments if isinstance(self.max_segments, tuple) else (0, self.max_segments)
        if seg_start > 0 or seg_end is not None:
            if init_url:
                dl_segs = [dl_segs[0]] + dl_segs[1:][seg_start:seg_end]
            else:
                dl_segs = dl_segs[seg_start:seg_end]
            logger.debug(f"Limiting HLS download to segments [{seg_start}:{seg_end}] ({len(dl_segs)} segments)")

        dl_segs = self._apply_max_time(dl_segs)

        def _refresh_hls_seg_urls(failed_numbers: list[int]) -> dict[int, str]:
            logger.info(f"HLS token refresh: {len(failed_numbers)} failed segment(s), attempting manifest refresh to get new token")
            if not self.manifest_refresh_fn:
                return {}

            fresh_master = self.manifest_refresh_fn()
            if not fresh_master:
                logger.error("HLS token refresh: manifest_refresh_fn returned no URL")
                return {}

            fresh_query = urlsplit(fresh_master).query
            failed_set = set(failed_numbers)
            return {
                s["number"]: urlunsplit(urlsplit(s["url"])._replace(query=fresh_query))
                for s in dl_segs
                if s["number"] in failed_set
            }

        self._download_stream_generic(
            dl_segs,
            stream,
            "hls",
            "ts",
            bar_manager,
            live_decryption=live_decryption,
            seg_url_refresh_fn=_refresh_hls_seg_urls,
        )

    def _download_dash_stream(self, stream, bar_manager: DownloadBarManager, live_decryption: bool = False) -> None:
        if not stream.segments:
            logger.error(f"DASH stream has no segments: {stream}")
            return

        # Multi-period tracks (same representation id spread across Periods that use
        # distinct source files and/or mix clear + encrypted content) can't be
        # concatenated into one file: each Period has its own init/moov and DRM.
        media_periods = {s.period_idx for s in stream.segments if s.seg_type == "media"}
        if len(media_periods) > 1:

            # SSAI-style manifests can signal several logical ad-break Periods
            # that all reference the exact same SegmentBase resource
            media_segs = [s for s in stream.segments if s.seg_type == "media"]
            unique_media = {(s.url, s.byte_range) for s in media_segs}
            
            if len(unique_media) <= 1:
                logger.info(f"DASH multi-period stream ({len(media_periods)} periods) but every Period shares the same media segment -- treating as single-period | {stream.type} {stream.resolution or stream.language}")
                deduped_segments: list = []
                seen_media_key = None
                for s in stream.segments:
                    if s.seg_type != "media":
                        deduped_segments.append(s)
                        continue

                    key = (s.url, s.byte_range)
                    if key == seen_media_key:
                        continue

                    seen_media_key = key
                    deduped_segments.append(s)
                stream.segments = deduped_segments
            else:
                # Periods can also differ in media content but still share the exact
                # same init segment (same URL/byte-range) -- e.g. chapter/programming
                # boundaries within one continuously-encrypted Representation, as
                # opposed to true SSAI ad-insertion where each Period is its own
                # source with its own init/DRM. When every Period has its own init
                # entry and they're all byte-identical, it's safe to treat the whole
                # thing as one continuous track instead of paying for a separate
                # merge+decrypt+ffmpeg-concat pass per Period.
                init_segs = [s for s in stream.segments if s.seg_type == "init"]
                init_key_by_period = {}
                for s in init_segs:
                    init_key_by_period.setdefault(s.period_idx, (s.url, s.byte_range))

                shares_one_init = (
                    len(init_key_by_period) == len(media_periods)
                    and len(set(init_key_by_period.values())) == 1
                )

                if shares_one_init:
                    logger.info(f"DASH multi-period stream ({len(media_periods)} periods) but every Period shares the same init segment -- flattening into one continuous track | {stream.type} {stream.resolution or stream.language}")
                    flattened_segments: list = []
                    seen_init = False
                    for s in stream.segments:
                        if s.seg_type == "init":
                            if seen_init:
                                continue
                            seen_init = True
                        flattened_segments.append(s)
                    stream.segments = flattened_segments
                else:
                    logger.info(f"DASH multi-period stream detected ({len(media_periods)} periods) — using per-period pipeline | {stream.type} {stream.resolution or stream.language}")
                    self._download_dash_multiperiod(stream, bar_manager, live_decryption)
                    return

        all_headers = self._build_headers()
        chunk_size = max(8 * 1024 * 1024, 1 * 1024 * 1024)
        media_segments = [s for s in stream.segments if s.seg_type == "media"]
        unique_media_urls = {s.url for s in media_segments}
        is_single_file = len(unique_media_urls) == 1 and not any(s.byte_range for s in media_segments)
        sidx_split = is_single_file and getattr(stream, "no_range_split", False)

        dl_segs: list[dict] = []
        next_num = 0
        single_file_emitted = False
        for seg in stream.segments:
            if seg.byte_range:
                dl_segs.append(
                    {
                        "url": seg.url,
                        "number": next_num,
                        "seg_type": seg.seg_type,
                        "enc": {"method": "NONE"},
                        "headers": {"Range": f"bytes={seg.byte_range}"},
                    }
                )
                next_num += 1

            elif is_single_file and seg.seg_type == "media":
                # Emit the byte-range split once; skip the duplicate period refs.
                if single_file_emitted:
                    continue

                single_file_emitted = True
                if sidx_split:
                    ranged, _total_size = build_dash_sidx_segments(seg.url, all_headers, self._SIDX_CHUNK_BYTES, REQUEST_TIMEOUT)
                else:
                    ranged, _total_size = build_dash_ranged_segments(seg.url, all_headers, chunk_size, REQUEST_TIMEOUT)

                if ranged:
                    for part in ranged:
                        part["number"] = next_num
                        part["seg_type"] = seg.seg_type
                        dl_segs.append(part)
                        next_num += 1

                    continue
                else:
                    dl_segs.append(
                        {"url": seg.url, "number": next_num, "seg_type": seg.seg_type, "enc": {"method": "NONE"}}
                    )
                    next_num += 1
            else:
                entry = {"url": seg.url, "number": next_num, "seg_type": seg.seg_type, "enc": {"method": "NONE"}}
                if seg.inline_data:
                    entry["inline_data"] = seg.inline_data

                dl_segs.append(entry)
                next_num += 1

        self._assign_segment_durations(stream, dl_segs, all_headers)

        seg_start, seg_end = self.max_segments if isinstance(self.max_segments, tuple) else (0, self.max_segments)
        if seg_start > 0 or seg_end is not None:
            if dl_segs and dl_segs[0].get("seg_type") == "init":
                dl_segs = [dl_segs[0]] + dl_segs[1:][seg_start:seg_end]
            else:
                dl_segs = dl_segs[seg_start:seg_end]
            logger.debug(f"Limiting DASH download to segments [{seg_start}:{seg_end}] ({len(dl_segs)} segments)")

        dl_segs = self._apply_max_time(dl_segs)

        def _refresh_dash_seg_urls(failed_numbers: list[int]) -> dict[int, str]:
            logger.info(f"DASH token refresh: {len(failed_numbers)} failed segment(s), attempting manifest refresh to get new token")
            if not self.manifest_refresh_fn:
                return {}

            fresh_master = self.manifest_refresh_fn()
            if not fresh_master:
                logger.error("DASH token refresh: manifest_refresh_fn returned no URL")
                return {}

            fresh_query = urlsplit(fresh_master).query
            failed_set = set(failed_numbers)
            return {
                s["number"]: urlunsplit(urlsplit(s["url"])._replace(query=fresh_query))
                for s in dl_segs
                if s["number"] in failed_set
            }

        # Single-file byte-range DASH: every media segment is a byte range of ONE file.
        byte_range_single_file = bool(media_segments) and all(s.byte_range for s in media_segments)
        effective_live = live_decryption
        probed_kid: str | None = None
        if byte_range_single_file and live_decryption:
            is_frag_init, probed_kid = _frag_init_probe(dl_segs, all_headers)
            if is_frag_init:
                logger.info("DASH byte-range stream, but the init is a real ftyp+moov fragment -- live per-segment decrypt enabled")
            else:
                effective_live = False
                logger.info("DASH byte-range single-file stream: decrypting after merge (not per-segment)")

        if probed_kid is None and self._needs_kid_probe(stream):
            _, probed_kid = _frag_init_probe(dl_segs, all_headers)

        if probed_kid and not self._key_matches_kid(probed_kid):
            self._skip_stream_no_key(stream, probed_kid, bar_manager)
            return

        # If the stream is a single-file DASH and live decryption is enabled, spawn a background thread to fetch the growing segment and check for sample-encryption. If the stored key is wrong, poison the KID so all tracks sharing it terminate mid-download.
        media_dl = [s for s in dl_segs if s.get("seg_type") != "init"]
        whole_file = len(media_dl) == 1 and not (media_dl[0].get("headers") or {}).get("Range")
        if whole_file and effective_live and self.key:
            drm = getattr(stream, "drm", None)
            if drm is not None and drm.is_encrypted():
                self._spawn_live_key_trial(media_dl[0]["url"], stream, media_dl[0]["number"], media_dl[0]["url"])

        self._download_stream_generic(
            dl_segs,
            stream,
            "dash",
            "mp4",
            bar_manager,
            live_decryption=effective_live,
            seg_url_refresh_fn=_refresh_dash_seg_urls,
        )

    def _download_ism_stream(self, stream, bar_manager: DownloadBarManager, live_decryption: bool = False) -> None:
        if not stream.segments:
            logger.error(f"ISM stream has no segments: {stream}")
            return

        all_headers = self._build_headers()
        chunk_size = max(8 * 1024 * 1024, 1 * 1024 * 1024)
        media_segments = [s for s in stream.segments if s.seg_type == "media"]
        unique_media_urls = {s.url for s in media_segments}
        is_single_file = len(unique_media_urls) == 1 and not any(s.byte_range for s in media_segments)
        byte_range_single_file = bool(media_segments) and all(s.byte_range for s in media_segments)

        dl_segs: list[dict] = []
        next_num = 0
        single_file_emitted = False

        ism_enc_dict = {"method": "NONE"}
        if stream.drm and stream.drm.is_encrypted():
            ism_enc_dict = {"method": "playready-piff"}
            if hasattr(stream.drm, "kid") and stream.drm.kid != "N/A":
                ism_enc_dict["kid"] = stream.drm.kid

        for seg in stream.segments:
            if seg.byte_range:
                dl_segs.append(
                    {
                        "url": seg.url,
                        "number": next_num,
                        "seg_type": seg.seg_type,
                        "enc": ism_enc_dict,
                        "headers": {"Range": f"bytes={seg.byte_range}"},
                    }
                )
                next_num += 1
            elif is_single_file and seg.seg_type == "media":
                # Emit the byte-range split once; skip the duplicate period refs.
                if single_file_emitted:
                    continue

                single_file_emitted = True
                ranged, _total_size = build_dash_ranged_segments(seg.url, all_headers, chunk_size, REQUEST_TIMEOUT)

                if ranged:
                    for part in ranged:
                        part["number"] = next_num
                        part["seg_type"] = seg.seg_type
                        part["enc"] = ism_enc_dict
                        dl_segs.append(part)
                        next_num += 1

                    continue
                else:
                    dl_segs.append({"url": seg.url, "number": next_num, "seg_type": seg.seg_type, "enc": ism_enc_dict})
                    next_num += 1
            else:
                dl_segs.append({"url": seg.url, "number": next_num, "seg_type": seg.seg_type, "enc": ism_enc_dict})
                next_num += 1

        self._assign_segment_durations(stream, dl_segs, all_headers)

        seg_start, seg_end = self.max_segments if isinstance(self.max_segments, tuple) else (0, self.max_segments)
        if seg_start > 0 or seg_end is not None:
            dl_segs = dl_segs[seg_start:seg_end]
            logger.debug(f"Limiting ISM download to segments [{seg_start}:{seg_end}] ({len(dl_segs)} segments)")

        dl_segs = self._apply_max_time(dl_segs)

        def _refresh_ism_seg_urls(failed_numbers: list[int]) -> dict[int, str]:
            logger.info(f"ISM token refresh: {len(failed_numbers)} failed segment(s), attempting manifest refresh to get new token")
            if not self.manifest_refresh_fn:
                return {}

            fresh_master = self.manifest_refresh_fn()
            if not fresh_master:
                logger.error("ISM token refresh: manifest_refresh_fn returned no URL")
                return {}

            fresh_query = urlsplit(fresh_master).query
            failed_set = set(failed_numbers)
            return {
                s["number"]: urlunsplit(urlsplit(s["url"])._replace(query=fresh_query))
                for s in dl_segs
                if s["number"] in failed_set
            }

        # A byte-range-split single file has no per-fragment boundary to decrypt
        # independently -- unless the first segment turns out to be a real
        # ftyp+moov init (see `_frag_init_probe`), same check as DASH.
        effective_live = live_decryption
        probed_kid: str | None = None
        if is_single_file:
            effective_live = False
        elif byte_range_single_file and live_decryption:
            is_frag_init, probed_kid = _frag_init_probe(dl_segs, all_headers)
            if is_frag_init:
                logger.info("ISM byte-range stream, but the init is a real ftyp+moov fragment -- live per-segment decrypt enabled")
            else:
                effective_live = False
                logger.info("ISM byte-range single-file stream: decrypting after merge (not per-segment)")

        if probed_kid is None and self._needs_kid_probe(stream):
            _, probed_kid = _frag_init_probe(dl_segs, all_headers)

        if probed_kid and not self._key_matches_kid(probed_kid):
            self._skip_stream_no_key(stream, probed_kid, bar_manager)
            return

        self._download_stream_generic(
            dl_segs, stream, "ism", "mp4", bar_manager, live_decryption=effective_live, seg_url_refresh_fn=_refresh_ism_seg_urls
        )
