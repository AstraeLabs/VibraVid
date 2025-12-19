# 31.01.24

import logging
import subprocess
from typing import List, Dict


# External library
from rich.console import Console


# Internal utilities
from StreamingCommunity.Util.config_json import config_manager
from StreamingCommunity.Util.os import get_ffmpeg_path


# Logic class
from .util import need_to_force_to_ts, check_duration_v_a
from .capture import capture_ffmpeg_real_time
from ..HLS.parser import M3U8_Codec


# Config
DEBUG_MODE = config_manager.get_bool("DEFAULT", "debug")
DEBUG_FFMPEG = "debug" if DEBUG_MODE else "error"
USE_GPU = config_manager.get_bool("M3U8_CONVERSION", "use_gpu")
PARAM_VIDEO = config_manager.get_list("M3U8_CONVERSION", "param_video")
PARAM_AUDIO = config_manager.get_list("M3U8_CONVERSION", "param_audio")
PARAM_FINAL = config_manager.get_list("M3U8_CONVERSION", "param_final")
PARAM_SUBTITLES = config_manager.get_list("M3U8_CONVERSION", "param_subtitles")


# Variable
console = Console()


def add_encoding_params(ffmpeg_cmd: List[str]):
    """
    Add encoding parameters to the ffmpeg command.
    
    Parameters:
        ffmpeg_cmd (List[str]): List of the FFmpeg command to modify
    """
    if PARAM_FINAL:
        ffmpeg_cmd.extend(PARAM_FINAL)
    else:
        ffmpeg_cmd.extend(PARAM_VIDEO)
        ffmpeg_cmd.extend(PARAM_AUDIO)


def join_video(video_path: str, out_path: str, codec: M3U8_Codec = None):
    """
    Joins single ts video file to mp4
    
    Parameters:
        - video_path (str): The path to the video file.
        - out_path (str): The path to save the output file.
        - codec (M3U8_Codec): The video codec to use (non utilizzato con nuova configurazione).
    """
    ffmpeg_cmd = [get_ffmpeg_path()]

    # Enabled the use of gpu
    if USE_GPU:
        ffmpeg_cmd.extend(['-hwaccel', 'cuda'])

    # Add mpegts to force to detect input file as ts file
    if need_to_force_to_ts(video_path):
        ffmpeg_cmd.extend(['-f', 'mpegts'])

    # Insert input video path
    ffmpeg_cmd.extend(['-i', video_path])

    # Add encoding parameters (prima dell'output)
    add_encoding_params(ffmpeg_cmd)

    # Output file and overwrite
    ffmpeg_cmd.extend([out_path, '-y'])
    logging.info(f"FFMPEG Command: {' '.join(ffmpeg_cmd)} \n")

    # Run join
    if DEBUG_MODE:
        subprocess.run(ffmpeg_cmd, check=True)
    else:
        capture_ffmpeg_real_time(ffmpeg_cmd, "[yellow]FFMPEG [cyan]Join video")
        print()

    return out_path


def join_audios(video_path: str, audio_tracks: List[Dict[str, str]], out_path: str, codec: M3U8_Codec = None, limit_duration_diff: float = 10.0):
    """
    Joins audio tracks with a video file using FFmpeg.
    
    Parameters:
        - video_path (str): The path to the video file.
        - audio_tracks (list[dict[str, str]]): A list of dictionaries containing information about audio tracks.
            Each dictionary should contain the 'path' and 'name' keys.
        - out_path (str): The path to save the output file.
        - codec (M3U8_Codec): The video codec to use (non utilizzato con nuova configurazione).
        - limit_duration_diff (float): Maximum duration difference in seconds.
    """
    use_shortest = False
    duration_diffs = []
    
    for audio_track in audio_tracks:
        audio_path = audio_track.get('path')
        audio_lang = audio_track.get('name', 'unknown')
        is_matched, diff, video_duration, audio_duration = check_duration_v_a(video_path, audio_path)
        
        duration_diffs.append({
            'language': audio_lang,
            'difference': diff,
            'has_error': diff > limit_duration_diff,
            'video_duration': video_duration,
            'audio_duration': audio_duration
        })
        
        # If any audio track has a significant duration difference, use -shortest
        if diff > limit_duration_diff:
            use_shortest = True

    # Print duration differences for each track
    if use_shortest:
        for track in duration_diffs:
            color = "red" if track['has_error'] else "green"
            console.print(f"[{color}]Audio {track['language']}: Video duration: {track['video_duration']:.2f}s, Audio duration: {track['audio_duration']:.2f}s, Difference: {track['difference']:.2f}s[/{color}] \n")

    # Start command with locate ffmpeg
    ffmpeg_cmd = [get_ffmpeg_path()]

    # Enabled the use of gpu
    if USE_GPU:
        ffmpeg_cmd.extend(['-hwaccel', 'cuda'])

    # Insert input video path
    ffmpeg_cmd.extend(['-i', video_path])

    # Add audio tracks as input
    for i, audio_track in enumerate(audio_tracks):
        ffmpeg_cmd.extend(['-i', audio_track.get('path')])

    # Map the video and audio streams
    ffmpeg_cmd.extend(['-map', '0:v'])
    
    for i in range(1, len(audio_tracks) + 1):
        ffmpeg_cmd.extend(['-map', f'{i}:a'])

    # Add encoding parameters (prima di -shortest e output)
    add_encoding_params(ffmpeg_cmd)

    # Use shortest input path if any audio track has significant difference
    if use_shortest:
        ffmpeg_cmd.extend(['-shortest', '-strict', 'experimental'])

    # Output file and overwrite
    ffmpeg_cmd.extend([out_path, '-y'])
    logging.info(f"FFMPEG Command: {' '.join(ffmpeg_cmd)} \n")

    # Run join
    if DEBUG_MODE:
        subprocess.run(ffmpeg_cmd, check=True)
    else:
        capture_ffmpeg_real_time(ffmpeg_cmd, "[yellow]FFMPEG [cyan]Join audio")
        print()

    return out_path, use_shortest


def join_subtitle(video_path: str, subtitles_list: List[Dict[str, str]], out_path: str):
    """
    Joins subtitles with a video file using FFmpeg.
    
    Parameters:
        - video (str): The path to the video file.
        - subtitles_list (list[dict[str, str]]): A list of dictionaries containing information about subtitles.
            Each dictionary should contain the 'path' key with the path to the subtitle file and the 'name' key with the name of the subtitle.
        - out_path (str): The path to save the output file.
    """
    ffmpeg_cmd = [get_ffmpeg_path(), "-i", video_path]

    # Add subtitle input files first
    for subtitle in subtitles_list:
        ffmpeg_cmd += ["-i", subtitle['path']]

    # Add maps for video and audio streams
    ffmpeg_cmd += ["-map", "0:v", "-map", "0:a"]

    # Add subtitle maps and metadata
    for idx, subtitle in enumerate(subtitles_list):
        ffmpeg_cmd += ["-map", f"{idx + 1}:s"]
        ffmpeg_cmd += ["-metadata:s:s:{}".format(idx), "title={}".format(subtitle['language'])]

    # For subtitles, we always use copy for video/audio and configured encoder for subtitles
    ffmpeg_cmd.extend(['-c:v', 'copy', '-c:a', 'copy'])
    
    # Add subtitle encoding parameters from config
    if PARAM_SUBTITLES:
        ffmpeg_cmd.extend(PARAM_SUBTITLES)

    # Overwrite
    ffmpeg_cmd += [out_path, "-y"]
    logging.info(f"FFMPEG Command: {' '.join(ffmpeg_cmd)} \n")

    # Run join
    if DEBUG_MODE:
        subprocess.run(ffmpeg_cmd, check=True)
    else:
        capture_ffmpeg_real_time(ffmpeg_cmd, "[yellow]FFMPEG [cyan]Join subtitle")
        print()

    return out_path