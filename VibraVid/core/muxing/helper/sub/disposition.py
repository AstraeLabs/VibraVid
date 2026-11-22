# 15.09.26

from dataclasses import dataclass

from VibraVid.core.utils.language import extract_lang_and_flags, resolve_iso639_1
from VibraVid.core.utils.subtitle_flags import SubFlags, flags_satisfy, split_lang_flags
from VibraVid.utils import config_manager


@dataclass(frozen=True)
class SubtitleDispositionInfo:
    language: str
    forced: bool = False
    sdh: bool = False
    cc: bool = False
    hi: bool = False

    @classmethod
    def from_track(cls, track: dict) -> "SubtitleDispositionInfo":
        """From a muxer subtitle dict (``language`` plus the optional ``forced`` / ``sdh`` / ``cc`` / ``hi`` booleans)."""
        flags = SubFlags.from_track(track)
        return cls(language=track.get("language", ""), forced=flags.forced, sdh=flags.sdh, cc=flags.cc, hi=flags.hi)

    @property
    def is_forced(self) -> bool:
        return self.forced or "forced" in split_lang_flags(self.language)[1]

    @property
    def is_hearing_impaired(self) -> bool:
        """cc, sdh and hi all mean "for the deaf / hard of hearing" and share the muxer's single hearing_impaired disposition."""
        return self.sdh or self.cc or self.hi or bool(split_lang_flags(self.language)[1] & {"cc", "sdh", "hi"})


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

    return flags_satisfy(sub_flags, cfg_flags)


def find_default_subtitle(tracks: list[SubtitleDispositionInfo], config_lang_raw: str = "") -> int | None:
    """Index of the first track matching PROCESS.subtitle_disposition_language (e.g. "ita_forced"), if any."""
    config_lang = (config_lang_raw or "").lower().strip()
    if not config_lang:
        return None
    for idx, t in enumerate(tracks):
        if disposition_lang_matches(t.language, config_lang, {"forced": t.forced, "sdh": t.sdh, "cc": t.cc, "hi": t.hi}):
            return idx
    return None


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
        if t.is_forced or t.is_hearing_impaired:
            disp_parts = []
            if t.is_forced:
                disp_parts.append("forced")
            if t.is_hearing_impaired:
                disp_parts.append("hearing_impaired")
            args += [f"-disposition:s:{idx}", "+".join(disp_parts)]

    default_idx = find_default_subtitle(tracks, config_lang_raw)
    if default_idx is not None:
        config_lang = config_lang_raw.lower().strip()
        disp = "default"
        if "forced" in split_lang_flags(config_lang)[1]:
            disp += "+forced"
        if split_lang_flags(config_lang)[1] & {"cc", "sdh", "hi"}:
            disp += "+hearing_impaired"
        args += [f"-disposition:s:{default_idx}", disp]

    return args
