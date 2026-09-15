# 19.06.24

import logging
import re as _re
import sys

from rich.console import Console
from rich.prompt import Prompt

from VibraVid.provider.tmdb import tmdb_client
from VibraVid.utils import config_manager, os_manager
from VibraVid.utils.console import TVShowManager

msg = Prompt()
console = Console()
logger = logging.getLogger(__name__)
MOVIE_FORMAT = config_manager.config.get("OUTPUT", "movie_format")
EPISODE_FORMAT = config_manager.config.get("OUTPUT", "episode_format")
SONG_FORMAT = config_manager.config.get("OUTPUT", "song_format", default=None)


def refresh_output_formats() -> None:
    """Re-read MOVIE_FORMAT/EPISODE_FORMAT/SONG_FORMAT from config.

    These are cached as module-level constants (read once at import time)
    for CLI runs, where the process is short-lived and this is harmless.
    In the long-running GUI/Docker server, saving a new format from
    Settings calls config_manager.reload_config_only() - which refreshes
    the config cache itself, but does nothing for names that already
    copied a value out of it at import time. Without this, an edited
    output format silently keeps using whatever was in effect when the
    server started, until the next full restart.
    """
    global MOVIE_FORMAT, EPISODE_FORMAT, SONG_FORMAT
    MOVIE_FORMAT = config_manager.config.get("OUTPUT", "movie_format")
    EPISODE_FORMAT = config_manager.config.get("OUTPUT", "episode_format")
    SONG_FORMAT = config_manager.config.get("OUTPUT", "song_format", default=None)


_MEDIA_TOKENS = {"quality", "language", "video_codec", "audio_codec"}
_TMDB_TOKENS = (
    "%(tmdb_id)",
    "%(imdb_id)",
    "%(original_title)",
    "%(original_language)",
    "%(tmdb_title)",
    "%(tmdb_episode_title)",
    "%(tmdb_season_name)",
    "%(tmdb_season_number",
)
_DUB_TAG_RE = _re.compile(r"\s*\((?:ita|sub|sub\s*ita|dub|cr)\)\s*$", _re.IGNORECASE)


def _apply_format_token(token: str, value: int) -> str:
    """
    Applies an inline format specifier to a numeric value.

    Supported specifiers:
        - '02d'  -> zero-padded to 2 digits  (e.g. 01, 10, 100)
        - '03d'  -> zero-padded to 3 digits  (e.g. 001, 010, 100)
        - 'd'    -> no padding               (e.g. 1, 10, 100)

    Parameters:
        token (str): The format specifier extracted from the template.
        value (int): The numeric value to format.

    Returns:
        str: The formatted number string.
    """
    if token == "d":
        return str(value)
    if token.endswith("d") and token[:-1].isdigit():
        return str(value).zfill(int(token[:-1]))
    return str(value).zfill(2)


def _replace_format_key(fmt_string: str, key: str, value) -> str:
    """
    Replaces all occurrences of %(key:FORMAT) in a format string.

    Supported syntax:
        %(season:02d)   -> zero-padded to 2 digits
        %(season:03d)   -> zero-padded to 3 digits
        %(season:d)     -> no padding
        %(episode:02d)  -> zero-padded to 2 digits
        %(episode:d)    -> no padding

    Parameters:
        fmt_string (str): The format string to process.
        key (str): The token key to replace (e.g. 'season', 'episode').
        value: The numeric value to substitute.

    Returns:
        str: The format string with all occurrences of the key replaced.
    """

    def replacer(match):
        token = match.group(1)
        try:
            n = int(str(value))
            return _apply_format_token(token, n)
        except (ValueError, TypeError):
            return str(value)

    pattern = _re.compile(r"%\(" + _re.escape(key) + r":([^)]+)\)")
    return pattern.sub(replacer, fmt_string)


def _resolve_tmdb_tokens(
    fmt: str, title: str, year, media_type: str, season_number: int = None, episode_number: int = None
) -> str:
    """Resolve TMDB-backed tokens (%(tmdb_id), %(imdb_id), %(original_title), %(original_language), %(tmdb_title), %(tmdb_episode_title)) when present in the format string."""
    if not title or not any(tok in fmt for tok in _TMDB_TOKENS):
        return fmt

    try:
        lookup_title = _DUB_TAG_RE.sub("", title)
        slug = tmdb_client._slugify(lookup_title)
        year_str = None
        if year is not None:
            y = str(year).split("-")[0].strip()
            year_str = y if (y.isdigit() and len(y) == 4) else None

        info = tmdb_client.get_type_and_id_by_slug_year(slug, year_str, media_type)
        if not info or not info.get("id"):
            return fmt

        tmdb_id = info["id"]
        resolved_type = info.get("type", media_type)

        if "%(tmdb_id)" in fmt:
            fmt = fmt.replace("%(tmdb_id)", str(tmdb_id))

        if "%(imdb_id)" in fmt:
            imdb_id = tmdb_client.get_imdb_id(tmdb_id, resolved_type)
            if imdb_id:
                fmt = fmt.replace("%(imdb_id)", str(imdb_id))

        if "%(tmdb_title)" in fmt:
            tmdb_title = tmdb_client.get_title(tmdb_id, resolved_type)
            if tmdb_title:
                fmt = fmt.replace("%(tmdb_title)", os_manager.get_sanitize_file(tmdb_title))

        if "%(original_title)" in fmt:
            original_title = tmdb_client.get_original_title(tmdb_id, resolved_type)
            if original_title:
                fmt = fmt.replace("%(original_title)", os_manager.get_sanitize_file(original_title))

        if "%(original_language)" in fmt:
            original_language = tmdb_client.get_original_language(tmdb_id, resolved_type)
            if original_language:
                fmt = fmt.replace("%(original_language)", str(original_language))

        if (
            "%(tmdb_episode_title)" in fmt
            and resolved_type == "tv"
            and season_number is not None
            and episode_number is not None
        ):
            real_season, real_episode = tmdb_client.resolve_actual_season_episode(
                tmdb_id, season_number, episode_number
            )
            tmdb_episode_title = tmdb_client.get_episode_title(tmdb_id, real_season, real_episode)
            if tmdb_episode_title:
                fmt = fmt.replace("%(tmdb_episode_title)", os_manager.get_sanitize_file(tmdb_episode_title))

        # Only mapped when explicitly requested — avoids extra TMDB calls for everyone else.
        if (
            ("%(tmdb_season_name)" in fmt or "%(tmdb_season_number" in fmt)
            and resolved_type == "tv"
            and season_number is not None
            and episode_number is not None
        ):
            real_season, _ = tmdb_client.resolve_actual_season_episode(tmdb_id, season_number, episode_number)

            if "%(tmdb_season_name)" in fmt:
                season_name = tmdb_client.get_season_name(tmdb_id, real_season)
                if season_name:
                    fmt = fmt.replace("%(tmdb_season_name)", os_manager.get_sanitize_file(season_name))

            if "%(tmdb_season_number" in fmt:
                fmt = fmt.replace("%(tmdb_season_number)", str(real_season))
                fmt = _replace_format_key(fmt, "tmdb_season_number", real_season)

    except Exception as e:
        logger.warning(f"TMDB token resolution failed for '{title}': {e}")

    return fmt


def _strip_unknown_tokens(fmt: str) -> str:
    """
    Remove any leftover %(...) token (and an immediately surrounding [] or ()
    wrapper) that was not resolved, so unresolved placeholders never leak into
    the final filename. Media tokens in _MEDIA_TOKENS are preserved because they
    are resolved later, after muxing, in core/downloader/base.py::_finalize.

    Mirrors the cleanup already applied to track_number in map_song_path.
    """

    def replacer(match):
        key = match.group("key")
        if key in _MEDIA_TOKENS:
            return match.group(0)
        return ""

    pattern = _re.compile(r"[\[(]?%\((?P<key>\w+)(?::[^)]+)?\)[\])]?")
    fmt = pattern.sub(replacer, fmt)
    fmt = _re.sub(r"  +", " ", fmt).replace(" /", "/").replace("/ ", "/")
    return fmt.strip()


def manage_selection(cmd_insert: str, max_count: int) -> list[int]:
    """
    Manage user selection for seasons or episodes to download.

    Parameters:
        cmd_insert (str): User input for selection.
        max_count (int): Maximum count available.

    Returns:
        list_selection (List[int]): List of selected items.
    """
    while True:
        list_selection = []

        if cmd_insert.lower() in ("q", "quit"):
            console.print("\n[red]Quit ...")
            sys.exit(0)

        # For all items ('*')
        if cmd_insert == "*":
            list_selection = list(range(1, max_count + 1))
            break

        try:
            # Handle comma separated values and ranges
            parts = cmd_insert.split(",")
            for part in parts:
                part = part.strip()
                if not part:
                    continue

                if "-" in part:
                    start, end = map(str.strip, part.split("-"))
                    start = int(start)

                    # Handle end part (could be numeric or '*' or empty for max_count)
                    if end.isnumeric():
                        end = int(end)
                    else:
                        end = max_count

                    list_selection.extend(list(range(start, end + 1)))
                elif part.isnumeric():
                    list_selection.append(int(part))
                else:
                    raise ValueError

            if list_selection:
                list_selection = sorted(list(set(list_selection)))
                break

        except (ValueError, TypeError):
            pass

        cmd_insert = msg.ask("[red]Invalid input. Please enter a valid command")

    return list_selection


def map_movie_path(title_name: str, title_year: str = None) -> tuple:
    """
    Maps the complete movie directory and filename using the movie_format config.

    Parameters:
        title_name (str): The name of the movie.
        title_year (str): The release year of the movie (optional).

    Returns:
        tuple: (path_components, filename) where path_components is a list for path assembly and filename is the final movie filename.
    """
    map_movie_temp = MOVIE_FORMAT
    logger.info(f"Mapping movie path with name: {title_name} and year: {title_year}")

    if title_name is not None:
        # Support both %(title_name) and %(title_name_slug)
        map_movie_temp = map_movie_temp.replace("%(title_name)", os_manager.get_sanitize_file(title_name))
        map_movie_temp = map_movie_temp.replace("%(title_name_slug)", tmdb_client._slugify(title_name))

    if title_year is not None:
        y = str(title_year).split("-")[0].strip()
        if y.isdigit() and len(y) == 4:
            map_movie_temp = map_movie_temp.replace("%(title_year)", y)
        else:
            map_movie_temp = map_movie_temp.replace("(%(title_year))", "").strip()
            map_movie_temp = map_movie_temp.replace("%(title_year)", "").strip()
    else:
        map_movie_temp = map_movie_temp.replace("(%(title_year))", "").strip()
        map_movie_temp = map_movie_temp.replace("%(title_year)", "").strip()

    # TMDB-backed tokens (best-effort)
    map_movie_temp = _resolve_tmdb_tokens(map_movie_temp, title_name, title_year, "movie")

    # Drop any token left unresolved (media tokens survive for _finalize)
    map_movie_temp = _strip_unknown_tokens(map_movie_temp)

    # Split into path components and filename
    parts = map_movie_temp.split("/")
    filename = parts[-1] if parts else map_movie_temp
    path_components = parts[:-1] if len(parts) > 1 else []
    return (path_components, filename)


def map_song_path(artist: str, album: str, title: str, year: str = None, track_number: int = None) -> tuple:
    """
    Maps the complete song directory and filename using the song_format config.

    Supported tokens in song_format:
        %(artist)            - artist name
        %(album)             - album name
        %(title)             - track title
        %(year)              - release year (omitted with surrounding parens if missing)
        %(track_number:02d)  - track number with inline format spec (same as season/episode)

    Returns:
        tuple: (path_components, filename) where path_components is a list for
               os.path.join assembly and filename is the final track filename (no extension).
    """
    logger.info(f"Mapping song path: artist={artist} album={album} title={title} year={year} track_number={track_number}")
    fmt = SONG_FORMAT

    # ── artist
    if artist:
        fmt = fmt.replace("%(artist)", os_manager.get_sanitize_file(artist))
        fmt = fmt.replace("%(artist_slug)", tmdb_client._slugify(artist))
    else:
        fmt = fmt.replace("%(artist)", "Unknown Artist")
        fmt = fmt.replace("%(artist_slug)", "unknown-artist")

    # ── album
    if album:
        fmt = fmt.replace("%(album)", os_manager.get_sanitize_file(album))
        fmt = fmt.replace("%(album_slug)", tmdb_client._slugify(album))
    else:
        fmt = fmt.replace("%(album)", "Unknown Album")
        fmt = fmt.replace("%(album_slug)", "unknown-album")

    # ── title
    if title:
        fmt = fmt.replace("%(title)", os_manager.get_sanitize_file(title))
        fmt = fmt.replace("%(title_slug)", tmdb_client._slugify(title))
    else:
        fmt = fmt.replace("%(title)", "Unknown Track")
        fmt = fmt.replace("%(title_slug)", "unknown-track")

    # ── year (optional — strip surrounding parens when absent)
    if year is not None:
        y = str(year).split("-")[0].strip()
        if y.isdigit() and len(y) == 4:
            fmt = fmt.replace("%(year)", y)
        else:
            fmt = fmt.replace("(%(year))", "").strip()
            fmt = fmt.replace("%(year)", "").strip()
    else:
        fmt = fmt.replace("(%(year))", "").strip()
        fmt = fmt.replace("%(year)", "").strip()

    # ── track_number (optional — strip surrounding text when absent)
    if track_number is not None:
        fmt = _replace_format_key(fmt, "track_number", int(track_number))
    else:
        fmt = _re.sub(r"%\(track_number:[^)]+\)[.\s]*", "", fmt)

    # Clean up any double spaces left after removals
    fmt = _re.sub(r"  +", " ", fmt).strip()

    parts = fmt.split("/")
    filename = parts[-1] if parts else fmt
    path_components = parts[:-1] if len(parts) > 1 else []
    return (path_components, filename)


def map_episode_path(
    series_name: str,
    series_year: str = None,
    season_number: int = None,
    episode_number: int = None,
    episode_name: str = None,
    absolute_number: int = None,
) -> tuple:
    """
    Maps the complete episode path and filename using the consolidated episode_format config.

    Parameters:
        series_name (str): The name of the series.
        series_year (str): The release year of the series (optional).
        season_number (int): The season number.
        episode_number (int): The episode number.
        episode_name (str): The name of the episode.
        absolute_number (int): The absolute episode number (anime); enables %(absolute:FORMAT).

    Returns:
        tuple: (path_components, filename) where path_components is a list for path assembly
               and filename is the final episode filename.
    """
    logger.info(f"Mapping episode path with series name: {series_name}, series year: {series_year}, season number: {season_number}, episode number: {episode_number}, episode name: {episode_name}")
    map_episode_temp = EPISODE_FORMAT

    # Replace series_name and its variant
    if series_name is not None:
        map_episode_temp = map_episode_temp.replace("%(series_name)", os_manager.get_sanitize_file(series_name))
        map_episode_temp = map_episode_temp.replace("%(series_name_slug)", tmdb_client._slugify(series_name))

    # Replace series_year if present
    if series_year is not None:
        y = str(series_year).split("-")[0].strip()
        if y.isdigit() and len(y) == 4:
            map_episode_temp = map_episode_temp.replace("%(series_year)", y)
        else:
            map_episode_temp = map_episode_temp.replace("(%(series_year))", "").strip()
            map_episode_temp = map_episode_temp.replace("%(series_year)", "").strip()
    else:
        map_episode_temp = map_episode_temp.replace("(%(series_year))", "").strip()
        map_episode_temp = map_episode_temp.replace("%(series_year)", "").strip()

    # Replace season and episode numbers (honours inline format spec e.g. :02d, :d, :03d)
    season_val = season_number if season_number is not None else 0
    map_episode_temp = _replace_format_key(map_episode_temp, "season", season_val)

    episode_val = episode_number if episode_number is not None else 0
    map_episode_temp = _replace_format_key(map_episode_temp, "episode", episode_val)

    # Replace episode_name and its variant
    if episode_name is not None:
        map_episode_temp = map_episode_temp.replace("%(episode_name)", os_manager.get_sanitize_file(episode_name))
        map_episode_temp = map_episode_temp.replace("%(episode_name_slug)", tmdb_client._slugify(episode_name))

    # Absolute episode number (anime) — honours inline format spec e.g. :000, :d
    if absolute_number is not None:
        map_episode_temp = _replace_format_key(map_episode_temp, "absolute", absolute_number)

    # TMDB-backed tokens (best-effort)
    map_episode_temp = _resolve_tmdb_tokens(map_episode_temp, series_name, series_year, "tv", season_val, episode_val)

    # Drop any token left unresolved (media tokens survive for _finalize)
    map_episode_temp = _strip_unknown_tokens(map_episode_temp)

    # Split into path components and filename
    parts = map_episode_temp.split("/")
    filename = parts[-1] if parts else map_episode_temp
    path_components = parts[:-1] if len(parts) > 1 else []

    return (path_components, filename)


def validate_selection(list_season_select: list[int], available_seasons: list[int]) -> list[int]:
    """
    Validates and adjusts the selected seasons based on the available seasons.

    Parameters:
        list_season_select (List[int]): List of seasons selected by the user.
        available_seasons (List[int]): List of available season numbers.

    Returns:
        - List[int]: Adjusted list of valid season numbers.
    """
    while True:
        try:
            # Remove any seasons not in the available seasons
            valid_seasons = [season for season in list_season_select if season in available_seasons]

            # If the list is empty, the input was completely invalid
            if not valid_seasons:
                input_seasons = msg.ask(f"[red]Enter valid season numbers ({', '.join(map(str, available_seasons))})")
                list_season_select = list(map(int, input_seasons.split(",")))
                continue

            return valid_seasons

        except ValueError:
            input_seasons = input(f"Enter valid season numbers ({', '.join(map(str, available_seasons))}): ")
            list_season_select = list(map(int, input_seasons.split(",")))


def display_seasons_list(seasons_manager) -> str:
    """
    Display seasons list and handle user input.

    Parameters:
        - seasons_manager: Manager object containing seasons information.

    Returns:
        last_command (str): Last command entered by the user.
    """
    if len(seasons_manager.seasons) == 1:
        return "1"

    # Set up table for displaying seasons
    table_show_manager = TVShowManager()

    # Check if 'type', 'id' or 'extra' attributes exist in seasons
    try:
        first = seasons_manager.seasons[0]
        has_type = hasattr(first, "type") and (first.type) is not None and str(first.type) != ""
        has_id = hasattr(first, "id") and (first.id) is not None and str(first.id) != ""
    except IndexError:
        has_type = False
        has_id = False

    # Determine if any season has a non-empty extra field
    has_extra = False
    for s in seasons_manager.seasons:
        extra = getattr(s, "extra", None)
        if extra is not None and str(extra).strip() != "":
            has_extra = True
            break

    # Add columns to the table
    column_info = {"Index": {"color": "red"}, "Name": {"color": "yellow"}}
    if has_type:
        column_info["Type"] = {"color": "magenta"}
    if has_id:
        column_info["ID"] = {"color": "cyan"}
    if has_extra:
        column_info["Extra"] = {"color": "green"}

    table_show_manager.add_column(column_info)

    # Populate the table with seasons information
    for i, season in enumerate(seasons_manager.seasons):
        season_name = season.name if hasattr(season, "name") else "N/A"
        season_info = {"Index": str(i + 1), "Name": season_name}
        if has_type:
            season_info["Type"] = season.type if hasattr(season, "type") else "N/A"
        if has_id:
            season_info["ID"] = season.id if hasattr(season, "id") else "N/A"
        if has_extra:
            season_info["Extra"] = getattr(season, "extra", "")
        table_show_manager.add_tv_show(season_info)

    # Run the table and handle user input
    last_command = table_show_manager.run()
    if last_command in ("q", "quit"):
        console.print("\n[red]Quit ...")
        sys.exit(0)
    return last_command


def display_episodes_list(episodes_manager) -> str:
    """
    Display episodes list and handle user input.

    Returns:
        last_command (str): Last command entered by the user.
    """
    # Set up table for displaying episodes
    table_show_manager = TVShowManager()

    def _field(media, key):
        return media.get(key) if isinstance(media, dict) else getattr(media, key, None)

    def _has(key):
        return any(_field(m, key) is not None and str(_field(m, key)).strip() != "" for m in episodes_manager)

    has_category = _has("category")
    has_duration = _has("duration")
    has_releasedate = _has("release_date")
    has_genre = _has("genre")
    has_language = _has("language")

    # Add columns to the table
    column_info = {
        "Index": {"color": "red"},
    }

    column_info["Name"] = {"color": "magenta"}

    if has_category:
        column_info["Category"] = {"color": "green"}

    if has_duration:
        column_info["Duration"] = {"color": "blue"}

    if has_releasedate:
        column_info["Release Date"] = {"color": "cyan"}

    if has_genre:
        column_info["Genre"] = {"color": "green"}

    if has_language:
        column_info["Language"] = {"color": "yellow"}

    table_show_manager.add_column(column_info)

    # Populate the table with episodes information
    for i, media in enumerate(episodes_manager):
        episode_info = {
            "Index": str(i + 1),
            "Name": _field(media, "name"),
        }
        if has_category:
            episode_info["Category"] = _field(media, "category")

        if has_duration:
            episode_info["Duration"] = _field(media, "duration")

        if has_releasedate:
            episode_info["Release Date"] = _field(media, "release_date")

        if has_genre:
            episode_info["Genre"] = _field(media, "genre")

        if has_language:
            episode_info["Language"] = _field(media, "language")

        table_show_manager.add_tv_show(episode_info)

    # Run the table and handle user input
    last_command = table_show_manager.run()

    if last_command in ("q", "quit"):
        console.print("\n[red]Quit ...")
        sys.exit(0)

    return last_command
