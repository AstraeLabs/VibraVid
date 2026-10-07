# 06.06.25

import json
import logging

from django.contrib import messages
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import redirect
from django.views.decorators.http import require_http_methods
from searchapp.api import get_api
from searchapp.api.base import Entries

from ..forms import DownloadForm
from ._shared import _run_download_in_thread

logger = logging.getLogger(__name__)


@require_http_methods(["POST"])
def series_metadata(request: HttpRequest) -> JsonResponse:
    """Return series metadata including seasons and episode counts."""
    try:
        # Parse request
        if request.content_type and "application/json" in request.content_type:
            body = json.loads(request.body.decode("utf-8"))
            source_alias = body.get("source_alias") or body.get("site")
            item_payload = body.get("item_payload") or {}
        else:
            source_alias = request.POST.get("source_alias") or request.POST.get("site")
            item_payload_raw = request.POST.get("item_payload")
            item_payload = json.loads(item_payload_raw) if item_payload_raw else {}

        if not source_alias or not item_payload:
            return JsonResponse({"error": "Parametri mancanti"}, status=400)

        # Get API instance
        api = get_api(source_alias)

        # Convert to Entries
        entries_fields = {k: v for k, v in item_payload.items() if k in Entries.__dataclass_fields__}
        media_item = Entries(**entries_fields)

        # Check if it's a movie
        if media_item.is_movie:
            return JsonResponse({
                "isSeries": False,
                "seasonsCount": 0,
                "episodesPerSeason": {}
            })

        # Get series metadata
        seasons = api.get_series_metadata(media_item)

        if not seasons:
            return JsonResponse({
                "isSeries": False,
                "seasonsCount": 0,
                "episodesPerSeason": {}
            })

        # Build response
        episodes_per_season = {
            season.number: season.episode_count
            for season in seasons
        }

        return JsonResponse({
            "isSeries": True,
            "seasonsCount": len(seasons),
            "episodesPerSeason": episodes_per_season
        })

    except Exception as e:
        return JsonResponse({"Error get metadata": str(e)}, status=500)


@require_http_methods(["POST"])
def start_download(request: HttpRequest) -> HttpResponse:
    """Handle download requests for movies or individual series selections."""
    form = DownloadForm(request.POST)
    if not form.is_valid():
        error_msg = f"Invalid data: {form.errors.as_text()}"
        logger.error(error_msg)
        messages.error(request, error_msg)
        return redirect("search_home")

    source_alias = form.cleaned_data["source_alias"]
    item_payload_raw = form.cleaned_data["item_payload"]
    season = form.cleaned_data.get("season") or None
    episode = form.cleaned_data.get("episode") or None
    audio_format = form.cleaned_data.get("audio_format") or None

    # Normalize
    if season:
        season = str(season).strip() or None
    if episode:
        episode = str(episode).strip() or None
    if audio_format:
        audio_format = str(audio_format).strip().lower() or None

    try:
        item_payload = json.loads(item_payload_raw)
    except (ValueError, TypeError):
        messages.error(request, "Invalid payload")
        return redirect("search_home")

    # Determine media type
    item_type = str(item_payload.get("type") or "").lower()
    if item_type in ("song", "track", "music"):
        media_type = "Musica"
    elif item_type == "album":
        media_type = "Album"
    elif item_payload.get("is_movie"):
        media_type = "Film"
    else:
        media_type = "Serie"

    # Check for series episode selection
    if media_type == "Serie" and season and not episode:
        messages.error(request, "Select at least one episode before downloading!")

    quality = form.cleaned_data.get("quality") or None

    # Run download
    _run_download_in_thread(source_alias, item_payload, season, episode, media_type, audio_format=audio_format, quality=quality)
    return redirect("download_dashboard")

@require_http_methods(["POST"])
def available_qualities(request: HttpRequest) -> JsonResponse:
    """Inspect one exact provider video, without starting a download."""
    try:
        try:
            data = json.loads(request.body)
            payload = data.get("item_payload") or {}
            if isinstance(payload, str):
                payload = json.loads(payload)
            missing = [f for f in ("name", "type") if f not in payload]
        except (ValueError, AttributeError, TypeError):
            return JsonResponse({"qualities": [], "message": "Invalid request body."}, status=400)
        if missing:
            return JsonResponse({"qualities": [], "message": f"Missing item_payload field(s): {', '.join(missing)}."}, status=400)

        item = Entries(**{k: v for k, v in payload.items() if k in Entries.__dataclass_fields__})
        api = get_api(data.get("source_alias") or "")
        season = episode = None
        description = item.name
        if not item.is_movie:
            import re
            seasons = api.get_series_metadata(item) or []
            season_match = re.match(r"\d+", str(data.get("season") or ""))
            season = int(season_match.group()) if season_match else (seasons[0].number if seasons else None)
            selected = next((s for s in seasons if int(s.number) == season), None)
            episode_match = re.match(r"\d+", str(data.get("episode") or ""))
            episode = int(episode_match.group()) if episode_match else (
                selected.episodes[0].number if selected and selected.episodes else None
            )
            if season is None or episode is None:
                return JsonResponse({"qualities": [], "message": "No episode is available yet."})
            description += f" S{season} E{episode}"
        from VibraVid.core.utils.quality import normalize_quality
        qualities = set()
        for candidate in api.get_available_qualities(item, season, episode):
            try:
                quality = normalize_quality(candidate)
            except ValueError:
                continue
            if quality and quality != "360p":
                qualities.add(quality)
        return JsonResponse({"qualities": sorted(qualities, key=lambda q: int(q[:-1]), reverse=True),
                             "sample": description,
                             "message": "For batches, availability is checked again for each episode." if season is not None else ""})
    except NotImplementedError as exc:
        return JsonResponse({"qualities": [], "supported": False, "message": str(exc)})
    except Exception:
        logger.exception("Unable to discover provider qualities")
        return JsonResponse({"qualities": [], "message": "Unable to check qualities right now. You can use config or retry."}, status=502)


__all__ = ['series_metadata', 'start_download', 'available_qualities']
