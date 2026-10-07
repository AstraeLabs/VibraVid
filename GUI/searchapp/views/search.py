# 06.06.25

import concurrent.futures
import json
import logging
from typing import Any

from django.contrib import messages
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import redirect, render
from django.views.decorators.http import require_http_methods
from searchapp.api import get_api

from VibraVid.provider.tmdb import tmdb_client
from VibraVid.services._base import tmdb_artwork

from ..forms import DownloadForm, SearchForm
from ._shared import _known_series_tmdb_id, _media_item_to_display_dict, _resolve_global_sites, _run_global_search

logger = logging.getLogger(__name__)


@require_http_methods(["GET", "POST"])
def search(request: HttpRequest) -> HttpResponse:
    """Handle search requests."""
    def _back_to_search(form: SearchForm) -> HttpResponse:
        return redirect("search_page")

    if request.method == "POST":
        form = SearchForm(request.POST)
    else:
        query = request.GET.get('query')
        site = request.GET.get('site')
        if query and site:
            form = SearchForm({'query': query, 'site': site})
        else:
            return redirect("search_home")

    if not form.is_valid():
        messages.error(request, "Invalid data")
        return _back_to_search(form)

    site = form.cleaned_data["site"]
    query = form.cleaned_data["query"]

    # Global / per-category search across multiple services.
    global_sites = _resolve_global_sites(site)
    if global_sites is not None:
        results, failed = _run_global_search(query, global_sites)
        if failed:
            messages.warning(request, f"Some sources didn't respond: {', '.join(failed)}")
        return render(
            request,
            "searchapp/cinema_results.html",
            {
                "form": SearchForm(initial={"site": site, "query": query}),
                "query": query,
                "download_form": DownloadForm(),
                "results": results,
                "selected_site": site,
                "is_global": True,
                "tmdb_available": bool(tmdb_client.api_key),
                "nav_active": "cerca",
            },
        )

    try:
        api = get_api(site)
        media_items = api.search(query)
        results = [_media_item_to_display_dict(item, site) for item in media_items]
    except Exception as e:
        messages.error(request, f"Search error: {e}")
        return _back_to_search(form)

    download_form = DownloadForm()
    return render(
        request,
        "searchapp/cinema_results.html",
        {
            "form": SearchForm(initial={"site": site, "query": query}),
            "query": query,
            "download_form": download_form,
            "results": results,
            "selected_site": site,
            "tmdb_available": bool(tmdb_client.api_key),
            "nav_active": "cerca",
        },
    )


def _series_identity(item: dict[str, Any]) -> tuple:
    """Return a tuple that uniquely identifies a series for TMDB lookup."""
    return (
        str(item.get("tmdb_id") or ""), str(item.get("name") or ""),
        str(item.get("slug") or ""), str(item.get("year") or ""), str(item.get("site") or ""),
    )


def _resolve_episode_artwork(items: list[dict[str, Any]]) -> tuple[dict[Any, str], dict[Any, dict]]:
    """Resolve TMDB stills and episode info for a batch of episode items."""
    groups: dict[tuple, list[dict[str, Any]]] = {}
    for item in items:
        groups.setdefault(_series_identity(item), []).append(item)

    art: dict[Any, str] = {}
    info: dict[Any, dict] = {}
    for group in groups.values():
        head = group[0]
        series_tmdb_id = _known_series_tmdb_id(head.get("tmdb_id"))
        if series_tmdb_id is None:
            try:
                series_tmdb_id = tmdb_artwork.resolve_series_tmdb_id(
                    name=head.get("name"), slug=head.get("slug"),
                    year=head.get("year"), site_name=head.get("site"),
                )
            except (KeyError, TypeError, ValueError, AttributeError):
                logger.debug("[resolve_tmdb_posters] series lookup failed for %s", head.get("name"), exc_info=True)
                continue

        if not series_tmdb_id:
            continue

        by_season: dict[int, list[tuple[dict[str, Any], int]]] = {}
        for item in group:
            try:
                site_season = int(item.get("season"))
                site_episode = int(item.get("episode"))
            except (TypeError, ValueError):
                logger.debug("[resolve_tmdb_posters] unusable season/episode for index=%s", item.get("index"))
                continue
            try:
                real_season, real_episode = tmdb_client.resolve_actual_season_episode(
                    series_tmdb_id, site_season, site_episode
                )
            except (KeyError, TypeError, ValueError, AttributeError):
                logger.debug("[resolve_tmdb_posters] season/episode remap failed for S%sE%s", site_season, site_episode, exc_info=True,)
                real_season, real_episode = site_season, site_episode
            by_season.setdefault(real_season, []).append((item, real_episode))

        if not by_season:
            continue

        def _season(entry: tuple[int, list[tuple[dict[str, Any], int]]], series_id: int = series_tmdb_id):
            season_number, rows = entry
            try:
                stills, episode_info, fallback = tmdb_artwork.resolve_season_artwork(series_id, season_number)
            except (KeyError, TypeError, ValueError, AttributeError):
                logger.debug("[resolve_tmdb_posters] season lookup failed for S%s", season_number, exc_info=True)
                return {}, {}
            
            found: dict[Any, str] = {}
            found_info: dict[Any, dict] = {}
            for row, episode_number in rows:
                url = stills.get(episode_number) or fallback
                if url:
                    found[row.get("index")] = url
                if episode_number in episode_info:
                    found_info[row.get("index")] = episode_info[episode_number]
            return found, found_info

        with concurrent.futures.ThreadPoolExecutor(max_workers=min(len(by_season), 8)) as executor:
            for found, found_info in executor.map(_season, by_season.items()):
                art.update(found)
                info.update(found_info)

    return art, info


def resolve_tmdb_posters(request: HttpRequest) -> JsonResponse:
    """Resolve TMDB posters and episode info for a batch of items."""
    if request.method != "POST":
        return JsonResponse({"status": "error", "message": "Method not allowed"}, status=405)

    try:
        items = json.loads(request.body).get("items", [])
    except (ValueError, TypeError) as e:
        return JsonResponse({"status": "error", "message": str(e)}, status=400)


    items = [i for i in items if isinstance(i, dict)] if isinstance(items, list) else []
    def _is_episode(item: dict[str, Any]) -> bool:
        return item.get("season") not in (None, "") and item.get("episode") not in (None, "")

    episodes = [i for i in items if _is_episode(i)]
    covers = [i for i in items if not _is_episode(i)]

    def _resolve(item: dict[str, Any]):
        index = item.get("index")
        try:
            if item.get("backdrop"):
                poster = tmdb_artwork.resolve_backdrop_url(
                    tmdb_id=item.get("tmdb_id"), name=item.get("name"),
                    slug=item.get("slug"), year=item.get("year"),
                    is_movie=bool(item.get("is_movie")), site_name=item.get("site"),
                )
            elif item.get("is_movie"):
                poster = tmdb_artwork.resolve_movie_poster_url(
                    tmdb_id=item.get("tmdb_id"), name=item.get("name"),
                    slug=item.get("slug"), year=item.get("year"),
                )
            else:
                series_tmdb_id = tmdb_artwork.resolve_series_tmdb_id(
                    tmdb_id=item.get("tmdb_id"), name=item.get("name"),
                    slug=item.get("slug"), year=item.get("year"), site_name=item.get("site"),
                )
                poster = tmdb_artwork.resolve_series_poster_url(series_tmdb_id)
        except (KeyError, TypeError, ValueError, AttributeError):
            logger.debug("[resolve_tmdb_posters] lookup failed for item index=%s", index, exc_info=True)
            poster = None
        return index, poster

    posters: dict[Any, str] = {}
    if covers:
        with concurrent.futures.ThreadPoolExecutor(max_workers=min(len(covers), 8)) as executor:
            for index, poster in executor.map(_resolve, covers):
                if poster:
                    posters[index] = poster

    episode_info: dict[Any, dict] = {}
    if episodes:
        episode_art, episode_info = _resolve_episode_artwork(episodes)
        posters.update(episode_art)

    return JsonResponse({"posters": posters, "episode_info": episode_info})


__all__ = ['search', 'resolve_tmdb_posters']
