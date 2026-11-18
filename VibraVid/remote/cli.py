# 12.10.26

import argparse
import logging
import os

from rich.console import Console
from rich.prompt import Prompt

from VibraVid.core.ui.tracker import context_tracker

from .client import RemoteClient, make_license_request_fn, make_manifest_refresh_fn
from .config import RemoteServer, pick_remote_server
from .credentials import LocalCache, apply_updates
from .factory import build_downloader, download_media
from .models import RemoteError, ResolvedMedia

console = Console()
logger = logging.getLogger(__name__)
_remote_option_dests: list[str] = []


def parse_server_options(pairs: list[str] | None) -> dict:
    """``--server-opt key=value`` pairs → typed dict (true/false → bool, digits → int)."""
    options: dict = {}
    for pair in pairs or []:
        key, sep, value = pair.partition("=")
        if not sep or not key.strip():
            raise ValueError(f"--server-opt expects KEY=VALUE, got '{pair}'")
        value = value.strip()
        if value.lower() in ("true", "false"):
            options[key.strip()] = value.lower() == "true"
        elif value.lstrip("-").isdigit():
            options[key.strip()] = int(value)
        else:
            options[key.strip()] = value
    return options


def _service_entry(row: dict):
    """The service's own ``Entries`` for a search result: the very item its scraper produced (``raw_data``)."""
    from VibraVid.services._base import Entries

    payload = row.get("payload") or {}
    raw = payload.get("raw_data") if isinstance(payload.get("raw_data"), dict) else None
    if raw:
        return Entries(**raw)
    return Entries(name=row.get("title"), type=row.get("type"), year=row.get("year"), url=payload.get("url"))


def select_results(results: list[dict], item_index: int | None) -> list[dict]:
    """The chosen search results: ``--item N`` (0-based), the only result, or a pick in the project's usual results table."""
    if item_index is not None:
        if not 0 <= item_index < len(results):
            raise RemoteError(f"--item {item_index} is out of range (0-{len(results) - 1})")
        return [results[item_index]]
    if len(results) == 1:
        return results

    from VibraVid.services._base import EntriesManager
    from VibraVid.services._base.site_search_manager import get_select_title
    from VibraVid.utils import TVShowManager

    manager = EntriesManager()
    by_entry = {}
    for row in results:
        entry = _service_entry(row)
        manager.media_list.append(entry)
        by_entry[id(entry)] = row

    selected = get_select_title(TVShowManager(), manager)
    if not selected:
        raise RemoteError("selection cancelled")
    return [by_entry[id(entry)] for entry in selected]


def _server_name_from_argv(argv: list[str]) -> tuple[bool, str | None]:
    probe = argparse.ArgumentParser(add_help=False, exit_on_error=False)
    probe.add_argument("--server", nargs="?", const="", default=None)
    try:
        known, _ = probe.parse_known_args(argv)
    except (argparse.ArgumentError, SystemExit):
        return False, None
    return known.server is not None, (known.server or None)


def add_remote_site_options(parser: argparse.ArgumentParser, argv: list[str], site: str | None) -> list[str]:
    """Teach *parser* the command-line options of the service on the server (``--url``, ``--store``...)."""
    _remote_option_dests.clear()
    wanted, name = _server_name_from_argv(argv)
    if not wanted or not site or site.isdigit():
        return []
    try:
        info = RemoteClient(pick_remote_server(name), timeout=30).site_info(site)
    except (RemoteError, LookupError) as exc:
        console.print(f"[yellow]Could not list the options of '{site}' on the server: {exc}")
        return []

    group = parser.add_argument_group(f"{site} options (remote server)")
    for opt in info.get("options") or []:
        flag, dest, kind = opt.get("flag"), opt.get("dest"), opt.get("kind")
        if not flag or not dest or flag in parser._option_string_actions:
            continue

        help_text = opt.get("help") or ""
        if kind == "flag":
            group.add_argument(flag, dest=dest, action="store_true", default=False, help=help_text)
        elif kind == "int":
            group.add_argument(flag, dest=dest, type=int, default=opt.get("default"), help=help_text)
        elif kind == "choice":
            group.add_argument(flag, dest=dest, choices=opt.get("choices") or None, default=opt.get("default"), help=help_text)
        else:
            group.add_argument(flag, dest=dest, default=opt.get("default"), metavar=dest.upper(), help=help_text)
        _remote_option_dests.append(dest)
    return list(_remote_option_dests)


def _site_options_from_args(args) -> dict:
    """What the user typed for the service: its remote options, then ``--server-opt KEY=VALUE`` on top."""
    options = {}
    for dest in _remote_option_dests:
        value = getattr(args, dest, None)
        if value not in (None, False, ""):
            options[dest] = value
    return {**(context_tracker.site_options or {}), **options, **parse_server_options(getattr(args, "server_opt", None))}


def _login_data() -> dict:
    from VibraVid.utils import config_manager

    return config_manager.login._config_dict


def _store_login_update(updates: dict) -> None:
    """The server rotated tokens for this account: keep the new ones, or the stored copy stops working."""
    from VibraVid.utils import config_manager

    if apply_updates(_login_data(), updates):
        config_manager.save_login()
        console.print(f"[dim]Login updated by the server: {', '.join(sorted(updates))}")


def make_client(server: RemoteServer) -> RemoteClient:
    return RemoteClient(server, login_data=_login_data(), on_login_update=_store_login_update, local_cache=LocalCache())


def output_path_for(media: ResolvedMedia, site: str) -> str:
    """The client's own output path for *media*, built from its local OUTPUT/naming config."""
    from VibraVid.services._base import movie_folder, series_folder
    from VibraVid.services._base.site_loader import current_site_var
    from VibraVid.services._base.tv_display_manager import map_episode_path, map_movie_path
    from VibraVid.utils import config_manager

    extension = config_manager.config.get("PROCESS", "extension")
    token = current_site_var.set(site)
    try:
        song = (media.post_process or {}).get("args") if (media.post_process or {}).get("kind") == "song" else None
        if song:
            from VibraVid.services._base import music_folder
            from VibraVid.services._base.tv_display_manager import map_song_path

            components, filename = map_song_path(
                artist=song.get("artist"), album=song.get("album"), title=song.get("title"),
                year=song.get("year"), track_number=song.get("track_number"),
            )
            return os.path.join(music_folder(*components), filename)
        if media.media_type == "Film" or not (media.season or media.episode):
            components, filename = map_movie_path(media.title, media.year)
            folder = movie_folder(*components)
        else:
            components, filename = map_episode_path(media.title, media.year, media.season, media.episode, media.episode_name)
            folder = series_folder(*components)
    finally:
        current_site_var.reset(token)
    return os.path.join(folder, f"{filename}.{extension}")


def _prepare_context(media: ResolvedMedia, site: str) -> None:
    context_tracker.site_name = site
    context_tracker.title = media.title
    context_tracker.media_type = media.media_type or "Film"
    context_tracker.season = media.season
    context_tracker.episode = media.episode
    context_tracker.episode_name = media.episode_name


def download_resolved(client: RemoteClient, use_server_cdm: bool, session_id: str, index: int, media: ResolvedMedia, site: str) -> bool:
    """Download one resolved item locally; licenses and refreshes go back through the server."""
    from VibraVid.core.downloader.base import DownloadResult
    from VibraVid.services._base.site_loader import current_site_var

    path = output_path_for(media, site)
    _prepare_context(media, site)
    site_token = current_site_var.set(site)
    try:
        if media.kind == "mp4":
            return download_media(media, path).ok

        key = None
        license_fn = None
        if use_server_cdm and media.has_drm:
            key = client.keys(session_id, index)
            if not key:
                raise RemoteError("the server could not get any decryption key for this title")
            console.print(f"[green]Server CDM returned {len(key)} key(s)")
        elif media.license_proxy or media.license_url:
            license_fn = make_license_request_fn(client, session_id, index, media.drm_preference)

        refresh_fn = make_manifest_refresh_fn(client, session_id, index) if media.manifest_refresh else None
        downloader = build_downloader(media, path, key=key, license_request_fn=license_fn, manifest_refresh_fn=refresh_fn)
        result = DownloadResult.from_raw(downloader.start())
        if not result.ok:
            console.print(f"[red]Download failed: {result.error or 'stopped'}")
            return False
        return _post_process(media, result.path)
    finally:
        current_site_var.reset(site_token)


class RemoteSeries:
    def __init__(self, name: str, year, seasons: list[dict]):
        from VibraVid.services._base.object import Season, SeasonManager

        self.series_name = name
        self.year = year
        self.seasons_manager = SeasonManager()
        self._episodes: dict = {}
        for season in seasons:
            number = season["number"]
            self.seasons_manager.add(Season(number=number, name=season.get("name") or f"Season {number}"))
            self._episodes[number] = [dict(ep) for ep in season.get("episodes") or []]

    def getNumberSeason(self) -> int:
        return len(self.seasons_manager)

    def getEpisodeSeasons(self, number) -> list[dict]:
        return self._episodes.get(number, [])

    def position_of_season(self, number) -> int:
        return [s.number for s in self.seasons_manager.seasons].index(number) + 1


class Remote:
    def __init__(self, client: RemoteClient, site: str, options: dict, use_server_cdm: bool):
        self.client = client
        self.site = site
        self.options = options
        self.use_server_cdm = use_server_cdm

    def fetch(self, payload: dict, season: str | None, episode: str | None, naming: dict):
        """Resolve one title/episode on the server and download it here. Returns the usual ``DownloadResult``."""
        from VibraVid.core.downloader.base import DownloadResult

        session_id = None
        try:
            session_id, items = self.client.resolve(self.site, payload, season, episode, self.options)
            ok = True
            for index, media in enumerate(items):
                for field, value in naming.items():
                    setattr(media, field, value)
                ok = download_resolved(self.client, self.use_server_cdm, session_id, index, media, self.site) and ok
            return DownloadResult("", False, None) if ok else DownloadResult(None, True, "download failed")
        except RemoteError as exc:
            console.print(f"[red]{exc}")
            return DownloadResult(None, True, str(exc))
        finally:
            if session_id:
                self.client.close(session_id)


def _artwork_for(payload: dict, is_movie: bool) -> None:
    """The poster lookups the local flow does before a download (best effort: the client's own TMDB key)."""
    from VibraVid.services._base import tmdb_artwork

    keys = {"tmdb_id": payload.get("tmdb_id"), "name": payload.get("name"), "slug": payload.get("slug"), "year": payload.get("year")}
    try:
        if is_movie:
            context_tracker.poster_url = tmdb_artwork.resolve_movie_poster_url(**keys)
        else:
            context_tracker.series_tmdb_id = tmdb_artwork.resolve_series_tmdb_id(**keys)
    except Exception:  # noqa: BLE001 - artwork is optional
        logger.debug("artwork lookup failed", exc_info=True)


def download_title(remote: Remote, row: dict, args) -> bool:
    """Download one chosen title (a film, or the seasons/episodes picked the usual way)."""
    from VibraVid.core.downloader.base import DownloadCancelled, DownloadResult
    from VibraVid.services._base.tv_download_manager import process_episode_download, process_season_selection

    payload = row.get("payload") or {}
    payload.setdefault("is_movie", bool(row.get("is_movie")))
    name, year = payload.get("name") or row.get("title"), payload.get("year") or row.get("year")
    console.print(f"[cyan]Selected: [green]{name} [dim]({year or '-'})")
    context_tracker.title = name
    _artwork_for(payload, bool(payload["is_movie"]))

    if payload["is_movie"]:
        context_tracker.media_type = "Film"
        return DownloadResult.from_raw(remote.fetch(payload, None, None, {"title": name, "year": year, "media_type": "Film"})).ok

    seasons = remote.client.seasons(remote.site, payload, remote.options)
    if not seasons:
        raise RemoteError("the server found no seasons for this title")
    series = RemoteSeries(name, year, seasons)
    context_tracker.media_type = "Serie"

    def download_episode(ep_obj, season_number, episode_number):
        episode_position = next((i for i, ep in enumerate(series.getEpisodeSeasons(season_number), 1) if ep is ep_obj), episode_number)
        naming = {
            "title": name, "year": year, "media_type": "Serie",
            "season": season_number, "episode": episode_number, "episode_name": ep_obj.get("name"),
        }
        return remote.fetch(payload, str(series.position_of_season(season_number)), str(episode_position), naming)

    def season_callback(season_number, download_all, episode_selection=None):
        process_episode_download(
            index_season_selected=season_number,
            scrape_serie=series,
            download_video_callback=download_episode,
            download_all=download_all,
            episode_selection=episode_selection,
        )

    try:
        process_season_selection(series, len(series.seasons_manager), args.season, args.episode, season_callback)
    except (RuntimeError, DownloadCancelled) as exc:  # every episode of a season failed / the user cancelled
        console.print(f"[red]{exc}")
        return False
    return True


def _ask_locally(spec: dict) -> str:
    """Ask the user a question the service asked on the server, the way rich would have, and return the answer as text."""
    from rich.prompt import Confirm, FloatPrompt, IntPrompt

    if spec.get("error"):
        console.print(f"[red]{spec['error']}")
    kwargs = {
        "password": bool(spec.get("password")),
        "choices": spec.get("choices"),
        "case_sensitive": spec.get("case_sensitive", True),
        "show_default": spec.get("show_default", True),
        "show_choices": spec.get("show_choices", True),
    }
    if spec.get("has_default"):
        kwargs["default"] = spec.get("default")
    prompt_class = {"confirm": Confirm, "int": IntPrompt, "float": FloatPrompt}.get(spec.get("kind"), Prompt)
    value = prompt_class.ask(spec["prompt"], **kwargs)
    return ("y" if value else "n") if spec.get("kind") == "confirm" else str(value)


def run_interactive(client: RemoteClient, site: str, options: dict) -> bool:
    """Run a service's interactive step (``--login``/``--logout``) that lives on the server."""
    from rich.text import Text

    from VibraVid.utils import config_manager

    job_id = client.login_start(site, options)
    asked: set = set()
    state: dict = {}
    try:
        while True:
            state = client.login_poll(job_id)
            for chunk in state.get("output") or []:
                server_path = state.get("login_file_path")
                if server_path:  # "saved to <path>": the path of the server's file means nothing here
                    chunk = chunk.replace(server_path, str(config_manager.login_file_path))
                console.print(Text.from_ansi(chunk), end="", soft_wrap=True)
            prompt = state.get("prompt")
            if prompt and prompt.get("seq") not in asked:
                asked.add(prompt.get("seq"))
                client.login_answer(job_id, _ask_locally(prompt))
            if state.get("done"):
                break
    except (KeyboardInterrupt, EOFError):  # Ctrl+C, or no terminal to answer on
        client.login_abort(job_id)
        raise RemoteError("cancelled") from None
    if state.get("error"):
        raise RemoteError(state["error"])
    return state.get("result") is not False


def _post_process(media: ResolvedMedia, path: str | None) -> bool:
    """What the service does once the file exists (a song: tags, cover, container fix, conversion), run here on the real file."""
    step = media.post_process or {}
    if step.get("kind") != "song" or not path:
        return True
    from VibraVid.core.muxing.helper.audio import process_song

    try:
        return bool(process_song(file_path=str(path), **step.get("args", {})))
    except Exception as exc:  # noqa: BLE001 - the audio is downloaded; say what failed
        console.print(f"[red]Post-processing failed: {exc}")
        logger.exception("post-processing failed")
        return False


def run_remote(args) -> bool:
    server: RemoteServer = pick_remote_server(args.server or None)
    client = make_client(server)

    info = client.sites()
    console.print(f"[cyan]Remote server [green]{server.name}[cyan] ({server.url})")

    site = _resolve_site_name(args.site)
    if site not in info.get("sites", []):
        raise RemoteError(f"site '{site}' is not available on this server (available: {', '.join(info.get('sites', [])) or 'none'})")
    client.site_info(site)

    options = _site_options_from_args(args)
    if options.get("csv"):
        raise RemoteError("--csv reads a file on this machine, which the server cannot see: run it without --server (the service must be installed here)")
    if options.get("login") or options.get("logout"):
        return run_interactive(client, site, options)
    
    query = args.search or options.get("url") or options.get("direct_id")
    if not query:
        query = Prompt.ask(f"[purple]Insert a word to search in [green]{site}").strip()

    if not query:
        raise RemoteError("a search term (-s) or a title URL (--url) is required")
    
    results = client.search(site, query, options)
    if not results:
        raise RemoteError(f"no result for '{query}' on {site}")

    use_server_cdm = bool(server.server_cdm and info.get("server_cdm"))
    if server.server_cdm and not info.get("server_cdm"):
        console.print("[yellow]server_cdm is enabled in your config but this API key has no server_cdm grant: using your local CDM.")

    remote = Remote(client, site, options, use_server_cdm)
    ok = True
    for row in select_results(results, args.item):
        ok = download_title(remote, row, args) and ok
    return ok


def _resolve_site_name(site: str | None) -> str:
    if not site:
        raise RemoteError("-i SITE is required with --server")
    if site.isdigit():
        from VibraVid.services._base.site_loader import load_search_functions

        for func in load_search_functions().values():
            if str(func.indice) == site:
                return func.module_name
        raise RemoteError(f"no site with index {site} here: use the site name with --server")
    return site


def handle_remote_download(args) -> tuple[bool, bool]:
    """CLI entry: ``(handled, success)``. Not handled unless ``--server`` was given."""
    if getattr(args, "server", None) is None:
        return False, False
    try:
        return True, run_remote(args)
    except (RemoteError, LookupError, ValueError) as exc:
        console.print(f"[red]{exc}")
        logger.error(f"remote download failed: {exc}")
        return True, False
