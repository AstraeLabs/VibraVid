# 16.04.24

import re
import time
import logging
import threading
import subprocess


# External library
from rich.console import Console


# Internal utilities
from StreamingCommunity.Util.os import internet_manager


# Variable
console = Console()
terminate_flag = threading.Event()


def capture_output(process: subprocess.Popen, description: str) -> None:
    """
    Function to capture and print output from a subprocess.

    Parameters:
        - process (subprocess.Popen): The subprocess whose output is captured.
        - description (str): Description of the command being executed.
    """
    try:
        max_length = 0
        start_time = time.time()

        for line in iter(process.stdout.readline, ''):          
            try:
                line = line.strip()
                logging.info(f"CAPTURE ffmpeg line: {line}")

                if not line:
                    continue

                # Check if termination is requested
                if terminate_flag.is_set():
                    break

                if "size=" in line:
                    try:
                        elapsed_time = time.time() - start_time
                        data = parse_output_line(line)

                        if 'q' in data:
                            is_end = (float(data.get('q', -1.0)) == -1.0)
                            size_key = 'Lsize' if is_end else 'size'
                            byte_size = int(re.findall(r'\d+', data.get(size_key, '0'))[0]) * 1000
                        else:
                            byte_size = int(re.findall(r'\d+', data.get('size', '0'))[0]) * 1000

                        # Extract additional information
                        fps = data.get('fps', 'N/A')
                        time_processed = data.get('time', 'N/A')
                        bitrate = data.get('bitrate', 'N/A')
                        speed = data.get('speed', 'N/A')

                        # Format elapsed time as HH:MM:SS
                        elapsed_formatted = format_time(elapsed_time)

                        # Construct the progress string with formatted output information
                        progress_string = (
                            f"{description}[white]: "
                            f"([green]'fps': [yellow]{fps}[white], "
                            f"[green]'speed': [yellow]{speed}[white], "
                            f"[green]'size': [yellow]{internet_manager.format_file_size(byte_size)}[white], "
                            f"[green]'time': [yellow]{time_processed}[white], "
                            f"[green]'bitrate': [yellow]{bitrate}[white], "
                            f"[green]'elapsed': [yellow]{elapsed_formatted}[white])"
                        )
                        max_length = max(max_length, len(progress_string))

                        # Print the progress string to the console, overwriting the previous line
                        console.print(progress_string.ljust(max_length), end="\r")

                    except Exception as e:
                        logging.error(f"Error parsing output line: {line} - {e}")

            except Exception as e:
                logging.error(f"Error processing line from subprocess: {e}")

    except Exception as e:
        logging.error(f"Error in capture_output: {e}")

    finally:
        try:
            terminate_process(process)
        except Exception as e:
            logging.error(f"Error terminating process: {e}")


def format_time(seconds: float) -> str:
    """
    Format seconds into HH:MM:SS format.

    Parameters:
        - seconds (float): Time in seconds.
    """
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def parse_output_line(line: str) -> dict:
    """
    Function to parse the output line and extract relevant information.

    Parameters:
        - line (str): The output line to parse.

    Returns:
        dict: A dictionary containing parsed information.
    """
    try:
        data = {}
        parts = line.replace("  ", "").replace("= ", "=").split()

        for part in parts:
            key_value = part.split('=')

            if len(key_value) == 2:
                key = key_value[0]
                value = key_value[1]

                # Remove milliseconds from time value
                if key == 'time' and isinstance(value, str) and '.' in value:
                    value = value.split('.')[0]
                data[key] = value

        return data
    
    except Exception as e:
        logging.error(f"Error parsing line: {line} - {e}")
        return {}


def terminate_process(process):
    """
    Function to terminate a subprocess if it's still running.

    Parameters:
        - process (subprocess.Popen): The subprocess to terminate.
    """
    try:
        if process.poll() is None:
            process.kill()
    except Exception as e:
        logging.error(f"Failed to terminate process: {e}")


def capture_ffmpeg_real_time(ffmpeg_command: list, description: str) -> None:
    """
    Function to capture real-time output from ffmpeg process.

    Parameters:
        - ffmpeg_command (list): The command to execute ffmpeg.
        - description (str): Description of the command being executed.
    """
    global terminate_flag

    # Clear the terminate_flag before starting a new capture
    terminate_flag.clear()

    try:

        # Start the ffmpeg process with subprocess.Popen
        process = subprocess.Popen(ffmpeg_command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True)

        # Start a thread to capture and print output
        output_thread = threading.Thread(target=capture_output, args=(process, description))
        output_thread.start()

        try:
            # Wait for ffmpeg process to complete
            process.wait()

        except KeyboardInterrupt:
            logging.error("Terminating ffmpeg process...")

        except Exception as e:
            logging.error(f"Error in ffmpeg process: {e}")
            
        finally:
            terminate_flag.set()
            output_thread.join()

    except Exception as e:
        logging.error(f"Failed to start ffmpeg process: {e}")