# 21.05.24

# External library
from rich.console import Console
from rich.prompt import Prompt


# Internal utilities
from StreamingCommunity.Api.Template import site_constants, MediaItem, get_select_title


# Logic
from .site import title_search, table_show_manager, media_search_manager
from .series import download_series
from .film import download_film


# Variable
indice = 5
_useFor = "Film_&_Serie"
_deprecate = False

msg = Prompt()
console = Console()


def process_search_result(select_title, selections=None):
    """
    Handles the search result and initiates the download for either a film or series.
    
    Parameters:
        select_title (MediaItem): The selected media item
        selections (dict, optional): Dictionary containing selection inputs that bypass manual input
                                    {'season': season_selection, 'episode': episode_selection}

    Returns:
        bool: True if processing was successful, False otherwise
    """
    if not select_title:
        console.print("[yellow]No title selected or selection cancelled.")
        return False
    
    if select_title.type == 'tv':
        season_selection = None
        episode_selection = None
        
        if selections:
            season_selection = selections.get('season')
            episode_selection = selections.get('episode')

        download_series(select_title, season_selection, episode_selection)
        media_search_manager.clear()
        table_show_manager.clear()
        return True

    else:
        download_film(select_title)
        table_show_manager.clear()
        return True


def search(string_to_search: str = None, get_onlyDatabase: bool = False, direct_item: dict = None, selections: dict = None):
    """
    Main function of the application for search.

    Parameters:
        string_to_search (str, optional): String to search for
        get_onlyDatabase (bool, optional): If True, return only the database object
        direct_item (dict, optional): Direct item to process (bypass search)
        selections (dict, optional): Dictionary containing selection inputs that bypass manual input
                                    {'season': season_selection, 'episode': episode_selection}
    """
    if direct_item:
        select_title = MediaItem(**direct_item)
        result = process_search_result(select_title, selections)
        return result
    
    # Get the user input for the search term
    actual_search_query = None
    if string_to_search is not None:
        actual_search_query = string_to_search.strip()
    else:
        actual_search_query = msg.ask(f"\n[purple]Insert a word to search in [green]{site_constants.SITE_NAME}").strip()

    # Handle empty input
    if not actual_search_query:
        return False

    # Search on database
    len_database = title_search(actual_search_query)

    # If only the database is needed, return the manager
    if get_onlyDatabase:
        return media_search_manager
    
    if len_database > 0:
        select_title = get_select_title(table_show_manager, media_search_manager, len_database)
        result = process_search_result(select_title, selections)
        return result
    
    else:
        console.print(f"\n[red]Nothing matching was found for[white]: [purple]{actual_search_query}")
        return False