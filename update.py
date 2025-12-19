# 05.08.25

import os
import shutil
import argparse
from io import BytesIO
from zipfile import ZipFile
from datetime import datetime


# External library
import httpx
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.prompt import Prompt

from StreamingCommunity.Upload.version import __author__, __title__


# Variable
max_timeout = 15
console = Console()
local_path = os.path.join(".")
REQUIRED_PROJECT_FILES = {"update.py", "Video"}
PROJECT_MARKER_FILE = "update.py"


def verify_project_directory(directory: str) -> bool:
    """
    Verify that we're in the correct project directory by checking for required files.
    
    Parameters:
        - directory (str): The path to verify
        
    Returns:
        - bool: True if this appears to be the correct project directory
    """
    if not os.path.isdir(directory):
        console.print(f"[red]Error: '{directory}' is not a valid directory.")
        return False
    
    marker_path = os.path.join(directory, PROJECT_MARKER_FILE)
    if not os.path.exists(marker_path):
        console.print(f"[red]Safety check failed: '{PROJECT_MARKER_FILE}' not found in {os.path.abspath(directory)}")
        console.print("[yellow]Please ensure you're running this script from the project root directory.")
        return False
    
    return True


def dry_run_deletion(directory: str, keep_folder: str, keep_file: str) -> list:
    """
    Simulate what would be deleted without actually deleting anything.
    
    Parameters:
        - directory (str): The path to the directory
        - keep_folder (str): The name of the folder to keep
        - keep_file (str): The name of the file to keep
        
    Returns:
        - list: Items that would be deleted
    """
    if not os.path.exists(directory) or not os.path.isdir(directory):
        return []
    
    skip_folders = {keep_folder, ".git"}
    skip_files = {keep_file}
    items_to_delete = []
    
    for item in os.listdir(directory):
        if item in skip_folders or item in skip_files:
            continue
        
        item_path = os.path.join(directory, item)
        item_type = "directory" if os.path.isdir(item_path) else "file"
        items_to_delete.append((item, item_type, item_path))
    
    return items_to_delete


def move_content(source: str, destination: str):
    """
    Move all content from the source folder to the destination folder.

    Parameters:
        - source (str): The path to the source folder.
        - destination (str): The path to the destination folder.
    """
    os.makedirs(destination, exist_ok=True)

    # Iterate through all elements in the source folder
    for element in os.listdir(source):
        source_path = os.path.join(source, element)
        destination_path = os.path.join(destination, element)

        # If it's a directory, recursively call the function
        if os.path.isdir(source_path):
            move_content(source_path, destination_path)
        else:
            shutil.move(source_path, destination_path)


def keep_specific_items(directory: str, keep_folder: str, keep_file: str, dry_run: bool = False):
    """
    Deletes all items in the given directory except for the specified folder, the specified file, and the '.git' directory.

    Parameters:
        - directory (str): The path to the directory.
        - keep_folder (str): The name of the folder to keep.
        - keep_file (str): The name of the file to keep.
        - dry_run (bool): If True, only show what would be deleted without deleting.
    """
    if not verify_project_directory(directory):
        return False

    skip_folders = {keep_folder, ".git"}
    skip_files = {keep_file}

    for item in os.listdir(directory):
        if item in skip_folders or item in skip_files:
            continue

        item_path = os.path.join(directory, item)
        try:
            if dry_run:
                item_type = "directory" if os.path.isdir(item_path) else "file"
                console.log(f"[yellow][DRY RUN] Would remove {item_type}: {item_path}")

            else:
                if os.path.isdir(item_path):
                    shutil.rmtree(item_path)
                    console.log(f"[green]Removed directory: {item_path}")

                elif os.path.isfile(item_path):
                    os.remove(item_path)
                    console.log(f"[green]Removed file: {item_path}")

        except Exception as e:
            console.log(f"[yellow]Skipping {item_path} due to error: {e}")
    
    return True


def print_commit_info(commit_info: dict):
    """
    Print detailed information about the commit in a formatted table.
    
    Parameters:
        - commit_info (dict): The commit information from GitHub API
    """

    # Create a table for commit information
    table = Table(title=f"[bold green]Latest Commit Information - {__title__}", show_header=False)
    table.add_column("Field", style="cyan")
    table.add_column("Value", style="yellow")

    # Basic commit info
    commit = commit_info['commit']
    commit_date = datetime.strptime(commit['author']['date'], "%Y-%m-%dT%H:%M:%SZ")
    formatted_date = commit_date.strftime("%Y-%m-%d %H:%M:%S")

    # Add rows to the table
    table.add_row("Repository", f"{__author__}/{__title__}")
    table.add_row("Commit SHA", commit_info['sha'][:8])
    table.add_row("Author", f"{commit['author']['name']} <{commit['author']['email']}>")
    table.add_row("Date", formatted_date)
    table.add_row("Committer", f"{commit['committer']['name']} <{commit['committer']['email']}>")
    table.add_row("Message", commit['message'])
    
    # Add stats if available
    if 'stats' in commit_info:
        stats = commit_info['stats']
        table.add_row("Changes", f"+{stats['additions']} -[red]{stats['deletions']}[/red] ({stats['total']} total)")

    # Add URL info
    table.add_row("HTML URL", commit_info['html_url'])
    
    # Print the table in a panel
    console.print(Panel.fit(table))


def download_and_extract_latest_commit():
    """
    Download and extract the latest commit from a GitHub repository.
    """
    try:

        # Get the latest commit information using GitHub API
        api_url = f'https://api.github.com/repos/{__author__}/{__title__}/commits?per_page=1'
        console.log("[green]Requesting latest commit from GitHub repository...")
        
        headers = {
            'Accept': 'application/vnd.github.v3+json',
            'User-Agent': f'{__title__}-updater'
        }
        response = httpx.get(api_url, headers=headers, timeout=max_timeout, follow_redirects=True)

        if response.status_code == 200:
            commit_info = response.json()[0]
            commit_sha = commit_info['sha']
            
            # Print detailed commit information
            print_commit_info(commit_info)

            zipball_url = f'https://github.com/{__author__}/{__title__}/archive/{commit_sha}.zip'
            console.log("[green]Downloading latest commit zip file...")

            # Download the zipball
            response = httpx.get(zipball_url, follow_redirects=True, timeout=max_timeout)
            temp_path = os.path.join(os.path.dirname(os.getcwd()), 'temp_extracted')

            # Extract the content of the zipball into a temporary folder
            with ZipFile(BytesIO(response.content)) as zip_ref:
                zip_ref.extractall(temp_path)
            console.log("[green]Extracting files...")

            # Move files from the temporary folder to the current folder
            for item in os.listdir(temp_path):
                item_path = os.path.join(temp_path, item)
                destination_path = os.path.join(local_path, item)
                shutil.move(item_path, destination_path)

            # Remove the temporary folder
            shutil.rmtree(temp_path)

            # Move all folder to main folder
            new_folder_name = f"{__title__}-{commit_sha}"
            move_content(new_folder_name, ".")
            shutil.rmtree(new_folder_name)
            
            console.log("[cyan]Latest commit downloaded and extracted successfully.")
        else:
            console.log(f"[red]Failed to fetch commit information. Status code: {response.status_code}")

    except httpx.RequestError as e:
        console.print(f"[red]Request failed: {e}")

    except Exception as e:
        console.print(f"[red]An unexpected error occurred: {e}")


def main_upload(auto_confirm=None, dry_run=False):
    """
    Main function to update to the latest commit of a GitHub repository with safety checks.
    
    Parameters:
        - auto_confirm (str or None): 'y', 'n', or None for interactive prompt
        - dry_run (bool): If True, only show what would be deleted
    """
    
    # Verify we're in the correct project directory
    if not verify_project_directory("."):
        console.print("[red]Aborted: Cannot verify project directory. Refusing to proceed.")
        return
    
    current_dir = os.path.abspath(".")
    console.print(f"[cyan]Project directory: {current_dir}")
    
    # Show what will be deleted
    items_to_delete = dry_run_deletion(".", "Video", "upload.py")
    
    if items_to_delete:
        console.print("\n[bold yellow]Files and folders that will be deleted:")
        for item_name, item_type, item_path in items_to_delete:
            console.print(f"  [red]• {item_type}: {item_name}")
        console.print()
    
    # First confirmation: Basic yes/no
    if auto_confirm is None:
        cmd_insert = Prompt.ask(
            "[bold red]Are you sure you want to delete all files except 'Video' folder and 'upload.py'?",
            choices=['y', 'n'],
            default='n',
            show_choices=True
        )
        
    else:
        cmd_insert = auto_confirm

    if cmd_insert.lower().strip() not in ('y', 'yes'):
        console.print("[yellow]Operation cancelled.")
        return

    # Second confirmation: Require explicit phrase
    console.print("\n[bold red]WARNING: This action cannot be undone!")
    confirmation_phrase = "DELETE MY FILES"
    user_input = Prompt.ask(
        f"[bold red]Type '{confirmation_phrase}' to confirm deletion",
        default=""
    )
    
    if user_input.strip() != confirmation_phrase:
        console.print("[yellow]Confirmation phrase incorrect. Operation cancelled.")
        return
    
    # Perform dry run first
    if dry_run:
        console.print("\n[bold cyan]DRY RUN MODE - No files will be deleted")
        keep_specific_items(".", "Video", "upload.py", dry_run=True)
        return
    
    # Perform actual deletion
    console.print("[red]Deleting all files except 'Video' folder and 'upload.py'...")
    if keep_specific_items(".", "Video", "upload.py"):
        download_and_extract_latest_commit()
    else:
        console.print("[red]Deletion aborted due to safety check failure.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Safely update to the latest commit of a GitHub repository.",
        epilog="SAFETY: This script will only work from the project root directory."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be deleted without actually deleting anything."
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "-y", "--yes",
        action="store_true",
        help="Skip first confirmation (you'll still need to type the confirmation phrase)."
    )
    group.add_argument(
        "-n", "--no",
        action="store_true",
        help="Automatically cancel without prompting."
    )
    args = parser.parse_args()

    if args.no:
        main_upload("n")
    elif args.yes:
        main_upload("y", dry_run=args.dry_run)
    else:
        main_upload(dry_run=args.dry_run)