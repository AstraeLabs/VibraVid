# 27.11.26

from VibraVid.core.manifest.m3u8 import HLSParser
from VibraVid.core.ui.tracker import context_tracker
from VibraVid.core.utils.audio_selection import configured_audio_languages, language_code, matches_audio_language
from VibraVid.utils import config_manager

_DEFAULT_VIDEO_LANGUAGE = "ita"


def wanted_audio_languages() -> list[str] | None:
    """Requested audio languages in priority order: the GUI choice first, then ``DOWNLOAD.select_audio``. None = all."""
    raw = context_tracker.audio_filter or config_manager.config.get("DOWNLOAD", "select_audio", default="") or ""
    if str(raw).strip().lower() == "all":
        return None
    return [choice["code"] for choice in configured_audio_languages(raw)]


def lightest_rendition(master_playlist: str, headers: dict) -> str | None:
    """Playlist of the smallest video rendition: its embedded audio is the same as the full HD one."""
    parser = HLSParser(master_playlist, headers)
    if not parser.fetch_manifest():
        return None
    renditions = [stream for stream in parser.parse_streams() or [] if stream.type == "video"]
    if not renditions:
        return None
    lightest = min(renditions, key=lambda stream: stream.bitrate or stream.height or 0)
    return lightest.playlist_url or master_playlist


def plan_audio(languages: list[str], wanted: list[str] | None) -> tuple[str | None, list[str]]:
    """(language whose video is downloaded, languages that only add their audio) among the episode's *languages*."""
    if not languages:
        return None, []
    if wanted is None:
        chosen = list(languages)
    else:
        chosen = [lang for code in wanted for lang in languages if matches_audio_language([language_code(lang)], code)]
        chosen = list(dict.fromkeys(chosen))
    if not chosen:
        fallback = _DEFAULT_VIDEO_LANGUAGE if _DEFAULT_VIDEO_LANGUAGE in languages else languages[0]
        return fallback, []
    if wanted is None and _DEFAULT_VIDEO_LANGUAGE in chosen:
        chosen.remove(_DEFAULT_VIDEO_LANGUAGE)
        chosen.insert(0, _DEFAULT_VIDEO_LANGUAGE)
    return chosen[0], chosen[1:]
