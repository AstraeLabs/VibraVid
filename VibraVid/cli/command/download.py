# 10.12.25

import logging
import re
from pathlib import Path

from rich.console import Console

from VibraVid.core.downloader.util._detect import (
    DEFAULT_DOWNLOAD_DIR,
    derive_output_path,
    detect_stream_type,
    parse_headers,
    parse_keys,
    parse_raw_key,
)
from VibraVid.core.drm.system import DRMType
from VibraVid.core.ui.tracker import context_tracker
from VibraVid.setup import get_deno_path
from VibraVid.utils import config_manager
from VibraVid.utils.http_client import get_proxy_url

logger = logging.getLogger(__name__)
console = Console()


def _import_output_path(media, output: str | None) -> str:
    """Where an imported entry goes: the ``-o`` path, else the local naming of its title, else a name derived from the URL."""
    extension = config_manager.config.get("PROCESS", "extension")
    if output:
        return derive_output_path(media.url, output, extension)

    if media.title:
        from VibraVid.remote.cli import output_path_for

        return output_path_for(media, media.site)
    return derive_output_path(media.url, None, extension)


def handle_import(args) -> tuple[bool, bool]:
    """Download every entry of a ``--export`` JSON file when --import is passed."""
    path: str | None = getattr(args, "import_file", None)
    if not path:
        return False, False

    from VibraVid.core.downloader.util._export import read_export
    from VibraVid.remote.cli import _prepare_context
    from VibraVid.remote.factory import download_media
    from VibraVid.services._base.site_loader import current_site_var

    try:
        entries = read_export(path)
    except ValueError as exc:
        console.print(f"[red]--import: {exc}")
        return True, False

    output = (getattr(args, "output", None) or "").strip() or None
    if output and len(entries) > 1:
        console.print("[yellow]-o is ignored: the file has several entries, each one is named from its own title")
        output = None

    total = len(entries)
    console.print(f"[cyan]{total} entr{'y' if total == 1 else 'ies'} to download from {path}")

    failures = []
    for i, media in enumerate(entries, 1):
        name = media.title or media.url
        console.print(f"\n[cyan][{i}/{total}] {name} [dim]({media.kind})")
        site_token = current_site_var.set(media.site)
        try:
            _prepare_context(media, media.site)
            result = download_media(media, _import_output_path(media, output))
        except Exception as exc:
            logger.exception(f"Import of '{name}' failed: {exc}")
            console.print(f"[red]  failed: {exc}")
            failures.append(name)
            continue
        finally:
            current_site_var.reset(site_token)

        if not result.ok:
            console.print(f"[red]  failed: {result.error or 'stopped'}")
            failures.append(name)

    if failures:
        console.print(f"\n[yellow]{len(failures)}/{total} failed: {', '.join(failures)}")
    return True, not failures


def handle_direct_download(args) -> bool:
    """Execute a direct URL download when --down is passed."""
    url: str | None = getattr(args, "down", None)
    yt_dlp_url: str | None = getattr(args, "yt_dlp_url", None)
    
    # Handle --yt-dlp flag
    if yt_dlp_url:
        url = yt_dlp_url.strip()

    list_formats = bool(getattr(args, "list_formats", False))
    if list_formats and not url:
        return False, False

    if not url:
        return False, False

    url = url.strip()
    if getattr(args, "meta_title", None):
        context_tracker.title = args.meta_title
    if getattr(args, "meta_type", None):
        context_tracker.media_type = args.meta_type
    if getattr(args, "meta_season", None) is not None:
        context_tracker.season = args.meta_season
    if getattr(args, "meta_episode", None) is not None:
        context_tracker.episode = args.meta_episode
    if getattr(args, "meta_site", None):
        context_tracker.site_name = args.meta_site

    headers = parse_headers(getattr(args, "headers", None))
    keys = parse_keys(getattr(args, "key", None))
    output = (getattr(args, "output", None) or "").strip() or None
    lic_url = (getattr(args, "license_url", None) or "").strip() or None
    lic_hdr = parse_headers(getattr(args, "license_headers", None))
    drm_pref = (getattr(args, "drm", None) or "").strip().lower()
    max_segs = getattr(args, "max_segments", None)
    max_time = getattr(args, "max_time", None)
    skip_content_check = bool(getattr(args, "skip_content_check", False))
    skip_sanitize = bool(getattr(args, "skip_sanitize", False))

    hls_method = (getattr(args, "hls_method", None) or "").strip().upper() or None
    try:
        hls_key = parse_raw_key(getattr(args, "hls_key", None))
        hls_iv = parse_raw_key(getattr(args, "hls_iv", None))
    except Exception as exc:
        logger.error(f"Could not decode --hls-key/--hls-iv: {exc}")
        console.print(f"[red]Could not decode --hls-key/--hls-iv: {exc}")
        return True, False

    for _name, _val in (("--hls-key", hls_key), ("--hls-iv", hls_iv)):
        if _val is not None and len(_val) != 16:
            logger.error(f"{_name} must decode to exactly 16 bytes (got {len(_val)})")
            console.print(f"[red]{_name} must decode to exactly 16 bytes (got {len(_val)})")
            return True, False

    # Map DRM string to DRMType constant (or None if not recognized)
    drm_choice = None
    if drm_pref in ("widevine", "wv", DRMType.WIDEVINE):
        drm_choice = DRMType.WIDEVINE
    elif drm_pref in ("playready", "pr", DRMType.PLAYREADY):
        drm_choice = DRMType.PLAYREADY

    # Normalise key arg: single string → one-element list
    key_arg = keys

    # Allow forcing the stream type (e.g. --type mp4)
    forced_type = (getattr(args, "stream_type", None) or "auto").lower()
    
    # Check if --yt-dlp was used
    if getattr(args, "yt_dlp_url", None):
        url_type = "yt-dlp"
    else:
        url_type = forced_type if forced_type != "auto" else detect_stream_type(url)
        # Generic webpages and provider URLs have no media extension. Let
        # yt-dlp handle those URLs instead of rejecting them as unsupported.
        if url_type == "unsupported":
            url_type = "yt-dlp"

    # Keep yt-dlp's original title when no explicit output path was provided.
    if url_type != "yt-dlp":
        output = derive_output_path(url, output, config_manager.config.get("PROCESS", "extension"))

    # Lazy import to avoid circular dependency
    from VibraVid.core.downloader import DASH_Downloader, HLS_Downloader, ISM_Downloader, MP4_Downloader
    from VibraVid.core.downloader.yt_dlp_downloader import YTDLPDownloader

    try:
        if url_type == "mp4":
            path, cancelled, error = MP4_Downloader(
                url=url,
                path=output,
                headers=headers or None,
                key=key_arg,
                check_content_type=not skip_content_check,
                sanitize_path=not skip_sanitize,
            )

            if error:
                logger.error(f"MP4 download error: {error}")
                console.print(f"[red]Dio Cancaro: {error}")
                return True, False

        elif url_type == "hls":
            dl = HLS_Downloader(
                m3u8_url=url,
                headers=headers or None,
                license_url=lic_url,
                license_headers=lic_hdr or None,
                output_path=output,
                drm_preference=drm_choice or DRMType.WIDEVINE,
                key=key_arg,
                max_segments=max_segs,
                max_time=max_time,
                sanitize_path=not skip_sanitize,
                hls_method=hls_method,
                hls_key=hls_key,
                hls_iv=hls_iv,
            )
            path, cancelled, error = dl.start()

            if error:
                logger.error(f"HLS download error: {error}")
                console.print(f"[red]Dio Cancaro: {error}")
                return True, False

        elif url_type == "dash":
            effective_drm = drm_choice or DRMType.WIDEVINE
            dl = DASH_Downloader(
                mpd_url=url,
                mpd_headers=headers or None,
                license_url=lic_url,
                license_headers=lic_hdr or None,
                output_path=output,
                drm_preference=effective_drm,
                key=key_arg,
                max_segments=max_segs,
                max_time=max_time,
                sanitize_path=not skip_sanitize,
            )
            path, cancelled, error = dl.start()

            if error:
                logger.error(f"DASH download error: {error}")
                console.print(f"[red]Dio Cancaro: {error}")
                return True, False

        elif url_type == "ism":
            effective_drm = drm_choice or DRMType.PLAYREADY
            dl = ISM_Downloader(
                ism_url=url,
                headers=headers or None,
                license_url=lic_url,
                license_headers=lic_hdr or None,
                output_path=output,
                drm_preference=effective_drm,
                key=key_arg,
                max_segments=max_segs,
                max_time=max_time,
                sanitize_path=not skip_sanitize,
            )
            path, cancelled, error = dl.start()

            if error:
                logger.error(f"ISM download error: {error}")
                console.print(f"[red]Dio Cancaro: {error}")
                return True, False

        elif url_type == "yt-dlp":
            use_proxy = getattr(args, "use_proxy", False)
            proxy_url = get_proxy_url() if use_proxy else None
            list_formats = bool(getattr(args, "list_formats", False))
            interactive_format = bool(getattr(args, "interactive_format", False))

            if list_formats or interactive_format:
                dl = YTDLPDownloader(
                    url=url,
                    output_dir=str(Path(output).parent) if output else DEFAULT_DOWNLOAD_DIR,
                    filename=Path(output).stem if output else None,
                    headers=headers or None,
                    proxy=proxy_url,
                    format_spec=getattr(args, "format", None),
                    subtitle_langs=[t for t in re.split(r"[,|\s]+", getattr(args, "subtitle", None) or "") if t],
                    write_subs=bool(getattr(args, "write_subs", False)),
                    write_auto_subs=bool(getattr(args, "write_auto_subs", False)),
                    list_formats=list_formats,
                    interactive_format=interactive_format,
                    extract_audio=bool(getattr(args, "extract_audio", False)),
                    audio_format=getattr(args, "audio_format", None),
                    audio_quality=getattr(args, "audio_quality", None),
                    playlist_end=getattr(args, "playlist_end", None),
                    deno_path=get_deno_path(),
                )
                path, cancelled, error = dl.start()
                if error:
                    logger.error(f"yt-dlp format listing error: {error}")
                    console.print(f"[red]Format list error: {error}")
                    return True, False
                return True, True

            dl = YTDLPDownloader(
                url=url,
                output_dir=str(Path(output).parent) if output else DEFAULT_DOWNLOAD_DIR,
                filename=Path(output).stem if output else None,
                headers=headers or None,
                proxy=proxy_url,
                format_spec=getattr(args, "format", None),
                subtitle_langs=[t for t in re.split(r"[,|\s]+", getattr(args, "subtitle", None) or "") if t],
                write_subs=bool(getattr(args, "write_subs", False)),
                write_auto_subs=bool(getattr(args, "write_auto_subs", False)),
                list_formats=False,
                interactive_format=False,
                extract_audio=bool(getattr(args, "extract_audio", False)),
                audio_format=getattr(args, "audio_format", None),
                audio_quality=getattr(args, "audio_quality", None),
                playlist_end=getattr(args, "playlist_end", None),
                deno_path=get_deno_path(),
            )
            path, cancelled, error = dl.start()

            if error:
                logger.error(f"yt-dlp download error: {error}")
                console.print(f"[red]Dio Cancaro: {error}")
                return True, False

        elif url_type == "custom":
            from VibraVid.core.downloader import Generic_Downloader

            dl = Generic_Downloader(
                sources=[{"url": url, "protocol": "custom", "headers": headers or {}, "key": key_arg}],
                output_path=output,
                max_segments=max_segs,
                max_time=max_time,
            )
            path, cancelled, error = dl.start()

            if error:
                logger.error(f"Custom manifest download error: {error}")
                console.print(f"[red]Dio Cancaro: {error}")
                return True, False

        else:
            logger.error(f"Unsupported stream type for URL: {url}")
            console.print("[red]Unsupported: could not detect a valid stream (m3u8/dash/hls/ism/custom).")
            return True, False

    except Exception as exc:
        logger.exception(f"Direct download failed: {exc}")
        console.print(f"[red]Download error: {exc}")
        return True, False

    ok = True
    if cancelled:
        console.print("[yellow]Download cancelled.")
        ok = False
    elif path:
        logger.info(f"Download completed: {path}")
    else:
        console.print("[red]Download failed.")
        ok = False

    return True, ok
