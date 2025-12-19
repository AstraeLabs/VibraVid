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


def start_message(clean: bool=True):
    """Display a stylized start message in the console."""
    msg = r'''
[red]+[cyan]=======================================================================================[red]+[purple]
     ___                                         ______                     _           
    / _ | ___________ _    _____ _____[yellow]  __ __[purple]   / __/ /________ ___ ___ _  (_)__  ___ _ 
   / __ |/ __/ __/ _ \ |/|/ / _ `/ __/[yellow]  \ \ /[purple]  _\ \/ __/ __/ -_) _ `/  ' \/ / _ \/ _ `/ 
  /_/ |_/_/ /_/  \___/__,__/\_,_/_/   [yellow] /_\_\ [purple] /___/\__/_/  \__/\_,_/_/_/_/_/_//_/\_, /  
                                                                                /___/   
[red]+[cyan]=======================================================================================[red]+
    '''.rstrip()

    if CLEAN and clean: 
        os.system("cls" if platform.system() == 'Windows' else "clear")
    
    if SHOW:
        console.print(f"[purple]{msg}")