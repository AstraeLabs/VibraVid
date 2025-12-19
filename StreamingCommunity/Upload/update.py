# 01.03.2023

import os
import sys
import time
import asyncio
import importlib.metadata


# External library
import httpx
from rich.console import Console


# Internal utilities
from .version import __version__ as source_code_version, __author__, __title__
from StreamingCommunity.Util.config_json import config_manager
from StreamingCommunity.Util.http_client import get_userAgent


# Variable
if getattr(sys, 'frozen', False):  # Modalità PyInstaller
    base_path = os.path.join(sys._MEIPASS, "StreamingCommunity")
else:
    base_path = os.path.dirname(__file__)
console = Console()


async def fetch_github_data(client, url):
    """Helper function to fetch data from GitHub API"""
    response = await client.get(
        url=url,
        headers={'user-agent': get_userAgent()},
        timeout=config_manager.get_int("REQUESTS", "timeout"),
        follow_redirects=True
    )
    return response.json()

async def async_github_requests():
    """Make concurrent GitHub API requests"""
    async with httpx.AsyncClient() as client:
        tasks = [
            fetch_github_data(client, f"https://api.github.com/repos/{__author__}/{__title__}/releases"),
            fetch_github_data(client, f"https://api.github.com/repos/{__author__}/{__title__}/commits")
        ]
        return await asyncio.gather(*tasks)

def get_execution_mode():
    """Get the execution mode of the application"""
    if getattr(sys, 'frozen', False):
        return "installer"

    try:
        package_location = importlib.metadata.files(__title__)
        if any("site-packages" in str(path) for path in package_location):
            return "pip"
        
    except importlib.metadata.PackageNotFoundError:
        pass

    return "python"

def update():
    """Check for updates on GitHub and display relevant information."""
    try:
        # Run async requests concurrently
        response_releases, response_commits = asyncio.run(async_github_requests())
        
    except Exception as e:
        console.print(f"[red]Error accessing GitHub API: {e}")
        return

    # Calculate total download count from all releases
    total_download_count = sum(asset['download_count'] for release in response_releases for asset in release.get('assets', []))

    # Get latest version name
    if response_releases:
        last_version = response_releases[0].get('name', 'Unknown')
    else:
        last_version = 'Unknown'

    # Get the current version (installed version)
    try:
        current_version = importlib.metadata.version(__title__)
    except importlib.metadata.PackageNotFoundError:
        current_version = source_code_version

    # Get commit details
    latest_commit = response_commits[0] if response_commits else None
    latest_commit_message = latest_commit.get('commit', {}).get('message', 'No commit message')

    console.print(
        f"\n[red]{__title__} has been downloaded: [yellow]{total_download_count}"
        f"\n[yellow]{get_execution_mode()} - [green]Current installed version: [yellow]{current_version} "
        f"[green]last commit: [white]'[yellow]{latest_commit_message.splitlines()[0]}[white]'\n"
        f"  [cyan]Help the repository grow today by leaving a [yellow]star [cyan]and [yellow]sharing "
        f"[cyan]it with others online!\n"
        f"      [magenta]If you'd like to support development and keep the program updated, consider leaving a "
        f"[yellow]donation[magenta]. Thank you!"
    )

    if str(current_version).replace('v', '') != str(last_version).replace('v', ''):
        console.print(f"\n[cyan]New version available: [yellow]{last_version}")
    
    time.sleep(1)