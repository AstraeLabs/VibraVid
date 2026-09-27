# 15.09.26

from dataclasses import dataclass

from VibraVid.core.utils.language import extract_lang_and_flags, resolve_iso639_1
from VibraVid.utils import config_manager


@dataclass(frozen=True)
class SubtitleDispositionInfo:
    language: str
    forced: bool = False
    sdh: bool = False
    cc: bool = False


def get_configured_disposition_language() -> str:
    """PROCESS.subtitle_disposition_language, normalized to a single string (config can return a list)."""
    val = config_manager.config.get("PROCESS", "subtitle_disposition_language")
    if isinstance(val, list):
        val = val[0] if val else ""
    return val or ""


def disposition_lang_matches(subtitle_lang_raw: str, config_lang_raw: str, track_info: dict | None = None) -> bool:
    if not subtitle_lang_raw or not config_lang_raw:
        return False

    sub_base, sub_flags = extract_lang_and_flags(subtitle_lang_raw, track_info)
    cfg_base, cfg_flags = extract_lang_and_flags(config_lang_raw)

    sub_iso2 = resolve_iso639_1(sub_base) or sub_base.split("-")[0].lower()
    cfg_iso2 = resolve_iso639_1(cfg_base) or cfg_base.split("-")[0].lower()
    if not sub_iso2 or sub_iso2 != cfg_iso2:
        return False

    return cfg_flags.issubset(sub_flags)


def build_subtitle_disposition_args(tracks: list[SubtitleDispositionInfo], config_lang_raw: str = "") -> list[str]:
    """Build the -disposition:s:N ffmpeg args for a set of subtitle tracks.

    Passo 1: reset every subtitle disposition to 0.
    Passo 2: auto-flags (forced / hearing_impaired) from explicit booleans + language-suffix fallback ("_forced" -> forced, "_cc"/"_sdh" -> hearing_impaired).
    Passo 3: config-driven default (PROCESS.subtitle_disposition_language, e.g. "ita_forced"), which overwrites the disposition of the first matching track.
    """
    args: list[str] = []

    for idx in range(len(tracks)):
        args += [f"-disposition:s:{idx}", "0"]

    for idx, t in enumerate(tracks):
        lang_lower = (t.language or "").lower()
        is_forced = "_forced" in lang_lower or t.forced
        is_hi = "_sdh" in lang_lower or "_cc" in lang_lower or t.sdh or t.cc
        if is_forced or is_hi:
            disp_parts = []
            if is_forced:
                disp_parts.append("forced")
            if is_hi:
                disp_parts.append("hearing_impaired")
            args += [f"-disposition:s:{idx}", "+".join(disp_parts)]

    config_lang = (config_lang_raw or "").lower().strip()
    if config_lang and tracks:
        for idx, t in enumerate(tracks):
            track_info = {"forced": t.forced, "sdh": t.sdh, "cc": t.cc}
            if disposition_lang_matches(t.language, config_lang, track_info):
                disp = "default"
                if "_forced" in config_lang or "-forced" in config_lang:
                    disp += "+forced"
                if "_sdh" in config_lang or "_cc" in config_lang or "-sdh" in config_lang or "-cc" in config_lang:
                    disp += "+hearing_impaired"
                args += [f"-disposition:s:{idx}", disp]
                break

    return args
