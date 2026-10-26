# 3.12.23

import os
import platform

from rich.console import Console

from VibraVid.utils import config_manager

console = Console()
CLEAN = config_manager.config.get_bool("DEFAULT", "show_message")
SHOW = config_manager.config.get_bool("DEFAULT", "show_message")


def start_message(clean: bool = True):
    """Display a stylized start message in the console."""
    msg = r"""
[#7C3AED]   [#7C3AED] _    ___ __              _    ___     __
[#7C3AED]   [#7C3AED]| |  / (_) /_  _________ | |  / (_)___/ /
[#7C3AED]   [#7C3AED]| | / / / __ \/ ___/ __ `/ | / / / __  / 
[#7C3AED]   [#7C3AED]| |/ / / /_/ / /  / /_/ /| |/ / / /_/ /  
[#7C3AED]   [#7C3AED]|___/_/_.___/_/   \__,_/ |___/_/\__,_/      
"""

    if CLEAN and clean:
        os.system("cls" if platform.system() == "Windows" else "clear")

    if SHOW:
        console.print(f"[#7C3AED]{msg}")
