# 17.03.25


import time
import logging


# External library
from rich.console import Console
from rich.prompt import Prompt
from rich.table import Table


# Internal utilities
from StreamingCommunity.Util import start_message
from StreamingCommunity.Api.Template import load_search_functions


# Variable
console = Console()
msg = Prompt()



def global_search(search_terms: str = None, selected_sites: list = None):
    """
    Perform a search across multiple sites based on selection.
    
    Parameters:
        search_terms (str, optional): The terms to search for. If None, will prompt the user.
        selected_sites (list, optional): List of site aliases to search. If None, will search all sites.
    
    Returns:
        dict: Consolidated search results from all searched sites.
    """
    search_functions = load_search_functions()
    all_results = {}
    
    if search_terms is None:
        search_terms = msg.ask("\n[purple]Enter search terms for global search: ").strip()
    
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
            selected_categories = [list(sites_by_category.keys())[int(c.strip())-1] for c in category_choices.split(",")]
            
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
            selected_indices = [int(c.strip())-1 for c in site_choices.split(",")]
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
            if database and hasattr(database, 'media_list') and len(database.media_list) > 0:
                # Store media_list items with additional source information
                all_results[alias] = []
                for element in database.media_list:
                    # Convert element to dictionary if it's an object
                    if hasattr(element, '__dict__'):
                        item_dict = element.__dict__.copy()
                    else:
                        item_dict = {}  # Fallback for non-object items
                        
                    # Add source information
                    item_dict['source'] = site_name
                    item_dict['source_alias'] = alias
                    all_results[alias].append(item_dict)
                    
                console.print(f"[green]Found result: {len(database.media_list)}\n")

        except Exception as e:
            console.print(f"[red]Error searching {site_name}: {str(e)}")
    
    # Display the consolidated results
    if all_results:
        all_media_items = []
        for alias, results in all_results.items():
            for item in results:
                all_media_items.append(item)
        
        # Display consolidated results
        display_consolidated_results(all_media_items, search_terms)
        
        # Allow user to select an item
        selected_item = select_from_consolidated_results(all_media_items)
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
    Display consolidated search results from multiple sites.
    
    Parameters:
        all_media_items (list): List of media items from all searched sites.
        search_terms (str): The search terms used.
    """    
    time.sleep(1)
    start_message()

    console.print(f"\n[green]Search results for: [yellow]{search_terms} \n")
    
    table = Table(show_header=True, header_style="cyan")
    table.add_column("#", style="dim", width=4)
    table.add_column("Title", min_width=20)
    table.add_column("Type", width=15)
    table.add_column("Source", width=25)
    
    for i, item in enumerate(all_media_items, 1):

        # Extract values from item dict, with fallbacks if keys don't exist
        title = item.get('title', item.get('name', 'Unknown'))
        media_type = item.get('type', item.get('media_type', 'Unknown'))
        source = item.get('source', 'Unknown')
        
        table.add_row(
            str(i),
            str(title),
            str(media_type),
            str(source),
        )
    
    console.print(table)

def select_from_consolidated_results(all_media_items):
    """
    Allow user to select an item from consolidated results.
    
    Parameters:
        all_media_items (list): List of media items from all searched sites.
    
    Returns:
        dict: The selected media item or None if no selection was made.
    """
    if not all_media_items:
        return None
    
    max_index = len(all_media_items)
    choice = msg.ask(
        f"[green]Select item # (1-{max_index}) or 0 to cancel",
        choices=[str(i) for i in range(max_index + 1)],
        default="1",
        show_choices=False
    )
    
    if choice == "0":
        return None
    
    return all_media_items[int(choice) - 1]

def process_selected_item(selected_item, search_functions):
    """
    Process the selected item - download the media using the appropriate site API.
    
    Parameters:
        selected_item (dict): The selected media item.
        search_functions (dict): Dictionary of search functions by alias.
    """
    source_alias = selected_item.get('source_alias')
    if not source_alias or source_alias not in search_functions:
        console.print("[red]Error: Cannot process this item - source information missing.")
        return
    
    # Get the appropriate search function for this source
    func, _ = search_functions[source_alias]
    
    console.print(f"\n[green]Processing selection from: {selected_item.get('source')}")
    
    # Extract necessary information to pass to the site's search function
    item_id = None
    for id_field in ['id', 'media_id', 'ID', 'item_id', 'url']:
        item_id = selected_item.get(id_field)
        if item_id:
            break
            
    item_type = selected_item.get('type', selected_item.get('media_type', 'unknown'))
    item_title = selected_item.get('title', selected_item.get('name', 'Unknown'))
    
    if item_id:
        console.print(f"[green]Selected item: {item_title} (ID: {item_id}, Type: {item_type})")
        
        try:
            func(direct_item=selected_item)

        except Exception as e:
            console.print(f"[red]Error processing download: {str(e)}")
            logging.exception("Download processing error")
            
    else:
        console.print("[red]Error: Item ID not found. Available fields:")
        for key in selected_item.keys():
            console.print(f"[yellow]- {key}: {selected_item[key]}")