# 01.04.25

from pathlib import Path
from typing import TYPE_CHECKING

from VibraVid.utils.http_client import create_client

from ..util._cenc_init import strip_cenc_signaling
from ..util._stream_helpers import is_valid_frag_init, parse_range_header, repair_init_segment
from ..util.formatting import normalize_path_key
from .constants import REQUEST_TIMEOUT, logger

if TYPE_CHECKING:
    from .context import _SegmentDownloadContext


class WorkerMixin:
    def _compute_decrypt_needs(self, ctx: "_SegmentDownloadContext", live_decryption: bool) -> None:
        """Decide which live-decrypt / live-merge-only path (if any) applies to this
        track, based on its protocol and per-segment encryption metadata. Sets the
        ctx.needs_* flags read by both the worker-pool startup and _decrypt_worker's
        dispatch."""
        ctx.needs_hls_decrypt = ctx.protocol_lower == "hls" and any(
            str((seg.get("enc") or {}).get("method") or "NONE").upper() == "AES-128" for seg in ctx.dl_segs
        )
        stream_is_encrypted = ctx.stream.drm is not None and ctx.stream.drm.is_encrypted()
        ctx.needs_dash_live = ctx.protocol_lower == "dash" and live_decryption and bool(self.key) and stream_is_encrypted
        ctx.needs_ism_live = ctx.protocol_lower == "ism" and live_decryption and bool(self.key) and stream_is_encrypted

        # HLS's DRM state lives per-segment (#EXT-X-KEY METHOD), not on
        # stream.drm the way DASH/ISM's manifest-level DRM does -- check dl_segs
        # directly, same as needs_hls_decrypt (AES-128) already does.
        ctx.needs_hls_live = (
            ctx.protocol_lower == "hls"
            and live_decryption
            and bool(self.key)
            and any(str((seg.get("enc") or {}).get("method") or "NONE").upper().startswith("SAMPLE-AES") for seg in ctx.dl_segs)
        )

        # Unencrypted DASH/ISM: nothing to decrypt, but still worth routing
        # through the worker pool purely so _LiveMerger can order+append each
        # segment live instead of a separate merge pass at the end.
        ctx.needs_dash_clear_merge = (
            ctx.protocol_lower == "dash"
            and live_decryption
            and not stream_is_encrypted
        )
        ctx.needs_ism_clear_merge = (
            ctx.protocol_lower == "ism"
            and live_decryption
            and not stream_is_encrypted
            and not self.key
        )
        # Fully unencrypted HLS TS: no #EXT-X-KEY at all on any segment.
        ctx.needs_hls_clear_merge = (
            ctx.protocol_lower == "hls"
            and live_decryption
            and all(str((seg.get("enc") or {}).get("method") or "NONE").upper() == "NONE" for seg in ctx.dl_segs)
        )

    def _decrypt_worker(self, ctx: "_SegmentDownloadContext", live_decryption: bool) -> None:
        while True:
            item = ctx.decrypt_queue.get()
            if item is None:
                break
            try:
                if ctx.decrypt_aborted_reason is not None:
                    continue
                poisoned = self._kids_poisoned(self._stream_kids(ctx.stream))
                if poisoned is not None:
                    # Sibling (or this) track proved the key wrong -- stop
                    # burning decrypt cycles here; the abort reason below
                    # ends this track after the queue drains.
                    if ctx.decrypt_aborted_reason is None:
                        ctx.decrypt_aborted_reason = (
                            f"wrong key for KID {poisoned} "
                            "(confirmed on another track) -- terminating track"
                        )
                        self._skip_stream_wrong_key(ctx.stream, poisoned, ctx.bar_manager, ctx.task_key)
                    continue
                path_value = item.get("path")
                if not path_value:
                    continue
                fp = Path(path_value)
                if not fp.exists() or fp.stat().st_size <= 0:
                    continue
                seg = ctx.segment_meta_by_path.get(normalize_path_key(str(fp)))
                if not seg:
                    logger.debug(f"Segment completion without metadata match: {fp}")
                    continue

                _seg_method = str((seg.get("enc") or {}).get("method") or "NONE").upper()
                if item.get("skipped") and not (ctx.protocol_lower == "hls" and _seg_method == "AES-128"):
                    # A "skipped" event means this segment file already existed on
                    # disk (resume) -- it may be a leftover from a previous,
                    # interrupted run that never got decrypted. Only the HLS
                    # AES-128 path below can cheaply tell whether it was already
                    # decrypted (_reads_as_plaintext_ts), so it's safe to let those
                    # through; the DASH/ISM CENC and HLS SAMPLE-AES live-decrypt
                    # paths have no equivalent idempotency check yet, so keep
                    # skipping those to avoid corrupting an already-decrypted
                    # fragment.
                    continue

                if ctx.protocol_lower == "hls":
                    _hls_method = _seg_method
                    if _hls_method == "AES-128":
                        self._decrypt_hls_segment(ctx, fp, seg)
                        continue
                    if _hls_method == "NONE" and ctx.needs_hls_clear_merge:
                        # Fully unencrypted HLS TS: no decrypt, no init
                        # concept at all (every TS segment is a complete,
                        # independently concatenable unit via its own
                        # repeated PAT/PMT) -- just order+append it live.
                        if ctx.live_merger is not None:
                            ctx.live_merger.submit(seg.get("number"), fp)
                        continue

                    if not ctx.needs_hls_live:
                        # Unencrypted, or SAMPLE-AES without live support available
                        # (e.g. no keys resolved yet) -- leave for the batch
                        # whole-file decrypt after merge, same as before this
                        # branch existed.
                        continue

                    if seg.get("seg_type") != "init" and not _hls_method.startswith("SAMPLE-AES"):
                        # The dedicated init segment's own "enc" is always
                        # hardcoded to method=NONE (it isn't itself sample
                        # data) -- it still has to fall through below to get
                        # cached, or no media segment would ever find an
                        # init to decrypt against. Only filter out a genuine
                        # non-SAMPLE-AES *media* segment here.
                        continue

                    # SAMPLE-AES with live decrypt available falls through to the
                    # shared DASH/HLS CENC block below (EXT-X-MAP and
                    # self-initializing segments both handled there).

                if ctx.protocol_lower == "dash" and ctx.needs_dash_clear_merge:
                    # Unencrypted DASH (e.g. a subtitle track that has no
                    # ContentProtection of its own even though self.key is
                    # set for the video/audio tracks): nothing to decrypt,
                    # but still worth ordering+appending each segment into
                    # the final output live as it downloads instead of a
                    # separate merge pass afterward -- same _LiveMerger,
                    # just no decrypt step. Must be checked before the
                    # live-decrypt branch below: flux's --fragments-info
                    # only understands AVC/HEVC/AV1/VP9/AAC/Opus init
                    # segments, so routing an unencrypted text/TTML track
                    # there fails immediately with "unexpected box".
                    if ctx.live_merger is not None:
                        ctx.live_merger.submit(seg.get("number"), fp)

                elif (ctx.protocol_lower == "dash" and ctx.needs_dash_live) or (ctx.protocol_lower == "hls" and ctx.needs_hls_live):
                    if ctx.no_dedicated_init:
                        # No EXT-X-MAP (HLS) / no init seg_type at all (DASH
                        # SegmentList etc): every segment carries its own
                        # ftyp+moov, so there's no separate init to cache/wait for --
                        # init_path=None makes decrypt_segment_live fall through
                        # to flux's normal (non --fragments-info) decrypt, which
                        # reads the segment's own moov and -- being a full normal
                        # decrypt, not a --fragments-info one -- already produces
                        # clean (non-CENC-signaling) output on its own, same as
                        # the existing whole-file batch decrypt path does.
                        self._decrypt_dash_segment(ctx, fp, seg, ctx.dash_decryptor, None)

                    elif seg.get("seg_type") == "init":
                        flush: list[tuple] = []
                        cached_now = False
                        with ctx.dash_init_lock:
                            if ctx.dash_init is None:
                                ctx.dash_init = fp
                                cached_now = True
                                logger.debug(f"{ctx.protocol.upper()} init segment cached -> {fp.name}")
                                flush = ctx.dash_pending[:]
                                ctx.dash_pending.clear()

                        if cached_now:
                            # Unlike ISM's self-built init, this is the real init
                            # segment downloaded from the CDN -- it still carries
                            # genuine sinf/tenc CENC signaling that flux's
                            # --fragments-info leaves untouched (it only ever
                            # decrypts fragment sample data). Keep a protected copy
                            # for flux to keep reading, and rewrite fp itself (the
                            # one that ends up in the final merge via `paths`) to a
                            # clean, non-CENC-signaling copy -- otherwise players/probes
                            # would flag the merged output as "still encrypted" despite
                            # every fragment already being plaintext.

                            try:
                                protected_bytes = fp.read_bytes()

                                # Some CDNs answer the init-segment request with
                                # 200 OK + a small JS/JSON body embedding the real
                                # signed URL instead of an HTTP redirect.
                                redirect_url = repair_init_segment(protected_bytes)
                                if redirect_url:
                                    logger.debug(f"{ctx.protocol.upper()} init segment wasn't a real MP4, retrying via extracted URL: {redirect_url}")
                                    seg_headers = seg.get("headers") or {}
                                    byte_range = parse_range_header(seg_headers.get("Range"))

                                    if byte_range is not None:
                                        # Reuse the same byte range as the original
                                        # request -- and slice client-side if the
                                        # extracted URL's host ignores the Range
                                        # header, instead of silently accepting a
                                        # whole (possibly multi-GB) file as "the
                                        # init segment" just because it happens to
                                        # start with a valid ftyp+moov too.
                                        protected_bytes = self._fetch_exact_range(ctx, redirect_url, seg_headers, byte_range)
                                    else:
                                        req_headers = dict(ctx.all_headers)
                                        req_headers.update(seg_headers)
                                        with create_client(headers=req_headers, timeout=REQUEST_TIMEOUT, follow_redirects=True) as c:
                                            resp = c.get(redirect_url)
                                            resp.raise_for_status()
                                            protected_bytes = resp.content

                                    if not is_valid_frag_init(protected_bytes):
                                        raise RuntimeError(f"{ctx.protocol.upper()} init segment still not a valid ftyp+moov after following extracted redirect URL")
                                    fp.write_bytes(protected_bytes)
                                    logger.info(f"{ctx.protocol.upper()} init segment repaired via extracted redirect URL -> {fp.name}")

                                protected_copy = fp.with_name(f"_frag_init_protected{fp.suffix}")
                                protected_copy.write_bytes(protected_bytes)
                                with ctx.dash_init_lock:
                                    ctx.dash_init = protected_copy
                                fp.write_bytes(strip_cenc_signaling(protected_bytes))
                                logger.debug(f"{ctx.protocol.upper()} init CENC signaling stripped for merge -> {fp.name} (flux keeps reading {protected_copy.name})")
                            except Exception as exc:
                                logger.warning(f"{ctx.protocol.upper()} init CENC-signaling strip failed, keeping original (players may flag residual boxes): {exc}")

                            if ctx.live_merger is not None:
                                ctx.live_merger.submit(seg.get("number"), fp)
                        for pending_fp, pending_seg in flush:
                            self._decrypt_dash_segment(ctx, pending_fp, pending_seg, ctx.dash_decryptor, ctx.dash_init)
                    else:
                        with ctx.dash_init_lock:
                            init_path = ctx.dash_init
                            if init_path is None:
                                ctx.dash_pending.append((fp, seg))
                        if init_path is not None:
                            self._decrypt_dash_segment(ctx, fp, seg, ctx.dash_decryptor, init_path)

                elif ctx.protocol_lower == "ism" and ctx.needs_ism_clear_merge and not self.key:
                    # Clear ISM: still need the synthesized init (ISM
                    # fragments are never self-initializing, DRM or not),
                    # but no decrypt step -- just the sdi fix + live merge.
                    built_now = False
                    with ctx.ism_init_lock:
                        if ctx.ism_init is None:
                            ctx.ism_init = self._build_ism_init_from_fragment(ctx, fp)
                            built_now = True
                        _protected_init_path, clean_init_path = ctx.ism_init

                    if built_now and ctx.live_merger is not None:
                        ctx.live_merger.submit(-1, clean_init_path)

                    self._ism_clear_segment(ctx, fp, seg)

                elif ctx.protocol_lower == "ism" and live_decryption and self.key:
                    # No dedicated init segment exists on the wire -- the first
                    # fragment any worker thread happens to grab doubles as
                    # both the source of the synthesized init AND a normal
                    # fragment to decrypt (every fragment carries the same
                    # track_ID/kid/codec, so there's nothing to wait for).
                    built_now = False
                    with ctx.ism_init_lock:
                        if ctx.ism_init is None:
                            ctx.ism_init = self._build_ism_init_from_fragment(ctx, fp)
                            built_now = True
                        protected_init_path, clean_init_path = ctx.ism_init

                    if built_now and ctx.live_merger is not None:
                        ctx.live_merger.submit(-1, clean_init_path)

                    self._decrypt_ism_segment(ctx, fp, seg, ctx.ism_decryptor, protected_init_path)

            except Exception as exc:
                ctx.decrypt_errors.append(str(exc))
                exc_str = str(exc)
                is_permanent = "no content key for the track's default_kid" in exc_str.lower()
                if is_permanent and ctx.decrypt_aborted_reason is None:
                    ctx.decrypt_aborted_reason = exc_str
                    logger.error(f"Segment decrypt error ({ctx.protocol_lower}/{ctx.task_key}): {exc}")
                elif not is_permanent:
                    logger.error(f"Segment decrypt error ({ctx.protocol_lower}/{ctx.task_key}): {exc}")
                ctx.decrypt_queue.task_done()
