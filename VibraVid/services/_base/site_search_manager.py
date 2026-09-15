# 01.10.25

import logging
from collections.abc import Callable
from typing import Any

from rich.console import Console
from rich.prompt import Prompt

from VibraVid.core.downloader.base import DownloadResult
from VibraVid.core.ui.tracker import context_tracker
from VibraVid.services._base import Entries, EntriesManager, tmdb_artwork
from VibraVid.services._base.site_costant import site_constants
from VibraVid.utils import TVShowManager

console = Console()
msg = Prompt()
logger = logging.getLogger(__name__)
available_colors = ["red", "magenta", "yellow", "cyan", "green", "blue", "white"]
column_to_hide = [
    "Slug",
    "Sub_ita",
    "First_air_date",
    "Seasons_count",
    "Url",
    "Image",
    "Path_id",
    "Score",
    "Edit_id",
    "Chapters",
    "Desc",
    "Genres",
    "Duration",
    "Tmdb_id"
]


def _apply_year_filter(media_search_manager: EntriesManager, year_filter: str) -> int:
    """
    Filter media items by year range.

    Parameters:
        media_search_manager: EntriesManager containing the media items
        year_filter: Year filter string (e.g., "2010-2015" or "2020")

    Returns:
        int: Number of items after filtering
    """
    if not year_filter or not media_search_manager.media_list:
        return len(media_search_manager.media_list or [])

    try:
        year_parts = year_filter.split("-")
        if len(year_parts) == 1:
            # Single year
            min_year = max_year = int(year_parts[0].strip())

        elif len(year_parts) == 2:
            # Range (e.g., "2010-2015")
            min_year = int(year_parts[0].strip())
            max_year = int(year_parts[1].strip())
        else:
            logger.warning(f"Invalid year filter format: {year_filter}. Expected 'YYYY' or 'YYYY-YYYY'")
            return len(media_search_manager.media_list or [])

        # Filter media items
        filtered_items = []
        skipped_count = 0

        for media in media_search_manager.media_list:
            try:
                media_year = int(str(media.year).split("-")[0].strip())
                if min_year <= media_year <= max_year:
                    filtered_items.append(media)
                else:
                    skipped_count += 1
            except (ValueError, TypeError, AttributeError):
                skipped_count += 1

        # Update the media list
        media_search_manager.media_list = filtered_items
        logger.info(f"Year filter applied: {year_filter}. Kept {len(filtered_items)} items, skipped {skipped_count}")
        console.print(f"[cyan]Year filter applied ({year_filter}): [green]{len(filtered_items)}[/] items (skipped {skipped_count})")
        return len(filtered_items)

    except Exception as e:
        logger.error(f"Error applying year filter: {e}")
        console.print(f"[yellow]Warning: Could not apply year filter: {e}")
        return len(media_search_manager.media_list or [])


def _handle_download_result(result: Any) -> None:
    """Print a download error message when a downloader returns one."""
    if result is None:
        return
    err = DownloadResult.from_raw(result).error
    if err:
        console.print(f"[red]{err}")


def _parse_index_selection(cmd_insert: str, max_count: int) -> list[int]:
    """
    Parse a media index selection string into a sorted list of unique 0-based indices.

    Supports a single index ('2'), '*' for all, comma-separated lists ('1,3,5'),
    and ranges ('1-3', '3-*').

    Raises:
        ValueError: If the input is empty or contains an unparseable/out-of-range token.
    """
    if cmd_insert == "*":
        return list(range(max_count))

    indices: set[int] = set()
    for part in cmd_insert.split(","):
        part = part.strip()
        if not part:
            continue

        if "-" in part:
            start_str, end_str = (p.strip() for p in part.split("-", 1))
            start = int(start_str)
            end = int(end_str) if end_str.isnumeric() else max_count - 1
            indices.update(range(start, end + 1))
        else:
            indices.add(int(part))

    if not indices or any(i < 0 or i >= max_count for i in indices):
        raise ValueError("Index out of range")

    return sorted(indices)


def get_select_title(table_show_manager, media_search_manager):
    """
    Display a selection of titles and prompt the user to choose one or more.

    Parameters:
        table_show_manager: Manager for console table display.
        media_search_manager: Manager holding the list of media items.

    Returns:
        list[Entries]: The selected media items, or None if no selection is made or an error occurs.
    """
    if not media_search_manager.media_list:
        return None

    if not media_search_manager.media_list:
        console.print("\n[red]No media items available.")
        logger.info("No media items available for selection.")
        return None

    first_media_item = media_search_manager.media_list[0]
    column_info = {"Index": {"color": available_colors[0]}}

    color_index = 1
    for key in first_media_item.__dict__.keys():
        if key.capitalize() in column_to_hide:
            continue

        if key in ("id", "type", "name", "score"):
            if key == "type":
                column_info["Type"] = {"color": "yellow"}

            elif key == "name":
                column_info["Name"] = {"color": "magenta"}
            elif key == "score":
                column_info["Score"] = {"color": "cyan"}

        else:
            column_info[key.capitalize()] = {"color": available_colors[color_index % len(available_colors)]}
            color_index += 1

    table_show_manager.clear()
    table_show_manager.add_column(column_info)

    for i, media in enumerate(media_search_manager.media_list):
        media_dict = {"Index": str(i)}
        for key in first_media_item.__dict__.keys():
            if key.capitalize() in column_to_hide:
                continue
            media_dict[key.capitalize()] = str(getattr(media, key))
        table_show_manager.add_tv_show(media_dict)

    while True:
        last_command_str = table_show_manager.run()

        if last_command_str is None or last_command_str.lower() in ["q", "quit"]:
            table_show_manager.clear()
            console.print("\n[red]Selection cancelled by user.")
            return None

        try:
            selected_indices = _parse_index_selection(last_command_str, len(media_search_manager.media_list))

            table_show_manager.clear()
            selected_items = [media_search_manager.get(i) for i in selected_indices]
            logger.info(f"Media item(s) selected: {selected_indices}")
            context_tracker.cli_item = selected_indices[0] if len(selected_indices) == 1 else selected_indices
            return selected_items

        except ValueError:
            console.print("\n[red]Invalid or out-of-range index. Please try again.")
            logger.error("Invalid or out-of-range index selected.")


def base_process_search_result(
    select_title: Entries | None,
    download_film_func: Callable[[Entries], Any] | None = None,
    download_series_func: Callable[[Entries, str | None, str | None, Any | None], Any] | None = None,
    download_live_func: Callable[[Entries], Any] | None = None,
    media_search_manager: EntriesManager | None = None,
    table_show_manager: TVShowManager | None = None,
    selections: dict[str, str] | None = None,
    scrape_serie: Any | None = None,
) -> bool:
    """
    Handles the search result and initiates the download for either a film or series.

    Parameters:
        select_title (Entries): The selected media item. Can be None if selection fails.
        download_film_func (callable, optional): Function to download a film
        download_series_func (callable, optional): Function to download a series
        download_live_func (callable, optional): Function to download a live event
        media_search_manager (EntriesManager, optional): Manager to clear after processing
        table_show_manager (TVShowManager, optional): Manager to clear after processing
        selections (dict, optional): Dictionary containing selection inputs that bypass manual input
                                    e.g., {'season': season_selection, 'episode': episode_selection}
        scrape_serie (Any, optional): Pre-existing scraper instance to avoid recreation

    Returns:
        bool: True if processing was successful, False otherwise
    """
    if not select_title:
        console.print("[yellow]No title selected or selection cancelled.")
        logger.error("No title selected or selection cancelled.")
        return False

    # Populate context_tracker
    context_tracker.title = getattr(select_title, "name", None)
    context_tracker.media_type = getattr(select_title, "type", "Film")

    # Clear any season/episode left over from a previous selection.
    context_tracker.season = 0
    context_tracker.episode = 0
    context_tracker.episode_name = None

    try:
        context_tracker.site_name = site_constants.SITE_NAME
    except Exception:
        pass

    # Handle TV series
    if str(select_title.type).lower() in ["tv", "serie", "ova", "ona", "show", "tv short", "special"]:
        if not download_series_func:
            console.print("[red]Error: download_series_func not provided for TV series")
            logger.error("download_series_func not provided for TV series")
            return False

        season_selection = None
        episode_selection = None

        if selections:
            season_selection = selections.get("season")
            episode_selection = selections.get("episode")
            if not scrape_serie:
                scrape_serie = selections.get("scrape_serie")

        context_tracker.series_tmdb_id = tmdb_artwork.resolve_series_tmdb_id(
            tmdb_id=getattr(select_title, "tmdb_id", None),
            name=getattr(select_title, "name", None),
            slug=getattr(select_title, "slug", None),
            year=getattr(select_title, "year", None),
        )

        result = download_series_func(select_title, season_selection, episode_selection, scrape_serie)
        _handle_download_result(result)

        # Clear managers if provided
        if media_search_manager:
            media_search_manager.clear()
        if table_show_manager:
            table_show_manager.clear()

        return True

    # Handle films
    elif str(select_title.type).lower() in ["movie", "film"]:
        if not download_film_func:
            console.print("[red]Error: download_film_func not provided for films")
            logger.error("download_film_func not provided for films")
            return False

        context_tracker.poster_url = tmdb_artwork.resolve_movie_poster_url(
            tmdb_id=getattr(select_title, "tmdb_id", None),
            name=getattr(select_title, "name", None),
            slug=getattr(select_title, "slug", None),
            year=getattr(select_title, "year", None),
        )

        logger.info(f"Initiating download for film: {select_title}")
        film_result = download_film_func(select_title)

        # Clear managers if provided
        if table_show_manager:
            table_show_manager.clear()

        _handle_download_result(film_result)

        # A (path, stopped, error) result is always truthy, so a plain
        # `not film_result` check never catches a real failure — inspect the
        # normalised error element instead.
        film_failed = film_result is None or bool(DownloadResult.from_raw(film_result).error)
        if film_failed:
            console.print(f"[red]Download failed for: {getattr(select_title, 'name', select_title)}")
            logger.error(f"download_film_func failed for: {select_title} (result={film_result!r})")
            return False

        return True

    # Handle music
    elif str(select_title.type).lower() == "song":
        download_func = download_film_func
        if not download_func:
            console.print("[red]Error: download_film_func not provided for song")
            logger.error("download_film_func not provided for song")
            return False

        logger.info(f"Initiating direct download for song: {select_title}")
        song_result = download_func(select_title)

        if table_show_manager:
            table_show_manager.clear()

        if not song_result:
            console.print(f"[red]Download failed for: {getattr(select_title, 'name', select_title)}")
            logger.error(f"download_film_func returned no result for song: {select_title}")
            return False

        return True

    # Handle album (uses series pipeline with episode selection)
    elif str(select_title.type).lower() == "album":
        if not download_series_func:
            console.print("[red]Error: download_series_func not provided for album")
            logger.error("download_series_func not provided for album")
            return False

        season_selection = None
        episode_selection = None

        if selections:
            season_selection = selections.get("season")
            episode_selection = selections.get("episode")
            if not scrape_serie:
                scrape_serie = selections.get("scrape_serie")

        logger.info(f"Initiating album download with season: {season_selection}, episode: {episode_selection}")
        result = download_series_func(select_title, season_selection, episode_selection, scrape_serie)
        _handle_download_result(result)

        if media_search_manager:
            media_search_manager.clear()
        if table_show_manager:
            table_show_manager.clear()

        return True

    # Handle live events
    elif str(select_title.type).lower() == "live":
        if not download_live_func:
            console.print("[red]Error: download_live_func not provided for live events")
            logger.error("download_live_func not provided for live events")
            return False

        download_live_func(select_title)
        logger.info(f"Initiating download for live event: {select_title}")

        if table_show_manager:
            table_show_manager.clear()

        return True

    else:
        console.print(f"[red]Unknown media type: {select_title.type}")
        logger.error(f"Unknown media type: {select_title.type}")
        return False


def base_search(
    title_search_func: Callable[[str], int],
    process_result_func: Callable[[Entries | None, dict[str, str] | None, Any | None], bool],
    media_search_manager: EntriesManager,
    table_show_manager: TVShowManager,
    site_name: str,
    string_to_search: str | None = None,
    get_onlyDatabase: bool = False,
    direct_item: dict[str, Any] | None = None,
    selections: dict[str, str] | None = None,
    scrape_serie: Any | None = None,
) -> Any:
    """
    Generalized search function for streaming sites.

    Parameters:
        title_search_func (callable): Function that performs the actual search and returns number of results
        process_result_func (callable): Function that processes the selected result
        media_search_manager (EntriesManager): Manager for media search results
        table_show_manager (TVShowManager): Manager for displaying results
        site_name (str): Name of the site being searched
        string_to_search (str, optional): String to search for. Can be passed from run.py.
        get_onlyDatabase (bool, optional): If True, return only the database search manager object.
        direct_item (dict, optional): Direct item to process (bypasses search).
        selections (dict, optional): Dictionary containing selection inputs that bypass manual input
                                     for series (season/episode).
        scrape_serie (Any, optional): Pre-existing scraper instance to avoid recreation.

    Returns:
        EntriesManager if get_onlyDatabase=True, bool otherwise
    """
    # Handle direct item processing
    if direct_item:
        logger.info("Processing direct item without search.")
        select_title = Entries(**direct_item)
        result = process_result_func(select_title, selections, scrape_serie)
        return result

    # Get the user input for the search term
    actual_search_query = None
    if string_to_search is not None:
        logger.info(f"Using provided search string: {string_to_search}")
        actual_search_query = string_to_search.strip()
    else:
        actual_search_query = msg.ask(f"\n[purple]Insert a word to search in [green]{site_name}").strip()

    context_tracker.cli_search = actual_search_query
    context_tracker.cli_season_selection = None
    context_tracker.cli_episode_selection = None

    # Search on database
    len_database = title_search_func(str(actual_search_query).strip())

    # Sort results by fuzzy score
    logger.debug(f"Sorting {len_database} results by fuzzy score for query: '{actual_search_query}'")
    media_search_manager.sort_by_fuzzy_score(actual_search_query)

    # Apply year filter if provided
    if selections and "year" in selections:
        year_filter = selections.get("year")
        logger.info(f"Applying year filter: {year_filter}")
        len_database = _apply_year_filter(media_search_manager, year_filter)

    # Handle empty input
    if not actual_search_query:
        logger.error("Empty search query provided.")
        return False

    # If only the database is needed, return the manager
    if get_onlyDatabase:
        return media_search_manager

    # Process results
    if len_database > 0:
        selected_items = get_select_title(table_show_manager, media_search_manager)

        if not selected_items:
            return process_result_func(None, selections, scrape_serie)

        if len(selected_items) == 1:
            return process_result_func(selected_items[0], selections, scrape_serie)

        overall_result = True
        for i, item in enumerate(selected_items, 1):
            console.print(f"\n[cyan]Processing {i}/{len(selected_items)}: [magenta]{getattr(item, 'name', item)}")
            item_result = process_result_func(item, selections, scrape_serie)
            if not item_result:
                overall_result = False

        return overall_result
    else:
        console.print(f"\n[red]Nothing matching was found for[white]: [purple]{actual_search_query}")
        return False


def make_search_entrypoints(
    *,
    title_search: Callable[[str], int],
    entries_manager: EntriesManager,
    table_show_manager: TVShowManager,
    download_film: Callable[[Entries], Any] | None = None,
    download_series: Callable[[Entries, str | None, str | None, Any | None], Any] | None = None,
    download_live: Callable[[Entries], Any] | None = None,
    resolve_url: Callable[[str], Any] | None = None,
    process_result: Callable[[Entries | None, dict[str, str] | None, Any | None], bool] | None = None,
    site_name: str | None = None,
) -> tuple[Callable[..., Any], Callable[..., bool]]:
    """
    Build the module-level ``(search, process_search_result)`` pair that every
    service otherwise copy-pastes verbatim into its ``__init__.py``.

    Only the parts that actually vary per service are parameters: the
    ``download_*`` callables, an optional ``resolve_url`` prestep (``--url`` ->
    ``direct_item``), and — for the couple of services with bespoke routing
    (e.g. monochrome album/song) — a ``process_result`` override.

    Canonical minimal call: ``services/streamingcommunity/__init__.py``.

    Returns:
        (search, process_search_result) — bind both at module level:
        ``search, process_search_result = make_search_entrypoints(...)``.
    """
    resolved_site_name = site_name or site_constants.SITE_NAME

    def process_search_result(select_title, selections=None, scrape_serie=None):
        return base_process_search_result(
            select_title=select_title,
            download_film_func=download_film,
            download_series_func=download_series,
            download_live_func=download_live,
            media_search_manager=entries_manager,
            table_show_manager=table_show_manager,
            selections=selections,
            scrape_serie=scrape_serie,
        )

    process_result_func = process_result or process_search_result

    def search(
        string_to_search: str = None,
        get_onlyDatabase: bool = False,
        direct_item: dict = None,
        selections: dict = None,
        scrape_serie=None,
    ):
        if resolve_url is not None and direct_item is None and not get_onlyDatabase:
            url = (context_tracker.site_options or {}).get("url")
            if url:
                direct_item = resolve_url(url)
                if not direct_item:
                    return False

        return base_search(
            title_search_func=title_search,
            process_result_func=process_result_func,
            media_search_manager=entries_manager,
            table_show_manager=table_show_manager,
            site_name=resolved_site_name,
            string_to_search=string_to_search,
            get_onlyDatabase=get_onlyDatabase,
            direct_item=direct_item,
            selections=selections,
            scrape_serie=scrape_serie,
        )

    return search, process_search_result
