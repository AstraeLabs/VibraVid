# 3.12.23

import os
import platform


# External library
from rich.console import Console


# Internal utilities
from StreamingCommunity.Util.config_json import config_manager


# Variable
console = Console()
CLEAN = config_manager.get_bool('DEFAULT', 'show_message')
SHOW = config_manager.get_bool('DEFAULT', 'show_message')


def start_message(clean: bool = CLEAN):
    """Display a stylized start message in the console."""
    
    msg = r'''
     _    ___ __              _    ___     __
    | |  / (_) /_  _________ | |  / (_)___/ /
    | | / / / __ \/ ___/ __ `/ | / / / __  / 
    | |/ / / /_/ / /  / /_/ /| |/ / / /_/ /  
    |___/_/_.___/_/   \__,_/ |___/_/\__,_/   
                                         
    '''.rstrip()

    if CLEAN and clean: 
        os.system("cls" if platform.system() == 'Windows' else "clear")
    
    if SHOW:
        console.print(f"[purple]{msg}")