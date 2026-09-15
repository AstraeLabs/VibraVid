# 17.03.25

import logging
import time

from rich.console import Console
from rich.prompt import Prompt

from VibraVid.services._base import load_search_functions
from VibraVid.utils.console.message import start_message
from VibraVid.utils.console.table import TVShowManager

console = Console()
msg = Prompt()
logger = logging.getLogger(__name__)


def global_search(search_terms: str = None, selected_sites: list = None):
    """
    Perform a search across multiple sites based on selection.

    Parameters:
        search_terms (str, optional): The terms to search for. If None, will prompt the user.
        selected_sites (list, optional): List of site aliases to search. If None, will search all sites.

    Returns:
        dict: Consolidated search results from all searched sites.
    """
    search_functions = {
        alias: func for alias, func in load_search_functions().items() if not getattr(func, "hide", False)
    }
    all_results = {}

    if search_terms is None:
        search_terms = msg.ask("\n[purple]Enter search terms for global search").strip()

    # Organize sites by category for better display
    sites_by_category = {}
    for alias, (func, category) in search_functions.items():
        if category not in sites_by_category:
            sites_by_category[category] = []
        sites_by_category[category].append((alias, func))

    # If no sites are specifically selected, prompt the user
    if selected_sites is None:
        console.print("\n[green]Select sites to search:")
        console.print("[cyan]1. Search all sites")
        console.print("[cyan]2. Search by category")
        console.print("[cyan]3. Select specific sites")

        choice = msg.ask("[green]Enter your choice (1-3)", choices=["1", "2", "3"], default="1")

        if choice == "1":
            # Search all sites
            selected_sites = list(search_functions.keys())

        elif choice == "2":
            # Search by category
            console.print("\n[green]Select categories to search:")
            for i, category in enumerate(sites_by_category.keys(), 1):
                console.print(f"[cyan]{i}. {category.capitalize()}")

            category_choices = msg.ask("[green]Enter category numbers separated by commas", default="1")
            selected_categories = [
                list(sites_by_category.keys())[int(c.strip()) - 1] for c in category_choices.split(",")
            ]

            selected_sites = []
            for category in selected_categories:
                for alias, _ in sites_by_category.get(category, []):
                    selected_sites.append(alias)

        else:
            # Select specific sites
            console.print("\n[green]Select specific sites to search:")

            for i, (alias, _) in enumerate(search_functions.items(), 1):
                site_name = alias.split("_")[0].capitalize()
                console.print(f"[cyan]{i}.{site_name}")

            site_choices = msg.ask("[green]Enter site numbers separated by commas", default="1")
            selected_indices = [int(c.strip()) - 1 for c in site_choices.split(",")]
            selected_sites = [list(search_functions.keys())[i] for i in selected_indices if i < len(search_functions)]

    # Display progress information
    console.print(f"\n[green]Searching for: [yellow]{search_terms}")
    console.print(f"[green]Searching across: {len(selected_sites)} sites \n")

    # Search each selected site
    for alias in selected_sites:
        site_name = alias.split("_")[0].capitalize()
        console.print(f"[cyan]Search url in: {site_name}")

        func, _ = search_functions[alias]
        try:
            # Call the search function with get_onlyDatabase=True to get database object
            database = func(search_terms, get_onlyDatabase=True)

            # Check if database has media_list attribute and it's not empty
            if database and hasattr(database, "media_list") and len(database.media_list) > 0:
                # Store media_list items with additional source information
                all_results[alias] = []
                for element in database.media_list:
                    # Convert element to dictionary if it's an object
                    if hasattr(element, "__dict__"):
                        item_dict = element.__dict__.copy()
                    else:
                        item_dict = {}  # Fallback for non-object items

                    # Add source information
                    item_dict["source"] = site_name
                    item_dict["source_alias"] = alias
                    all_results[alias].append(item_dict)

                console.print(f"[green]Found result: {len(database.media_list)}\n")

        except Exception as e:
            console.print(f"[red]Error searching {site_name}: {str(e)}")

    # Display the consolidated results
    if all_results:
        all_media_items = []
        for _alias, results in all_results.items():
            for item in results:
                all_media_items.append(item)

        # Display consolidated results
        manager = display_consolidated_results(all_media_items, search_terms)

        # Allow user to select an item via manager
        selected_item = select_from_consolidated_results(all_media_items, manager)
        if selected_item:
            # Process the selected item - download or further actions
            process_selected_item(selected_item, search_functions)

    else:
        console.print(f"\n[red]No results found for: [yellow]{search_terms}")

        # Optionally offer to search again or return to main menu
        if msg.ask("[green]Search again? (y/n)", choices=["y", "n"], default="y") == "y":
            global_search()

    return all_results


def display_consolidated_results(all_media_items, search_terms):
    """
    Display consolidated search results from multiple sites using the shared ``TVShowManager`` helper.

    Parameters:
        all_media_items (list): List of media items from all searched sites.
        search_terms (str): The search terms used.

    Returns:
        TVShowManager: Manager instance that contains the displayed rows so
        it can be reused for user interaction (selection).
    """
    time.sleep(1)
    start_message()

    console.print(f"\n[green]Search results for: [yellow]{search_terms} \n")

    manager = TVShowManager()
    has_year = any("year" in item and item["year"] for item in all_media_items)

    # ensure all results appear in a single page
    manager.step = len(all_media_items)
    manager.slice_end = manager.step

    cols = {
        "#": {"color": "dim", "justify": "center", "width": 4},
        "Title": {"color": "magenta", "justify": "left", "min_width": 20},
        "Type": {"color": "yellow", "justify": "center", "width": 15},
    }
    if has_year:
        cols["Year"] = {"color": "green", "justify": "center", "width": 8}
    cols["Source"] = {"color": "cyan", "justify": "left", "width": 25}

    manager.add_column(cols)

    for i, item in enumerate(all_media_items, 1):
        entry = {
            "#": str(i),
            "Title": item.get("title", item.get("name", "Unknown")),  # default fallback
            "Type": item.get("type", item.get("media_type", "Unknown")),
            "Source": item.get("source", "Unknown"),
        }
        if has_year:
            entry["Year"] = str(item.get("year", ""))
        manager.add_tv_show(entry)

    return manager


def select_from_consolidated_results(all_media_items, manager: TVShowManager):
    """
    Prompt the user to choose a single item via the provided manager.

    Parameters:
        all_media_items (list): List of media items from all searched sites.
        manager (TVShowManager): The table manager that displayed the rows.

    Returns:
        dict: The selected media item or None if selection was cancelled.
    """
    if not all_media_items:
        return None

    total = len(all_media_items)
    while True:
        last = manager.run()
        if last is None or str(last).lower() in ["q", "quit"]:
            manager.clear()
            console.print("\n[red]Selection cancelled by user.")
            return None
        try:
            idx = int(last) - 1
            if 0 <= idx < total:
                manager.clear()
                return all_media_items[idx]
            else:
                console.print("\n[red]Invalid or out-of-range index. Please try again.")
        except ValueError:
            console.print("\n[red]Non-numeric input received. Please try again.")


def process_selected_item(selected_item, search_functions):
    """
    Process the selected item - download the media using the appropriate site API.

    Parameters:
        selected_item (dict): The selected media item.
        search_functions (dict): Dictionary of search functions by alias.
    """
    source_alias = selected_item.get("source_alias")
    if not source_alias or source_alias not in search_functions:
        console.print("[red]Error: Cannot process this item - source information missing.")
        return

    # Get the appropriate search function for this source
    func, _ = search_functions[source_alias]

    console.print(f"\n[green]Processing selection from: {selected_item.get('source')}")

    # Extract necessary information to pass to the site's search function
    item_id = None
    for id_field in ["id", "media_id", "ID", "item_id", "url"]:
        item_id = selected_item.get(id_field)
        if item_id:
            break

    item_type = selected_item.get("type", selected_item.get("media_type", "unknown"))
    item_title = selected_item.get("title", selected_item.get("name", "Unknown"))

    if item_id:
        console.print(f"[green]Selected item: {item_title} (ID: {item_id}, Type: {item_type})")

        try:
            func(direct_item=selected_item)

        except Exception as e:
            console.print(f"[red]Error processing download: {str(e)}")
            logger.error("Download processing error")

    else:
        console.print("[red]Error: Item ID not found. Available fields:")
        for key in selected_item.keys():
            console.print(f"[yellow]- {key}: {selected_item[key]}")
