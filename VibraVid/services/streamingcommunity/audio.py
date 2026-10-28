"""Plan requested audio across labelled renditions and single-language catalog videos."""

from VibraVid.core.manifest.m3u8 import HLSParser
from VibraVid.core.utils.audio_selection import language_code, matches_audio_language, requested_audio_languages


def inspect_manifest(playlist: str, headers: dict, catalog: str) -> dict:
    parser = HLSParser(playlist, headers)
    if not parser.fetch_manifest():
        raise ValueError("Unable to inspect the provider's video and audio tracks")
    streams = parser.parse_streams()
    if not streams:
        raise ValueError("The provider response did not contain playable tracks")
    audio = [stream for stream in streams if stream.type == "audio"]
    embedded = None
    if not audio and any(stream.type == "video" and any(codec in (stream.codecs or "").lower()
                         for codec in ("mp4a", "ac-3", "ec-3", "opus")) for stream in streams):
        embedded = catalog
    languages = list(dict.fromkeys(code for stream in audio
                     if (code := language_code(stream.resolved_language or stream.language))))
    return {"streams": streams, "content": parser.raw_content, "embedded": embedded,
            "languages": languages or ([catalog] if embedded else [])}


def download_audio_options(playlist: str, headers: dict, catalog: str, catalogs: list[str], resolve) -> dict:
    primary = inspect_manifest(playlist, headers, catalog)
    wanted = requested_audio_languages()
    available = list(primary["languages"])
    extras = []
    for language in catalogs:
        if not any(matches_audio_language([language], code) for code in wanted):
            continue
        if available and any(matches_audio_language(available, code) for code in wanted if matches_audio_language([language], code)):
            continue
        extra_url, extra_headers = resolve(language)
        extra = inspect_manifest(extra_url, extra_headers, language)
        candidates = [stream for stream in extra["streams"] if stream.type == "audio" and
                      matches_audio_language([language_code(stream.resolved_language or stream.language)], language)]
        if candidates:
            track = max(candidates, key=lambda stream: stream.bitrate or 0)
        elif extra["embedded"]:
            track = min((stream for stream in extra["streams"] if stream.type == "video"),
                        key=lambda stream: stream.bitrate or stream.height or 0)
        else:
            continue
        extras.append({"type": "audio", "language": language, "url": track.playlist_url or extra_url,
                       "headers": extra_headers, "single_audio": True})
        available.append(language)
    if wanted and (not available or not any(matches_audio_language(available, code) for code in wanted)):
        raise ValueError("The selected audio language is unavailable for this video")
    return {"m3u8_content": primary["content"], "embedded_audio_language": primary["embedded"],
            "other_tracks": extras, "required_audio_languages": [language for language in available
                              if not wanted or any(matches_audio_language([language], code) for code in wanted)]}
