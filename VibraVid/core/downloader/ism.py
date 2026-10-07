# 03.05.26

import logging
import os
from collections.abc import Callable

from rich.console import Console

from VibraVid.core.drm.manager import DRMManager
from VibraVid.core.drm.system import DRMType
from VibraVid.core.manifest.ism import ISMParser
from VibraVid.core.muxing.helper.video.hybrid import split_other_tracks
from VibraVid.core.ui.tracker import context_tracker
from VibraVid.core.velora.util.formatting import (
    parse_max_segments as _parse_max_segments,
)
from VibraVid.core.velora.util.formatting import (
    parse_max_time as _parse_max_time,
)
from VibraVid.setup import get_prd_path, get_wvd_path, resolve_service_cdm_paths
from VibraVid.utils import config_manager
from VibraVid.utils.http_client import get_headers

from .base import BaseDownloader, DownloadResult
from .drm_keys import fetch_drm_keys

console = Console()
logger = logging.getLogger(__name__)

EXTENSION_OUTPUT = config_manager.config.get("PROCESS", "extension")
SKIP_DOWNLOAD = config_manager.config.get_bool("DOWNLOAD", "skip_download")


class ISM_Downloader(BaseDownloader):
    def __init__(
        self,
        ism_url: str | None = None,
        ism_content: str | None = None,
        headers: dict[str, str] | None = None,
        manifest_refresh_fn: Callable[[], str | None] | None = None,
        license_url: str | None = None,
        license_headers: dict[str, str] | None = None,
        license_certificate: str | None = None,
        license_data: dict | None = None,
        output_path: str | None = None,
        drm_preference=DRMType.PLAYREADY,
        key: str | None = None,
        cookies: dict[str, str] | None = None,
        max_segments: int | None = None,
        max_time=None,
        other_tracks: list | None = None,
        chapters: list | None = None,
        poster_url: str | None = None,
        sanitize_path: bool = True,
        display_selected_only: bool = False,
    ):
        """
        Parameters:
            - ism_url: URL to the ISM manifest (ending with .ism or .ism/manifest).
            - ism_content: Content of the ISM manifest already downloaded (string). If provided, skips the HTTP fetch.
            - headers: HTTP headers for fetching the ISM manifest.
            - manifest_refresh_fn: Optional function to call to refresh the manifest content during download. Should return the new manifest content as a string, or None to keep using the original.
            - license_url: URL of the license server for DRM key acquisition.
            - license_headers: HTTP headers for license requests (defaults to *headers*).
            - license_certificate: Widevine certificate (base64) for license challenge.
            - license_data: Extra JSON fields merged into the license POST body alongside the challenge (e.g. playbackEnvelope/sessionHandoffToken for services like Amazon).
            - output_path: Output file path. Default: "download.{EXTENSION_OUTPUT}".
            - key: Manual decryption key (hex format) if known.
            - cookies: HTTP cookies for authenticated requests.
            - max_segments: Maximum number of segments to download (for testing). Default: None (all).
            - max_time: Maximum content duration to download, e.g. "01:00:00" or 3600 seconds. Default: None (all).
            - chapters: Chapter markers to inject into the muxed output, e.g. [{"name": str, "seconds": int}]. Default: context_tracker.chapters.
            - poster_url: Poster/still image URL to embed in the muxed output. Default: context_tracker.poster_url.
            - display_selected_only: Show only the selected tracks in the stream table, so the table previews exactly what will be downloaded. Default: False.
        """
        self.ism_url = self._resolve_url(str(ism_url).strip()) if ism_url else None
        self.ism_content = ism_content
        self.headers = headers or get_headers()
        self.manifest_refresh_fn = manifest_refresh_fn

        self.license_url = str(license_url).strip() if license_url else None
        self.license_headers = license_headers or self.headers
        self.license_certificate = license_certificate
        self.license_data = license_data
        self.drm_preference = drm_preference

        self.key = key
        self.cookies = cookies or {}
        self.max_segments = _parse_max_segments(
            max_segments if max_segments is not None else context_tracker.max_segments
        )
        self.max_time = _parse_max_time(max_time if max_time is not None else context_tracker.max_time)
        self.other_tracks = other_tracks or []
        self.display_selected_only = display_selected_only
        self.chapters = chapters if chapters is not None else context_tracker.chapters
        self.poster_url = context_tracker.poster_url or poster_url or context_tracker.fallback_poster_url
        context_tracker.poster_url = self.poster_url
        logger.info(f"Initialized ISM_Downloader with URL: {self.ism_url}, License URL: {self.license_url}, DRM Pref: {self.drm_preference}, Max Segments: {self.max_segments}, Max Time: {self.max_time}")

        wvd_override, prd_override = resolve_service_cdm_paths(context_tracker.site_name)
        self.drm_manager = DRMManager(
            wvd_override or get_wvd_path(),
            prd_override or get_prd_path(),
            config_manager.config.get_dict("DRM", "widevine", default={}),
            config_manager.config.get_dict("DRM", "playready", default={}),
            config_manager.config.get_bool("DRM", "prefer_remote_cdm"),
        )

        super().__init__(output_path, "_ism_temp", sanitize_path=sanitize_path)

    def _collect_drm_from_streams(self, streams: list) -> dict[str, list[dict]]:
        """
        Read PSSH / PRO data directly from ``Stream.drm`` on selected streams.
        """
        result: dict[str, list[dict]] = {DRMType.WIDEVINE: [], DRMType.PLAYREADY: []}
        seen: dict[str, set] = {DRMType.WIDEVINE: set(), DRMType.PLAYREADY: set()}

        for s in streams:
            if not (getattr(s, "selected", False) or getattr(s, "dv_companion", False)):
                continue

            drm = getattr(s, "drm", None)
            if not (drm and drm.is_encrypted()):
                continue

            for dt in drm.get_all_drm_types():
                if dt not in result:
                    continue

                # Collect all KIDs for this stream
                kids: list[str] = []
                if hasattr(drm, "get_all_kids"):
                    kids = [k for k in drm.get_all_kids() if k and k != "N/A"]
                if not kids:
                    for attr in ("kid", "default_kid"):
                        val = getattr(drm, attr, None)
                        if val and val != "N/A":
                            kids = [val]
                            break
                if not kids:
                    kids = ["N/A"]

                pssh = drm.get_pssh_for(dt)

                if not pssh:
                    logger.warning("No PSSH found for this stream's DRM, skipping...")
                    continue

                for kid in kids:
                    dedup = (pssh, kid)
                    if dedup in seen[dt]:
                        continue

                    seen[dt].add(dedup)
                    result[dt].append(
                        {
                            "pssh": pssh,
                            "kid": kid,
                            "type": "Widevine" if dt == DRMType.WIDEVINE else "PlayReady",
                        }
                    )

        return result

    def _collect_drm_from_ism(self, raw_ism_path: str | None) -> dict[str, list[dict]]:
        """
        Fallback: run :class:`ISMParser` on the saved raw manifest to find
        PSSH / PRO data.  If *raw_ism_path* is ``None`` or missing the parser
        re-fetches from the network.  Returns ``{}`` gracefully on any error.
        """
        result: dict[str, list[dict]] = {DRMType.WIDEVINE: [], DRMType.PLAYREADY: []}
        try:
            content = None
            if raw_ism_path and os.path.exists(raw_ism_path):
                with open(raw_ism_path, encoding="utf-8") as f:
                    content = f.read()

            parser = ISMParser(self.ism_url, self.headers, content=content)
            if not parser.fetch_manifest():
                return result

            drm_info = parser.get_drm_info()
            for entry in drm_info.get("widevine", []):
                result[DRMType.WIDEVINE].append(
                    {
                        "pssh": entry["pssh"],
                        "kid": entry.get("kid", "N/A"),
                        "type": "Widevine",
                    }
                )

            for entry in drm_info.get("playready", []):
                result[DRMType.PLAYREADY].append(
                    {
                        "pssh": entry["pssh"],
                        "kid": entry.get("kid", "N/A"),
                        "type": "PlayReady",
                    }
                )

        except Exception as exc:
            logger.error(f"_collect_drm_from_ism error: {exc}")

        return result

    def _fetch_keys(self, drm_psshs: dict[str, list[dict]]) -> list[str]:
        """
        Dispatch key-fetch to :class:`DRMManager`.

        When the manifest only carries the *other* DRM type, fall back to that type instead of skipping key resolution entirely.
        ``license_request_fn`` is intentionally not forwarded (historical ISM behavior, pinned by Test/Manifest/test_fetch_keys_dispatch.py).
        """
        return fetch_drm_keys(
            self.drm_manager,
            drm_psshs,
            preference=self.drm_preference,
            license_url=self.license_url,
            license_headers=self.license_headers,
            key=self.key,
            license_data=self.license_data,
            license_certificate=self.license_certificate,
            cross_drm_fallback=True,
            swallow_errors=True,
        )

    def start(self) -> DownloadResult:
        """Execute the full ISM download pipeline."""
        precheck = self._precheck(self.ism_url)
        if precheck is not None:
            return precheck

        self.media_downloader = self._new_media_downloader(self.ism_url, self.headers, self.ism_content, "ism")
        self.media_downloader.other_tracks = self.other_tracks
        self.media_downloader.display_selected_only = self.display_selected_only

        # Inject external subtitles that were passed via other_tracks
        _, _, other_subtitles = split_other_tracks(self.other_tracks)
        if other_subtitles:
            self.media_downloader.external_subtitles = other_subtitles

        if self.chapters:
            logger.info(f"Adding {len(self.chapters)} external chapter(s).")

        # ── Parse ─────────────────────────────────────────────────────────────
        self._update_status("Parsing ISM ...")

        streams = self.media_downloader.parse_stream(show_table=context_tracker.should_print and not context_tracker.hide_manifest_info)

        no_match = self._no_match_result()
        if no_match is not None:
            return no_match

        # ── DRM key fetch ─────────────────────────────────────────────────────
        raw_ism = (
            str(self.media_downloader.raw_ism)
            if hasattr(self.media_downloader, "raw_ism") and self.media_downloader.raw_ism
            else None
        )

        # Primary: PSSH / PRO from Stream.drm (populated by ISMParser)
        drm_psshs = self._collect_drm_from_streams(streams)

        # Fallback: re-scan raw manifest via ISMParser
        if not drm_psshs[DRMType.WIDEVINE] and not drm_psshs[DRMType.PLAYREADY]:
            logger.info("No PSSH in Stream objects — falling back to ISMParser")
            drm_psshs = self._collect_drm_from_ism(raw_ism)

        is_protected = bool(drm_psshs.get(DRMType.WIDEVINE) or drm_psshs.get(DRMType.PLAYREADY))

        if is_protected:
            self._update_status("Fetching keys ...")
            keys = self._fetch_keys(drm_psshs)

            if keys:
                self.media_downloader.set_key(keys)
            else:
                console.print("[red]Warning: DRM detected but no decryption keys found")
        else:
            keys = []

        # ── Download ──────────────────────────────────────────────────────────
        self._log_tracks_json(streams, keys, self.ism_url)

        if SKIP_DOWNLOAD:
            return self._skip_download_result()

        self._create_media_players()

        self._update_status("Downloading ...")
        print()

        self._maybe_enable_streaming_mux()
        status = self.media_downloader.start_download()

        # ── Guards → merge → finalize (shared tail)
        return self._finish_from_status(status)
