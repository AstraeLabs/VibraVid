"""Audio language choices shared by the GUI and individual download workers."""

import re
from contextlib import contextmanager
from copy import copy
from types import SimpleNamespace

from VibraVid.core.ui.tracker import context_tracker
from VibraVid.core.utils.language import LANGUAGES, canonical_iso639_2, resolve_iso639_1, resolve_language_display_name
from VibraVid.core.utils.selector import FilterSpec, _matches_lang, split_audio_slots
from VibraVid.utils import config_manager


def language_code(value: str) -> str:
    value = str(value or "").strip()
    iso3 = canonical_iso639_2(value)
    if iso3 not in LANGUAGES or iso3 in {"und", "zxx", "mul"}:
        return ""
    base = resolve_iso639_1(value) or iso3
    region = re.fullmatch(r"[a-zA-Z]{2,3}[-_]([a-zA-Z]{2}|\d{3})", value)
    return f"{base}-{region[1].upper()}" if region else base


def configured_audio_languages(raw: str | None = None) -> list[dict[str, str]]:
    raw = config_manager.config.get("DOWNLOAD", "select_audio", default="") if raw is None else raw
    slots = split_audio_slots(raw)
    groups = [slots[number] for number in sorted(slots)] if slots else [FilterSpec.parse(raw, "audio").langs or ""]
    choices = {}
    for group in groups:
        for token in re.split(r"[|\s]+", group):
            code = language_code(token)
            if code and code not in choices:
                name = resolve_language_display_name(code)
                if "-" in code:
                    name += f" ({code.split('-', 1)[1]})"
                choices[code] = {"code": code, "name": name}
    return list(choices.values())


def normalize_audio_language(value: str | None) -> str | None:
    if value in (None, "", "config"):
        return None
    if value == "all":
        return "all"
    code = language_code(value)
    configured = configured_audio_languages()
    if code and (not configured or code in {choice["code"] for choice in configured}):
        return code
    raise ValueError("Choose an audio language from the configured languages.")


def audio_filter_for_selection(value: str | None) -> str | None:
    value = normalize_audio_language(value)
    if value is None:
        return None
    if value == "all":
        return "|".join(choice["code"] for choice in configured_audio_languages()) or "all"
    return value


def requested_audio_languages() -> list[str]:
    raw = context_tracker.audio_filter
    return [choice["code"] for choice in configured_audio_languages(raw)] if raw else []


def matches_audio_language(available: str | list[str] | None, selection: str | None) -> bool:
    """Unknown catalog language remains eligible; actual tracks are checked before download."""
    if not selection or selection == "all" or not available:
        return True
    available = re.split(r"[,|\s/]+", available) if isinstance(available, str) else available
    wanted = language_code(selection).lower()
    return any(language_code(code).lower() == wanted or
               ("-" not in wanted and language_code(code).lower().split("-")[0] == wanted)
               for code in available)


def enforce_audio_selection(streams: list, embedded_language: str | None = None) -> None:
    """An explicit GUI choice must never silently fall back to a different language."""
    requested = context_tracker.audio_filter
    if not requested or requested == "all" or context_tracker.manual_tracks:
        return
    selected = []
    for stream in streams:
        if getattr(stream, "type", "") == "audio" and getattr(stream, "selected", False):
            if _matches_lang(stream, requested):
                selected.append(stream)
            else:
                stream.selected = False
    if not selected:
        if embedded_language and _matches_lang(SimpleNamespace(language=embedded_language), requested):
            return
        names = ", ".join(resolve_language_display_name(code) for code in requested_audio_languages())
        raise ValueError(f"Requested audio is unavailable for this video: {names}. No other language was selected.")


def filter_series_audio(seasons: list, selection: str | None) -> list:
    requested = audio_filter_for_selection(selection)
    wanted = [choice["code"] for choice in configured_audio_languages(requested)] if requested else []
    if not wanted:
        return seasons
    result = []
    for season in seasons:
        filtered = copy(season)
        filtered.episodes = [episode for episode in season.episodes
                             if any(matches_audio_language(getattr(episode, "language", None), code) for code in wanted)]
        if filtered.episodes:
            result.append(filtered)
    return result


@contextmanager
def audio_language_context(value: str | None):
    previous = context_tracker.audio_filter
    context_tracker.audio_filter = audio_filter_for_selection(value)
    try:
        yield
    finally:
        context_tracker.audio_filter = previous
