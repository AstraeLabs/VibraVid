# 28.10.26

"""Order of the audio and subtitle tracks in the muxed file: one function for join_media (ffmpeg and mkvmerge), the hybrid mux, the streaming mux and the chunks pipe."""

from collections.abc import Callable
from typing import Any

from VibraVid.core.ui.tracker import context_tracker
from VibraVid.core.utils.language import (
    canonical_iso639_2,
    extract_lang_and_flags,
    resolve_locale,
    track_language_and_flags,
)
from VibraVid.core.utils.selector import filter_language_order
from VibraVid.utils import config_manager


def effective_select_filter(kind: str, custom_filters: dict | None = None) -> str:
    """The select_audio / select_subtitle value in force for *kind*: the explicit audio choice of the GUI (``context_tracker.audio_filter``) first, then the per-download filter, then DOWNLOAD.select_audio / select_subtitle."""
    if kind == "audio" and context_tracker.audio_filter:
        return context_tracker.audio_filter
    return (custom_filters or {}).get(kind) or config_manager.config.get("DOWNLOAD", f"select_{kind}", default="") or ""


def _same_language(token_base: str, track_base: str) -> bool:
    """A region-qualified token ("es-419") asks for that very locale, a bare one ("es", "spa") for the language whatever its region."""
    if "-" in token_base:
        return (resolve_locale(token_base) or token_base).lower() == (resolve_locale(track_base) or track_base).lower()
    token_iso = canonical_iso639_2(token_base)
    return token_iso != "und" and token_iso == canonical_iso639_2(track_base.split("-", 1)[0])


def order_tracks(items: list[Any], kind: str, select_filter: str | None = None, track_of: Callable[[Any], Any] = lambda item: item) -> list[Any]:
    """Sort audio or subtitle *items* by the language priority of their track (dicts or manifest streams; *track_of* picks the track out of an item such as a ``(stream, path)`` pair), the unmatched ones last, each group in its original order.

    The priority is the order of *select_filter*, the select_audio / select_subtitle the download ran with ("ita|eng", or "1ita|2eng" by slot number); a filter that names no language leaves *items* as they are. A token with flags ("ita_forced") ranks only the tracks that carry them.
    """
    requests = [extract_lang_and_flags(token.strip()) for token in filter_language_order(select_filter or "", kind) if token.strip()]
    if not items or not requests:
        return items

    def rank(item: Any) -> int:
        base, flags = track_language_and_flags(track_of(item))
        for position, (token_base, token_flags) in enumerate(requests):
            if token_flags <= flags and _same_language(token_base.lower(), base.lower()):
                return position
        return len(requests)

    return sorted(items, key=rank)
