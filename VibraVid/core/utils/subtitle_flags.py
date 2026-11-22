# 21.11.26

import re
from collections.abc import Iterable
from dataclasses import dataclass

FLAG_ORDER = ("forced", "cc", "sdh", "hi")
FLAG_WORDS = (*FLAG_ORDER, "default")

# sdh and hi are two spellings of "hearing impaired": a request for one accepts the other
_HEARING_GROUP = frozenset({"sdh", "hi"})

_NAME_PATTERNS = {
    "forced": re.compile(r"\[forced\]|\bforced\b", re.IGNORECASE),
    "cc": re.compile(r"\[cc\]|\bcc\b|closed[- _]?captions?", re.IGNORECASE),
    "sdh": re.compile(r"\[sdh\]|\bsdh\b", re.IGNORECASE),
    "hi": re.compile(r"\[hi\]|\bhi\b|hearing[- _]?impaired|hard[- _]of[- _]hearing|\bhoh\b", re.IGNORECASE),
}
_LANG_SPLIT = re.compile(r"[-_.]")

# Audio description (narration for the visually impaired). "AD" is also the region code of Andorra (ca-AD) and an
_AD_NAME = re.compile(r"\[ad\]|(?i:audio[- _]?descri\w*|descriptive|described[- _]video|descrizione[- _]audio|audiodescrizione)|\bAD\b")
_AD_HLS = "public.accessibility.describes-video"

_ROLE_FORCED = {"forced-subtitle", "forced_subtitle", "forced"}
_ROLE_CAPTION = {"caption", "captions"}
_HLS_TRANSCRIBES = "transcribes-spoken-dialog"
_HLS_DESCRIBES_SOUND = "describes-music-and-sound"


@dataclass(frozen=True)
class SubFlags:
    forced: bool = False
    cc: bool = False
    sdh: bool = False
    hi: bool = False

    @classmethod
    def from_names(cls, names: Iterable[str]) -> "SubFlags":
        found = {str(n).strip().lower() for n in names}
        return cls(forced="forced" in found, cc="cc" in found, sdh="sdh" in found, hi="hi" in found)

    @classmethod
    def from_stream(cls, s) -> "SubFlags":
        """Flags of a manifest ``Stream`` (or any object with forced / is_cc / is_sdh / is_hi)."""
        return cls(
            forced=bool(getattr(s, "forced", False)),
            cc=bool(getattr(s, "is_cc", False)),
            sdh=bool(getattr(s, "is_sdh", False)),
            hi=bool(getattr(s, "is_hi", False)),
        )

    @classmethod
    def from_track(cls, track: dict) -> "SubFlags":
        """Flags of a muxer/status track dict, which spells them ``cc`` / ``sdh`` / ``hi`` (``is_*`` also accepted)."""
        get = track.get
        return cls(
            forced=bool(get("forced")),
            cc=bool(get("cc") or get("is_cc")),
            sdh=bool(get("sdh") or get("is_sdh")),
            hi=bool(get("hi") or get("is_hi")),
        )

    def names(self) -> tuple[str, ...]:
        """Active flags in canonical order."""
        return tuple(f for f in FLAG_ORDER if getattr(self, f))

    def as_set(self) -> set[str]:
        return set(self.names())

    @property
    def hearing_impaired(self) -> bool:
        """Any flag meaning the track is meant for the deaf / hard of hearing (drives the ``hearing_impaired`` disposition)."""
        return self.cc or self.sdh or self.hi

    def suffix(self) -> str:
        """``_forced``, ``_forced_sdh``... or "" for a plain track."""
        return "".join(f"_{n}" for n in self.names())

    def __or__(self, other: "SubFlags") -> "SubFlags":
        return SubFlags(self.forced or other.forced, self.cc or other.cc, self.sdh or other.sdh, self.hi or other.hi)

    def __bool__(self) -> bool:
        return bool(self.names())


def split_lang_flags(raw: str) -> tuple[str, set[str]]:
    """Split ``en-us_cc`` / ``en-us.[CC]`` / ``ita-forced`` / ``ita_ad`` into (clean language, flag words incl. ``default`` and ``ad``).

    ``hi`` is also the code of Hindi: as the first token it is the language, not "hearing impaired".
    ``ad`` (audio description) counts only after an underscore: ``ca-ad`` is Catalan in Andorra.
    """
    clean: list[str] = []
    flags: set[str] = set()
    text = raw or ""
    start = 0
    n = 0
    for match in [*_LANG_SPLIT.finditer(text), None]:
        end = match.start() if match else len(text)
        part = text[start:end]
        sep = text[start - 1] if start else ""
        token = part.strip("[]").lower()
        if (token in FLAG_WORDS and not (n == 0 and token == "hi")) or (token == "ad" and n > 0 and sep == "_"):
            flags.add(token)
        else:
            clean.append(part)
        start = match.end() if match else end
        n += 1
    return "-".join(clean), flags


def classify(
    *,
    lang: str = "",
    name: str = "",
    forced: bool = False,
    cc: bool = False,
    sdh: bool = False,
    hi: bool = False,
    roles: Iterable[str] = (),
    accessibility: Iterable[tuple[str, str]] = (),
    characteristics: str = "",
    subtype: str = "",
) -> SubFlags:
    """Flags of a subtitle track from whatever raw signals its protocol offers.

    ``lang`` / ``name``: the language string (``it-forced``) and the human label ("English SDH") of any protocol.
    ``forced`` / ``cc`` / ``sdh`` / ``hi``: explicit attributes (HLS ``FORCED=YES``, custom JSON, ``CLOSED-CAPTIONS``).
    ``roles``: DASH ``<Role value>`` values.
    ``accessibility``: DASH ``<Accessibility>`` as (schemeIdUri, value) pairs.
    ``characteristics``: HLS ``CHARACTERISTICS`` (comma separated UTIs).
    ``subtype``: ISM ``StreamIndex@Subtype`` (SUBT, CAPT, FORC, DESC...).
    """
    found: set[str] = {n for n, on in (("forced", forced), ("cc", cc), ("sdh", sdh), ("hi", hi)) if on}

    found |= split_lang_flags(lang)[1] & set(FLAG_ORDER)

    for flag, pattern in _NAME_PATTERNS.items():
        if name and pattern.search(name):
            found.add(flag)

    for role in roles:
        value = (role or "").strip().lower()
        if value in _ROLE_FORCED:
            found.add("forced")
        elif value in _ROLE_CAPTION:
            found.add("cc")

    # Accessibility metadata is a generic "for the hard of hearing" hint: it names the variant (hi / sdh) only when the
    # language, the name or an explicit attribute has not already done so, otherwise one track would carry both spellings.
    hearing_hint: str = ""

    for scheme, value in accessibility:
        scheme, value = (scheme or "").lower(), (value or "").strip().lower()
        if "audiopurpose" in scheme:
            if value == "2":  # AudioPurposeCS: 1 = visually impaired, 2 = hard of hearing
                hearing_hint = hearing_hint or "hi"
        elif value in _ROLE_CAPTION:
            found.add("cc")
        elif value in ("hearing-impaired", "hearing_impaired"):
            hearing_hint = hearing_hint or "hi"
        elif value == "sdh":
            found.add("sdh")

    chars = (characteristics or "").lower()
    if _HLS_DESCRIBES_SOUND in chars or "hearing" in chars:
        # Apple defines SDH as transcribing the dialog AND describing music and sound
        hearing_hint = "sdh" if _HLS_TRANSCRIBES in chars else (hearing_hint or "hi")

    if hearing_hint and not (found & _HEARING_GROUP):
        found.add(hearing_hint)

    sub = (subtype or "").strip().upper()
    if sub == "FORC":
        found.add("forced")
    elif sub == "CAPT":
        found.add("cc")

    return SubFlags.from_names(found)


def is_audio_description(
    *,
    lang: str = "",
    name: str = "",
    ad: bool = False,
    roles: Iterable[str] = (),
    accessibility: Iterable[tuple[str, str]] = (),
    characteristics: str = "",
) -> bool:
    """Whether an audio track is an audio description (narration for the visually impaired), from the signals of any protocol.

    ``name``: the human label ("English Audio Description"). ``roles``: DASH ``<Role value>``. ``accessibility``: DASH
    ``<Accessibility>`` as (schemeIdUri, value). ``characteristics``: HLS ``CHARACTERISTICS``. ``lang``: ``ita_ad`` style suffix.
    """
    if ad or "ad" in split_lang_flags(lang)[1] or (name and _AD_NAME.search(name)):
        return True
    if any((r or "").strip().lower() == "description" for r in roles):
        return True
    for scheme, value in accessibility:
        scheme, value = (scheme or "").lower(), (value or "").strip().lower()
        if ("audiopurpose" in scheme and value == "1") or value == "description":  # AudioPurposeCS: 1 = visually impaired
            return True
    return _AD_HLS in (characteristics or "").lower()


def canonical_name(language: str, flags: SubFlags | Iterable[str] | None = None) -> str:
    """``ita`` + {forced, sdh} -> ``ita_forced_sdh``. Any flag already written in ``language`` is removed first, so the call is idempotent."""
    base, _ = split_lang_flags(language)
    if flags is None:
        flags = SubFlags()
    elif not isinstance(flags, SubFlags):
        flags = SubFlags.from_names(flags)
    return f"{base or 'und'}{flags.suffix()}"


def flags_satisfy(have: SubFlags | Iterable[str], wanted: Iterable[str]) -> bool:
    """Whether a track carrying ``have`` fulfils a request for ``wanted`` flags (sdh and hi are interchangeable)."""
    have_set = have.as_set() if isinstance(have, SubFlags) else {str(f).lower() for f in have}
    for flag in wanted:
        flag = str(flag).lower()
        if flag in _HEARING_GROUP:
            if not (have_set & _HEARING_GROUP):
                return False
        elif flag not in have_set:
            return False
    return True


_KIND_TO_FLAG = {
    "forced": "forced",
    "cc": "cc",
    "closed_captions": "cc",
    "closed_caption": "cc",
    "sdh": "sdh",
    "hearing_impaired": "hi",
    "hard_of_hearing": "hi",
    "hi": "hi",
}


def flags_from_kind(kind: str, *, forced: bool = False) -> SubFlags:
    """Flags of a track whose service labels it with a plain ``subtitle_type`` (``forced``, ``cc``, ``sdh``, ``hearing_impaired``...)."""
    normalized = (kind or "").strip().lower().replace("-", "_").replace(" ", "_")
    flag = _KIND_TO_FLAG.get(normalized)
    return SubFlags.from_names([flag] if flag else []) | SubFlags(forced=forced)


def external_subtitle_track(url: str, locale: str, fmt: str, label: str, flags: SubFlags) -> dict:
    """Track dict for an external subtitle: canonical language token plus every flag as its own boolean."""
    return {
        "type": "subtitle",
        "url": url,
        "language": canonical_name(locale, flags),
        "format": fmt,
        "extension": fmt,
        "name": " ".join([label, *flags.names()]),
        "forced": flags.forced,
        "sdh": flags.sdh,
        "cc": flags.cc,
        "hi": flags.hi,
    }
