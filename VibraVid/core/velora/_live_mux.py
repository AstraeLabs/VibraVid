# 30.10.26

import logging
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from VibraVid.core.decryptor._models import _run_flux_dump
from VibraVid.core.muxing.streaming_mux import NamedPipeMuxer
from VibraVid.core.utils.language import track_title_and_iso_lang
from VibraVid.core.utils.resolution import classify_resolution
from VibraVid.core.utils.track_order import effective_select_filter, order_tracks
from VibraVid.setup import get_ffmpeg_path
from VibraVid.utils import config_manager

from .util._tfdt_rebase import rebase_fragment_bytes

logger = logging.getLogger("manual")
RENDEZVOUS_TIMEOUT_SECONDS = 1800.0
FINISH_TIMEOUT_SECONDS = 600.0
_FLUX_TYPE = {"video": "video", "audio": "audio"}


class LiveMux:
    def __init__(self, owner: Any, tracks: list, output_path: str):
        self._owner = owner
        self._tracks = tracks
        self._output_path = output_path
        self._muxer = NamedPipeMuxer(n_pipes=len(tracks))
        self._lock = threading.Lock()
        self._ready: dict[int, Path] = {}
        self._left: set[int] = set()
        self._all_ready = threading.Event()
        self._aborted = threading.Event()
        self._done = threading.Event()
        self.result: str | None = None
        self._muxer.prepare()

    @classmethod
    def create(cls, owner: Any) -> "LiveMux | None":
        """The live mux of *owner* (a MediaDownloader about to record live DASH tracks), or None when this download does not qualify."""
        def no(why: str) -> None:
            logger.info(f"live_mux: not used ({why}) -- tracks are muxed after the download")

        output_path = getattr(owner, "_streaming_mux_output_path", None)
        if not output_path or not str(output_path).lower().endswith(".mkv"):
            return no("streaming mux was not enabled for an .mkv output")
        
        if config_manager.config.get("PROCESS", "engine", default="ffmpeg").lower() != "ffmpeg":
            return no("PROCESS.engine is not ffmpeg")
        
        if getattr(owner, "other_tracks", None) or getattr(owner, "external_subtitles", None) or getattr(owner, "external_audios", None):
            return no("extra tracks beyond the manifest ones")

        selected = [s for s in getattr(owner, "streams", None) or [] if getattr(s, "selected", False) and not getattr(s, "is_external", False)]
        media = [s for s in selected if s.type in _FLUX_TYPE]
        if len(media) != len(selected) or not any(s.type == "video" for s in media):
            return no("it needs video and audio tracks only")
        
        for stream in media:
            if not getattr(stream, "is_live", False):
                return no("not every track is live")
            
            encrypted = getattr(getattr(stream, "drm", None), "method", None) is not None
            if encrypted and not (owner._session_live_decrypt and getattr(stream, "supports_live_decryption", True) and owner.key):
                return no("an encrypted track is not live-decrypted")

        try:
            ffmpeg_path = get_ffmpeg_path()
        except Exception:
            ffmpeg_path = None
        if not ffmpeg_path:
            return no("ffmpeg not found")

        video = next(s for s in media if s.type == "video")
        audios = order_tracks(
            [s for s in media if s.type == "audio"], "audio", effective_select_filter("audio", getattr(owner, "custom_filters", None))
        )
        return cls(owner, [video, *audios], str(output_path))

    def _index(self, stream: Any) -> int | None:
        return next((i for i, track in enumerate(self._tracks) if track is stream), None)

    def is_active(self) -> bool:
        return not self._aborted.is_set()

    def join(self, stream: Any, init_path: Path | None, stop_check: Callable[[], bool], rebased: bool = False) -> Callable[[bytes], None] | None:
        """Called by a track thread once its first segment is ready: returns the function that pipes one decrypted segment of *stream*, or None when the live mux is off for this download. Blocks until every track has reached this point (ffmpeg starts then). *rebased*: the segments already start at zero (flux did it while decrypting), so they are piped as they are."""
        index = self._index(stream)
        if index is None or self._aborted.is_set():
            return None
        if init_path is None or not self._init_matches(stream, init_path):
            self.abort(f"init segment of {stream.type} {stream.id!r} is not valid")
            return None

        with self._lock:
            self._ready[index] = init_path
            if len(self._ready) == len(self._tracks):
                self._start()
        if not self._wait_all_ready(stop_check):
            return None

        write = self._muxer.get_writer(index)
        base: list[int | None] = [None]
        write(init_path.read_bytes())

        def send(data: bytes) -> None:
            if rebased:
                write(data)
                return
            patched, base[0] = rebase_fragment_bytes(data, base[0])
            write(patched)

        return send

    def _init_matches(self, stream: Any, init_path: Path) -> bool:
        """The init segment is a real fMP4 init (flux can read it) with the track type the manifest announced."""
        report = _run_flux_dump(str(init_path))
        streams = (report or {}).get("streams") or []
        return any((s.get("stream_type") or "").lower() == _FLUX_TYPE[stream.type] for s in streams)

    def _wait_all_ready(self, stop_check: Callable[[], bool]) -> bool:
        deadline = time.monotonic() + RENDEZVOUS_TIMEOUT_SECONDS
        while not self._all_ready.wait(timeout=1.0):
            if self._aborted.is_set() or stop_check():
                return False
            if time.monotonic() >= deadline:
                self.abort("the tracks did not all have data in time")
                return False
        return not self._aborted.is_set()

    def _start(self) -> None:
        try:
            self._muxer.start_ffmpeg(self._build_cmd())
        except Exception as exc:
            self.abort(f"ffmpeg did not start: {exc}")
            return
        logger.info(f"live_mux: started, {len(self._tracks)} track(s) piped to ffmpeg -> {self._output_path}")
        self._all_ready.set()

    def _build_cmd(self) -> list[str]:
        # Not at module level: merge.py imports back into velora.
        from VibraVid.core.muxing.merge import video_track_name

        video, audios = self._tracks[0], self._tracks[1:]
        pipes = self._muxer.pipe_paths
        cmd = [get_ffmpeg_path(), "-y", "-hide_banner", "-loglevel", "warning", "-nostats"]
        for pipe in pipes:
            cmd += ["-thread_queue_size", "1024", "-f", "mp4", "-i", pipe]

        cmd += ["-map", "0:v:0"]
        quality = classify_resolution(getattr(video, "width", 0), getattr(video, "height", 0))
        if quality:
            cmd += ["-metadata:s:v:0", f"title={video_track_name('', quality=quality)}"]
        
        if any(tag in (getattr(video, "codecs", "") or "").lower() for tag in ("hev", "hvc", "dvh", "dvhe")):
            cmd += ["-tag:v", "hvc1"]

        for i, audio in enumerate(audios):
            title, lang = track_title_and_iso_lang(audio)
            cmd += ["-map", f"{i + 1}:a:0"]
            cmd += [f"-metadata:s:a:{i}", f"title={title}", f"-metadata:s:a:{i}", f"language={lang}", f"-metadata:s:a:{i}", f"handler_name={title}"]
            cmd += [f"-disposition:a:{i}", "default" if i == 0 else "0"]

        return cmd + ["-c", "copy", self._output_path]

    def leave(self, stream: Any) -> None:
        """Called by a track thread when it stops recording (also when it never joined): the last one out closes the pipes and collects ffmpeg's result."""
        index = self._index(stream)
        if index is None:
            return
        with self._lock:
            if index in self._left:
                return
            self._left.add(index)
            joined = index in self._ready
            everyone_left = len(self._left) == len(self._tracks)
        
        if not joined and not self._aborted.is_set():
            self.abort(f"{stream.type} {stream.id!r} never had data")
        
        if self._all_ready.is_set() and not self._aborted.is_set():
            self._muxer.close_writer(index)
        
        if everyone_left:
            self._finish()
        else:
            self._done.wait(timeout=FINISH_TIMEOUT_SECONDS)

    def _finish(self) -> None:
        try:
            self._finish_inner()
        finally:
            self._done.set()

    def _finish_inner(self) -> None:
        if self._aborted.is_set() or not self._all_ready.is_set():
            self._muxer.abort()
            return
        outcome = self._muxer.finish()
        if outcome.ok and Path(self._output_path).exists() and Path(self._output_path).stat().st_size > 0:
            self.result = self._output_path
            logger.info(f"live_mux: done -> {self._output_path}")
        else:
            logger.warning(f"live_mux: ffmpeg failed ({outcome.error or outcome.returncode}) -- tracks are muxed after the download. {outcome.stderr_tail[-400:]}")

    def abort(self, why: str) -> None:
        if self._aborted.is_set():
            return
        logger.info(f"live_mux: given up ({why}) -- tracks are muxed after the download")
        self._aborted.set()
        self._muxer.abort()
