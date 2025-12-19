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
        _ _                     _____    _ 
 /\   /(_) |__  _ __ __ _/\   /\\_   \__| |
 \ \ / / | '_ \| '__/ _` \ \ / / / /\/ _` |
  \ V /| | |_) | | | (_| |\ V /\/ /_| (_| |
   \_/ |_|_.__/|_|  \__,_| \_/\____/ \__,_|
                                           
[red]+[cyan]=======================================================================================[red]+
    '''.rstrip()

    if CLEAN and clean: 
        os.system("cls" if platform.system() == 'Windows' else "clear")
    
    if SHOW:
        console.print(f"[purple]{msg}")