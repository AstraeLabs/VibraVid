# 25.07.25

import os
import time
import subprocess
import logging
import threading


# External libraries
from rich.console import Console
from tqdm import tqdm


# Internal utilities
from StreamingCommunity.Util import config_manager, Colors


# Variable
console = Console()
extension_output = config_manager.get("M3U8_CONVERSION", "extension")
CLEANUP_TMP = config_manager.get_bool('M3U8_DOWNLOAD', 'cleanup_tmp_folder')
SHOW_DECRYPT_PROGRESS = True


def decrypt_with_mp4decrypt(type, encrypted_path, kid, key, output_path=None):
    """
    Decrypt an mp4/m4s file using mp4decrypt.

    Args:
        type (str): Type of file ('video' or 'audio').
        encrypted_path (str): Path to encrypted file.
        kid (str): Hexadecimal KID.
        key (str): Hexadecimal key.
        output_path (str): Output decrypted file path (optional).
        cleanup (bool): If True, remove temporary files after decryption.

    Returns:
        str: Path to decrypted file, or None if error.
    """
    from StreamingCommunity.Util.os import get_mp4decrypt_path

    # Check if input file exists
    if not os.path.isfile(encrypted_path):
        console.print(f"[red] Encrypted file not found: {encrypted_path}")
        return None

    # Check if kid and key are valid hex
    try:
        bytes.fromhex(kid)
        bytes.fromhex(key)
    except Exception:
        console.print("[red] Invalid KID or KEY (not hex).")
        return None

    if not output_path:
        output_path = os.path.splitext(encrypted_path)[0] + f"_decrypted.{extension_output}"

    # Get file size for progress tracking
    file_size = os.path.getsize(encrypted_path)
    
    key_format = f"{kid.lower()}:{key.lower()}"
    cmd = [get_mp4decrypt_path(), "--key", key_format, encrypted_path, output_path]
    logging.info(f"Running mp4decrypt command: {' '.join(cmd)}")

    progress_bar = None
    monitor_thread = None
    
    if SHOW_DECRYPT_PROGRESS:
        bar_format = (
            f"{Colors.YELLOW}DECRYPT{Colors.CYAN} {type}{Colors.WHITE}: "
            f"{Colors.MAGENTA}{{bar:40}} "
            f"{Colors.LIGHT_GREEN}{{n_fmt}}{Colors.WHITE}/{Colors.CYAN}{{total_fmt}} "
            f"{Colors.DARK_GRAY}[{Colors.YELLOW}{{elapsed}}{Colors.WHITE} < {Colors.CYAN}{{remaining}}{Colors.DARK_GRAY}] "
            f"{Colors.WHITE}{{postfix}}"
        )
        
        progress_bar = tqdm(
            total=100,
            bar_format=bar_format,
            unit="",
            ncols=150
        )
        
        def monitor_output_file():
            """Monitor output file growth and update progress bar."""
            last_size = 0
            while True:
                if os.path.exists(output_path):
                    current_size = os.path.getsize(output_path)
                    if current_size > 0:
                        progress_percent = min(int((current_size / file_size) * 100), 100)
                        progress_bar.n = progress_percent
                        progress_bar.refresh()
                        
                        if current_size == last_size and current_size > 0:
                            break
                        
                        last_size = current_size
                
                time.sleep(0.1)
        
        # Start monitoring thread
        monitor_thread = threading.Thread(target=monitor_output_file, daemon=True)
        monitor_thread.start()

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    except Exception as e:
        if progress_bar:
            progress_bar.close()
        console.print(f"[red] mp4decrypt execution failed: {e}")
        return None
    
    if progress_bar:
        progress_bar.n = 100
        progress_bar.refresh()
        progress_bar.close()

    if result.returncode == 0 and os.path.exists(output_path):

        # Cleanup temporary files
        if CLEANUP_TMP:
            if os.path.exists(encrypted_path):
                os.remove(encrypted_path)

            temp_dec = os.path.splitext(encrypted_path)[0] + f"_decrypted.{extension_output}"

            # Do not delete the final output!
            if temp_dec != output_path and os.path.exists(temp_dec):
                os.remove(temp_dec)

        # Check if output file is not empty
        if os.path.getsize(output_path) == 0:
            console.print(f"[red] Decrypted file is empty: {output_path}")
            return None

        return output_path

    else:
        console.print(f"[red] mp4decrypt failed: {result.stderr}")
        return None