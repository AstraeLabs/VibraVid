# 16.04.24

import logging
import os
from pathlib import Path

from VibraVid.core.muxing.capture import capture_ffmpeg_real_time
from VibraVid.setup import get_ffmpeg_path
from VibraVid.utils import config_manager

from .codec import _detect_output_ext, fix_container_mismatch
from .probe import get_video_duration
from .tagging import tag_track

logger = logging.getLogger(__name__)
ffmpeg_params = config_manager.config.get_list("PROCESS", "param_song", default=None)


def convert_audio(input_path: str, ffmpeg_params: list[str]) -> str | None:
    """
    Re-encode an audio file using custom FFmpeg parameters from config.
    """
    input_path_obj = Path(input_path)
    if not input_path_obj.exists():
        logger.error(f"Input file not found: {input_path}")
        return None

    ffmpeg_path = get_ffmpeg_path()
    if not ffmpeg_path:
        logger.error("FFmpeg not found")
        return None

    # Derive output extension from codec, fall back to original extension
    fallback_ext = input_path_obj.suffix.lstrip(".")
    output_ext = _detect_output_ext(ffmpeg_params, fallback_ext)
    output_path = input_path_obj.with_suffix(f".{output_ext}")

    # Avoid overwriting the source when the extension does not change
    if output_path == input_path_obj:
        output_path = input_path_obj.with_stem(input_path_obj.stem + "_converted")

    ffmpeg_cmd = [ffmpeg_path, "-i", str(input_path_obj)] + ffmpeg_params + ["-y", str(output_path)]
    logger.info(f"Running audio conversion: {' '.join(ffmpeg_cmd)}")

    # Get duration for progress tracking
    total_duration = get_video_duration(str(input_path_obj))
    capture_ffmpeg_real_time(ffmpeg_cmd, f"[cyan]Converting to {output_ext.upper()}", total_duration)

    if not output_path.exists():
        logger.error(f"Output file was not created: {output_path}")
        return None

    logger.info(f"Audio converted successfully: {output_path}")
    return str(output_path)


def process_song(
    file_path: str,
    title: str,
    artist: str,
    album: str = "",
    year: str = "",
    track_number: int | None = None,
    genre: str = "",
    cover_url: str | None = None,
    album_artist: str = "",
    lyrics: str | None = None,
) -> str:
    """
    Full post-download pipeline for a music file.

    Steps:
        1. Tag     → write metadata + cover art + lyrics via mutagen
        2. Fix     → remux to the codec's real container if the extension doesn't match
                     what's actually inside (e.g. FLAC packaged in .m4a)
        3. Convert → re-encode with FFmpeg if ffmpeg_params is set
        4. Cleanup → remove original file if conversion produced a new path

    Returns:
        - str: Final file path (converted path, or original if no conversion).
    """
    path = file_path

    # Step 1 — tag the downloaded file
    tag_track(
        file_path=path,
        title=title,
        artist=artist,
        album=album,
        year=year,
        track_number=track_number,
        genre=genre,
        cover_url=cover_url,
        album_artist=album_artist,
        lyrics=lyrics,
    )

    # Step 2
    fixed_path = fix_container_mismatch(path)
    if fixed_path != path:
        path = fixed_path
        tag_track(
            file_path=path,
            title=title,
            artist=artist,
            album=album,
            year=year,
            track_number=track_number,
            genre=genre,
            cover_url=cover_url,
            album_artist=album_artist,
            lyrics=lyrics,
        )

    # Step 3 — convert if the user configured ffmpeg params
    if not ffmpeg_params:
        return path

    output_ext = _detect_output_ext(ffmpeg_params, Path(path).suffix.lstrip("."))
    logger.info(f"Output extension for conversion: {output_ext}")

    converted = convert_audio(path, ffmpeg_params)
    if not converted:
        logger.warning("Audio conversion failed — keeping original file.")
        return path

    # Step 4 — remove original only after successful conversion
    if converted != path:
        try:
            os.remove(path)
            logger.info(f"Removed original file: {path}")
        except Exception as e:
            logger.warning(f"Could not remove original file: {e}")

        # Re-tag converted file: ffmpeg transfers text tags but not picture blocks (e.g. opus)
        tag_track(
            file_path=converted,
            title=title,
            artist=artist,
            album=album,
            year=year,
            track_number=track_number,
            genre=genre,
            cover_url=cover_url,
            album_artist=album_artist,
            lyrics=lyrics,
        )

    print("\n")
    return converted
