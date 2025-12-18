# 16.04.24

import os
import sys
import json
import subprocess
import logging
from typing import Tuple


# External library
from rich.console import Console


# Internal utilities
from StreamingCommunity.Util.os import get_ffprobe_path


# Variable
console = Console()


def get_video_duration(file_path: str, file_type: str = "file") -> float:
    """
    Get the duration of a media file (video or audio).

    Parameters:
        - file_path (str): The path to the media file.
        - file_type (str): The type of the file ('video' or 'audio'). Defaults to 'file'.

    Returns:
        (float): The duration of the media file in seconds if successful, None if there's an error.
    """
    try:
        ffprobe_cmd = [get_ffprobe_path(), '-v', 'error', '-show_format', '-print_format', 'json', file_path]
        logging.info(f"FFmpeg command: {ffprobe_cmd}")

        # Use a with statement to ensure the subprocess is cleaned up properly
        with subprocess.Popen(ffprobe_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) as proc:
            stdout, stderr = proc.communicate()

            if proc.returncode != 0:
                logging.error(f"Error: {stderr}")
                return None

            # Parse JSON output
            probe_result = json.loads(stdout)

            # Extract duration from the media information
            try:
                return float(probe_result['format']['duration'])

            except Exception:
                return 1

    except Exception as e:
        logging.error(f"Get {file_type} duration error: {e}, ffprobe path: {get_ffprobe_path()}, file path: {file_path}")
        sys.exit(0)


def format_duration(seconds: float) -> Tuple[int, int, int]:
    """
    Format duration in seconds into hours, minutes, and seconds.

    Parameters:
        - seconds (float): Duration in seconds.

    Returns:
        list[int, int, int]: List containing hours, minutes, and seconds.
    """
    hours, remainder = divmod(seconds, 3600)
    minutes, seconds = divmod(remainder, 60)

    return int(hours), int(minutes), int(seconds)


def print_duration_table(file_path: str, description: str = "Duration", return_string: bool = False):
    """
    Print the duration of a video file in hours, minutes, and seconds, or return it as a formatted string.

    Parameters:
        - file_path (str): The path to the video file.
        - description (str): Optional description to be included in the output. Defaults to "Duration". If not provided, the duration will not be printed.
        - return_string (bool): If True, returns the formatted duration string. If False, returns a dictionary with hours, minutes, and seconds.

    Returns:
        - str: The formatted duration string if return_string is True.
        - dict: A dictionary with keys 'h', 'm', 's' representing hours, minutes, and seconds if return_string is False.
    """
    video_duration = get_video_duration(file_path)

    if video_duration is not None:
        hours, minutes, seconds = format_duration(video_duration)
        formatted_duration = f"[yellow]{int(hours)}[red]h [yellow]{int(minutes)}[red]m [yellow]{int(seconds)}[red]s"
        duration_dict = {'h': hours, 'm': minutes, 's': seconds}

        if description:
            console.print(f"[cyan]{description} for [white]([green]{os.path.basename(file_path)}[white]): {formatted_duration}")
        else:
            if return_string:
                return formatted_duration
            else:
                return duration_dict


def get_ffprobe_info(file_path):
    """
    Get format and codec information for a media file using ffprobe.

    Parameters:
        - file_path (str): Path to the media file.
    
    Returns:
        dict: A dictionary containing the format name and a list of codec names.
              Returns None if file does not exist or ffprobe crashes.
    """
    if not os.path.exists(file_path):
        logging.error(f"File not found: {file_path}")
        return None

    try:
        cmd = [get_ffprobe_path(), '-v', 'error', '-show_format', '-show_streams', '-print_format', 'json', file_path]
        logging.info(f"Running FFprobe command: {' '.join(cmd)}")
        
        # Use subprocess.run instead of Popen for better error handling
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=False
        )

        if result.returncode != 0:
            logging.error(f"FFprobe failed with return code {result.returncode}")
            logging.error(f"FFprobe stderr: {result.stderr}")
            logging.error(f"FFprobe stdout: {result.stdout}")
            logging.error(f"Command: {' '.join(cmd)}")
            return None

        # Parse JSON output
        info = json.loads(result.stdout)
        return {
            'format_name': info.get('format', {}).get('format_name'),
            'codec_names': [stream.get('codec_name') for stream in info.get('streams', [])]
        }
    
    except Exception as e:
        logging.error(f"FFprobe execution failed: {e}")
        return None


def is_png_format_or_codec(file_info):
    """
    Check if the format is 'png_pipe' or if any codec is 'png'.

    Parameters:
        - file_info (dict): The dictionary containing file information.

    Returns:
        bool: True if the format is 'png_pipe' or any codec is 'png', otherwise False.
    """
    if not file_info:
        return False
    
    # Handle None values in format_name gracefully
    format_name = file_info.get('format_name')
    codec_names = file_info.get('codec_names', [])
    
    return format_name == 'png_pipe' or 'png' in codec_names


def need_to_force_to_ts(file_path):
    """
    Get if a file to TS format if it is in PNG format or contains a PNG codec.

    Parameters:
        - file_path (str): Path to the input media file.
    """
    file_info = get_ffprobe_info(file_path)

    if is_png_format_or_codec(file_info):
       logging.info(f"File {file_path} is in PNG format or contains a PNG codec. Need to convert to TS format.")
       return True
    
    return False


def check_duration_v_a(video_path, audio_path, tolerance=1.0):
    """
    Check if the duration of the video and audio matches.

    Parameters:
        - video_path (str): Path to the video file.
        - audio_path (str): Path to the audio file.
        - tolerance (float): Allowed tolerance for the duration difference (in seconds).

    Returns:
        - tuple: (bool, float, float, float) -> 
            - Bool: True if the duration of the video and audio matches within tolerance
            - Float: Difference in duration
            - Float: Video duration
            - Float: Audio duration
    """
    video_duration = get_video_duration(video_path, file_type="video")
    audio_duration = get_video_duration(audio_path, file_type="audio")

    # Check if either duration is None and specify which one is None
    if video_duration is None and audio_duration is None:
        console.print("[yellow]Warning: Both video and audio durations are None. Returning 0 as duration difference.")
        return False, 0.0, 0.0, 0.0
    
    elif video_duration is None:
        console.print("[yellow]Warning: Video duration is None. Using audio duration for calculation.")
        return False, 0.0, 0.0, audio_duration
    
    elif audio_duration is None:
        console.print("[yellow]Warning: Audio duration is None. Using video duration for calculation.")
        return False, 0.0, video_duration, 0.0
    
    # Calculate the duration difference
    duration_difference = abs(video_duration - audio_duration)

    # Check if the duration difference is within the tolerance
    if duration_difference <= tolerance:
        return True, duration_difference, video_duration, audio_duration
    else:
        return False, duration_difference, video_duration, audio_duration