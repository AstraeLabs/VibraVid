# 28.02.26


_SUBTITLE_FLAG_WORDS = (
    "forced", 
    "cc", 
    "sdh", 
    "hi", 
    "default"
)

LANGUAGES: dict[str, dict] = {
    "ita": {"iso1": "it", "bcp47": "it-IT", "display": "Italian", "aliases": ["italiano"]},
    "eng": {"iso1": "en", "bcp47": "en-US", "display": "English", "aliases": ["inglese"]},
    "jpn": {"iso1": "ja", "bcp47": "ja-JP", "display": "Japanese", "aliases": ["giapponese"]},
    "deu": {"iso1": "de", "bcp47": "de-DE", "display": "German", "aliases": ["ger", "german", "tedesco", "deutsch"]},
    "fra": {"iso1": "fr", "bcp47": "fr-FR", "display": "French", "aliases": ["fre", "french", "francese", "francais"]},
    "spa": {"iso1": "es", "bcp47": "es-419", "display": "Spanish", "aliases": ["spanish", "spagnolo", "espanol"]},
    "por": {"iso1": "pt", "bcp47": "pt-BR", "display": "Portuguese", "aliases": ["portuguese", "portoghese"]},
    "rus": {"iso1": "ru", "bcp47": "ru-RU", "display": "Russian", "aliases": ["russian", "russo"]},
    "ara": {"iso1": "ar", "bcp47": "ar-SA", "display": "Arabic", "aliases": ["arabic", "arabo"]},
    "zho": {"iso1": "zh", "bcp47": "zh-CN", "display": "Chinese", "aliases": ["chi", "chinese", "cinese", "mandarin"]},
    "kor": {"iso1": "ko", "bcp47": "ko-KR", "display": "Korean", "aliases": ["korean", "coreano"]},
    "hin": {"iso1": "hi", "bcp47": "hi-IN", "display": "Hindi", "aliases": ["hindi"]},
    "tur": {"iso1": "tr", "bcp47": "tr-TR", "display": "Turkish", "aliases": ["turkish", "turco"]},
    "pol": {"iso1": "pl", "bcp47": "pl-PL", "display": "Polish", "aliases": ["polish", "polacco"]},
    "nld": {"iso1": "nl", "bcp47": "nl-NL", "display": "Dutch", "aliases": ["dut", "dutch", "olandese"]},
    "swe": {"iso1": "sv", "bcp47": "sv-SE", "display": "Swedish", "aliases": ["swedish", "svedese"]},
    "fin": {"iso1": "fi", "bcp47": "fi-FI", "display": "Finnish", "aliases": ["finnish", "finlandese"]},
    "nor": {"iso1": "nb", "bcp47": "nb-NO", "display": "Norwegian", "aliases": ["no", "nob", "norwegian", "norvegese"]},
    "dan": {"iso1": "da", "bcp47": "da-DK", "display": "Danish", "aliases": ["danish", "danese"]},
    "cat": {"iso1": "ca", "bcp47": "ca-ES", "display": "Catalan", "aliases": ["catalan", "catala"]},
    "ron": {"iso1": "ro", "bcp47": "ro-RO", "display": "Romanian", "aliases": ["rum", "romanian", "rumeno"]},
    "ces": {"iso1": "cs", "bcp47": "cs-CZ", "display": "Czech", "aliases": ["cze", "czech", "ceco"]},
    "hun": {"iso1": "hu", "bcp47": "hu-HU", "display": "Hungarian", "aliases": ["hungarian", "ungherese"]},
    "ell": {"iso1": "el", "bcp47": "el-GR", "display": "Greek", "aliases": ["gre", "greek", "greco"]},
    "heb": {"iso1": "he", "bcp47": "he-IL", "display": "Hebrew", "aliases": ["hebrew", "ebraico"]},
    "tha": {"iso1": "th", "bcp47": "th-TH", "display": "Thai", "aliases": ["thai", "tailandese"]},
    "tam": {"iso1": "ta", "bcp47": "ta-IN", "display": "Tamil", "aliases": ["tamil"]},
    "tel": {"iso1": "te", "bcp47": "te-IN", "display": "Telugu", "aliases": ["telugu"]},
    "kan": {"iso1": "kn", "bcp47": "kn-IN", "display": "Kannada", "aliases": ["kannada"]},
    "mal": {"iso1": "ml", "bcp47": "ml-IN", "display": "Malayalam", "aliases": ["malayalam"]},
    "ben": {"iso1": "bn", "bcp47": "bn-IN", "display": "Bengali", "aliases": ["bengali"]},
    "mar": {"iso1": "mr", "bcp47": "mr-IN", "display": "Marathi", "aliases": ["marathi"]},
    "guj": {"iso1": "gu", "bcp47": "gu-IN", "display": "Gujarati", "aliases": ["gujarati"]},
    "pan": {"iso1": "pa", "bcp47": "pa-IN", "display": "Punjabi", "aliases": ["punjabi"]},
    "vie": {"iso1": "vi", "bcp47": "vi-VN", "display": "Vietnamese", "aliases": ["vietnamese", "vietnamita"]},
    "ind": {"iso1": "id", "bcp47": "id-ID", "display": "Indonesian", "aliases": ["indonesian", "indonesiano"]},
    "msa": {"iso1": "ms", "bcp47": "ms-MY", "display": "Malay", "aliases": ["may", "malay", "malese"]},
    "ukr": {"iso1": "uk", "bcp47": "uk-UA", "display": "Ukrainian", "aliases": ["ukrainian", "ucraino"]},
    "slk": {"iso1": "sk", "bcp47": "sk-SK", "display": "Slovak", "aliases": ["slo", "slovak", "slovacco"]},
    "hrv": {"iso1": "hr", "bcp47": "hr-HR", "display": "Croatian", "aliases": ["scr", "croatian"]},
    "srp": {"iso1": "sr", "bcp47": "sr-RS", "display": "Serbian", "aliases": ["serbian", "serbo"]},
    "bul": {"iso1": "bg", "bcp47": "bg-BG", "display": "Bulgarian", "aliases": ["bulgarian", "bulgaro"]},
    "slv": {"iso1": "sl", "bcp47": "sl-SI", "display": "Slovenian", "aliases": ["slovenian", "sloveno"]},
    "sqi": {"iso1": "sq", "bcp47": "sq-AL", "display": "Albanian", "aliases": ["alb", "albanian", "shqip"]},
    "est": {"iso1": "et", "bcp47": "et-EE", "display": "Estonian", "aliases": ["estonian"]},
    "isl": {"iso1": "is", "bcp47": "is-IS", "display": "Icelandic", "aliases": ["ice", "icelandic"]},
    "lit": {"iso1": "lt", "bcp47": "lt-LT", "display": "Lithuanian", "aliases": ["lithuanian"]},
    "lav": {"iso1": "lv", "bcp47": "lv-LV", "display": "Latvian", "aliases": ["latvian"]},
    "mkd": {"iso1": "mk", "bcp47": "mk-MK", "display": "Macedonian", "aliases": ["mac", "macedonian"]},
    "mon": {"iso1": "mn", "bcp47": "mn-MN", "display": "Mongolian", "aliases": ["mongolian"]},

    # --- Languages with no default locale/display ---
    "afr": {"iso1": "af", "aliases": ["afrikaans"]},
    "amh": {"iso1": "am", "aliases": ["amharic"]},
    "hye": {"iso1": "hy", "aliases": ["armenian"]},
    "aze": {"iso1": "az", "aliases": ["azerbaijani"]},
    "eus": {"iso1": "eu", "aliases": ["basque", "euskera"]},
    "bel": {"iso1": "be", "aliases": ["belarusian"]},
    "bos": {"iso1": "bs", "aliases": ["bosnian"]},
    "ceb": {"aliases": ["cebuano"]},
    "nya": {"iso1": "ny", "aliases": ["chichewa"]},
    "cos": {"iso1": "co", "aliases": ["corsican"]},
    "epo": {"iso1": "eo", "aliases": ["esperanto"]},
    "fil": {"iso1": "tl", "aliases": ["filipino", "tagalog"]},
    "fry": {"iso1": "fy", "aliases": ["frisian"]},
    "glg": {"iso1": "gl", "aliases": ["galician", "gallego"]},
    "kat": {"iso1": "ka", "aliases": ["georgian"]},

    "guj_dup": None,
    "hat": {"iso1": "ht", "aliases": ["haitian creole"]},
    "hau": {"iso1": "ha", "aliases": ["hausa"]},
    "haw": {"aliases": ["hawaiian"]},
    "hmn": {"aliases": ["hmong"]},
    "ibo": {"iso1": "ig", "aliases": ["igbo"]},
    "gle": {"iso1": "ga", "aliases": ["irish"]},
    "jav": {"iso1": "jv", "aliases": ["javanese"]},
    "kaz": {"iso1": "kk", "aliases": ["kazakh"]},
    "khm": {"iso1": "km", "aliases": ["khmer"]},
    "kin": {"iso1": "rw", "aliases": ["kinyarwanda"]},
    "kur": {"iso1": "ku", "aliases": ["kurdish"]},
    "kir": {"iso1": "ky", "aliases": ["kyrgyz"]},
    "lao": {"iso1": "lo", "aliases": ["lao"]},
    "lat": {"iso1": "la", "aliases": ["latin", "latino"]},
    "ltz": {"iso1": "lb", "aliases": ["luxembourgish"]},
    "mlg": {"iso1": "mg", "aliases": ["malagasy"]},
    "mlt": {"iso1": "mt", "aliases": ["maltese"]},
    "mri": {"iso1": "mi", "aliases": ["maori"]},
    "mya": {"iso1": "my", "aliases": ["myanmar", "burmese"]},
    "nep": {"iso1": "ne", "aliases": ["nepali"]},
    "ori": {"iso1": "or", "aliases": ["odia"]},
    "pus": {"iso1": "ps", "aliases": ["pashto"]},
    "fas": {"iso1": "fa", "aliases": ["persian", "farsi"]},
    "smo": {"iso1": "sm", "aliases": ["samoan"]},
    "gla": {"iso1": "gd", "aliases": ["scots gaelic"]},
    "sot": {"iso1": "st", "aliases": ["sesotho"]},
    "sna": {"iso1": "sn", "aliases": ["shona"]},
    "snd": {"iso1": "sd", "aliases": ["sindhi"]},
    "sin": {"iso1": "si", "aliases": ["sinhala", "sinhalese"]},
    "som": {"iso1": "so", "aliases": ["somali"]},
    "sun": {"iso1": "su", "aliases": ["sundanese"]},
    "swa": {"iso1": "sw", "aliases": ["swahili"]},
    "tgk": {"iso1": "tg", "aliases": ["tajik"]},
    "tat": {"iso1": "tt", "aliases": ["tatar"]},
    "tuk": {"iso1": "tk", "aliases": ["turkmen"]},
    "urd": {"iso1": "ur", "aliases": ["urdu"]},
    "uig": {"iso1": "ug", "aliases": ["uyghur"]},
    "uzb": {"iso1": "uz", "aliases": ["uzbek"]},
    "cym": {"iso1": "cy", "aliases": ["welsh"]},
    "xho": {"iso1": "xh", "aliases": ["xhosa"]},
    "yid": {"iso1": "yi", "aliases": ["yiddish"]},
    "yor": {"iso1": "yo", "aliases": ["yoruba"]},
    "zul": {"iso1": "zu", "aliases": ["zulu"]},

    # --- Extended ---
    "aar": {"iso1": "aa", "aliases": ["afar"]},
    "abk": {"iso1": "ab", "aliases": ["abkhazian"]},
    "aka": {"iso1": "ak", "aliases": ["akan"]},
    "arg": {"iso1": "an", "aliases": ["aragonese"]},
    "asm": {"iso1": "as", "aliases": ["assamese"]},
    "ava": {"iso1": "av", "aliases": ["avaric"]},
    "ave": {"iso1": "ae", "aliases": ["avestan"]},
    "aym": {"iso1": "ay", "aliases": ["aymara"]},
    "bak": {"iso1": "ba", "aliases": ["bashkir"]},
    "bam": {"iso1": "bm", "aliases": ["bambara"]},
    "bis": {"iso1": "bi", "aliases": ["bislama"]},
    "bod": {"iso1": "bo", "aliases": ["tibetan", "tib"]},
    "bre": {"iso1": "br", "aliases": ["breton"]},
    "cha": {"iso1": "ch", "aliases": ["chamorro"]},
    "che": {"iso1": "ce", "aliases": ["chechen"]},
    "chu": {"iso1": "cu", "aliases": ["church slavonic"]},
    "chv": {"iso1": "cv", "aliases": ["chuvash"]},
    "cor": {"iso1": "kw", "aliases": ["cornish"]},
    "cre": {"iso1": "cr", "aliases": ["cree"]},
    "div": {"iso1": "dv", "aliases": ["divehi"]},
    "dzo": {"iso1": "dz", "aliases": ["dzongkha"]},
    "ewe": {"iso1": "ee", "aliases": []},
    "fao": {"iso1": "fo", "aliases": ["faroese"]},
    "fij": {"iso1": "fj", "aliases": ["fijian"]},
    "ful": {"iso1": "ff", "aliases": ["fulah"]},
    "glv": {"iso1": "gv", "aliases": ["manx"]},
    "grn": {"iso1": "gn", "aliases": ["guarani"]},
    "her": {"iso1": "hz", "aliases": ["herero"]},
    "hmo": {"iso1": "ho", "aliases": ["hiri motu"]},
    "ido": {"iso1": "io", "aliases": []},
    "iii": {"aliases": ["sichuan yi"]},
    "iku": {"iso1": "iu", "aliases": ["inuktitut"]},
    "ile": {"iso1": "ie", "aliases": ["interlingue"]},
    "ina": {"iso1": "ia", "aliases": ["interlingua"]},
    "ipk": {"iso1": "ik", "aliases": ["inupiaq"]},
    "kal": {"iso1": "kl", "aliases": ["kalaallisut"]},
    "kas": {"iso1": "ks", "aliases": ["kashmiri"]},
    "kau": {"iso1": "kr", "aliases": ["kanuri"]},
    "kik": {"iso1": "ki", "aliases": ["kikuyu"]},
    "kom": {"iso1": "kv", "aliases": ["komi"]},
    "kon": {"iso1": "kg", "aliases": ["kongo"]},
    "kua": {"iso1": "kj", "aliases": ["kuanyama"]},
    "lim": {"iso1": "li", "aliases": ["limburgan"]},
    "lin": {"iso1": "ln", "aliases": ["lingala"]},
    "lub": {"iso1": "lu", "aliases": ["luba-katanga"]},
    "lug": {"iso1": "lg", "aliases": ["ganda"]},
    "mah": {"iso1": "mh", "aliases": ["marshallese"]},
    "nau": {"iso1": "na", "aliases": ["nauru"]},
    "nav": {"iso1": "nv", "aliases": ["navajo"]},
    "nbl": {"iso1": "nr", "aliases": ["south ndebele"]},
    "nde": {"iso1": "nd", "aliases": ["north ndebele"]},
    "ndo": {"iso1": "ng", "aliases": ["ndonga"]},
    "nno": {"iso1": "nn", "aliases": ["norwegian nynorsk"]},
    "oci": {"iso1": "oc", "aliases": ["occitan"]},
    "oji": {"iso1": "oj", "aliases": ["ojibwa"]},
    "orm": {"iso1": "om", "aliases": ["oromo"]},
    "oss": {"iso1": "os", "aliases": ["ossetian"]},
    "pli": {"iso1": "pi", "aliases": ["pali"]},
    "que": {"iso1": "qu", "aliases": ["quechua"]},
    "roh": {"iso1": "rm", "aliases": ["romansh"]},
    "run": {"iso1": "rn", "aliases": ["rundi"]},
    "sag": {"iso1": "sg", "aliases": ["sango"]},
    "san": {"iso1": "sa", "aliases": ["sanskrit"]},
    "sme": {"iso1": "se", "aliases": ["northern sami"]},
    "srd": {"iso1": "sc", "aliases": ["sardinian"]},
    "ssw": {"iso1": "ss", "aliases": ["swati"]},
    "tah": {"iso1": "ty", "aliases": ["tahitian"]},
    "tgl": {"iso1": "tl", "aliases": ["tagalog"]},
    "tir": {"iso1": "ti", "aliases": ["tigrinya"]},
    "ton": {"iso1": "to", "aliases": ["tonga"]},
    "tsn": {"iso1": "tn", "aliases": ["tswana"]},
    "tso": {"iso1": "ts", "aliases": ["tsonga"]},
    "twi": {"iso1": "tw", "aliases": []},
    "ven": {"iso1": "ve", "aliases": ["venda"]},
    "vol": {"iso1": "vo", "aliases": ["volapük"]},
    "wln": {"iso1": "wa", "aliases": ["walloon"]},
    "wol": {"iso1": "wo", "aliases": ["wolof"]},
    "zha": {"iso1": "za", "aliases": ["zhuang"]},
}

del LANGUAGES["guj_dup"]
_BCP47_REGION_OVERRIDES = {
    "pt-br": "pt-BR",
    "pt-pt": "pt-PT",
    "zh-cn": "zh-CN",
    "zh-tw": "zh-TW",
    "zh-hans": "zh-CN",
    "zh-hant": "zh-TW",
    "en-gb": "en-GB",
    "en-au": "en-AU",
    "es-mx": "es-MX",
    "es-es": "es-ES",
    "es-us": "es-US",
}

_BCP47_COUNTRY_SHORTCUTS = {
    "us": "en-US",
    "gb": "en-GB",
    "au": "en-AU",
    "br": "pt-BR",
    "jp": "ja-JP",
    "cn": "zh-CN",
    "tw": "zh-TW",
    "kr": "ko-KR",
    "mx": "es-MX",
}


def _build_lookup_tables():
    alias_to_iso3: dict[str, str] = {}
    iso3_to_bcp47: dict[str, str] = {}
    iso3_to_display: dict[str, str] = {}

    for iso3, info in LANGUAGES.items():
        alias_to_iso3[iso3] = iso3
        iso1 = info.get("iso1")
        if iso1:
            alias_to_iso3[iso1] = iso3
        display = info.get("display")
        if display:
            alias_to_iso3[display.lower()] = iso3
            iso3_to_display[iso3] = display
        for alias in info.get("aliases", ()):
            alias_to_iso3[alias.lower()] = iso3
        bcp47 = info.get("bcp47")
        if bcp47:
            iso3_to_bcp47[iso3] = bcp47

    return alias_to_iso3, iso3_to_bcp47, iso3_to_display


_ALIAS_TO_ISO3, _ISO3_TO_BCP47, _ISO3_TO_DISPLAY = _build_lookup_tables()
LANGUAGE_MAP = {alias: _ISO3_TO_BCP47[iso3] for alias, iso3 in _ALIAS_TO_ISO3.items() if iso3 in _ISO3_TO_BCP47}


def resolve_language_display_name(lang: str) -> str:
    """Best-effort human display name (e.g. "English", "Italian") for a language code/tag."""
    if not lang:
        return lang or ""

    locale = resolve_locale(lang) or lang
    primary = locale.split("-", 1)[0].lower()
    iso3 = _ALIAS_TO_ISO3.get(primary)
    if iso3 and iso3 in _ISO3_TO_DISPLAY:
        return _ISO3_TO_DISPLAY[iso3]

    iso3 = resolve_iso639_2(lang)
    return _ISO3_TO_DISPLAY.get(iso3) or iso3 or lang


def resolve_locale(lang: str) -> str:
    """Convert a language code or name to a BCP 47 locale string (e.g. "it-IT")."""
    if not lang or not isinstance(lang, str):
        return ""

    lang = lang.strip()
    if not lang:
        return ""

    if "-" in lang:
        parts = lang.split("-", 1)
        override = _BCP47_REGION_OVERRIDES.get(f"{parts[0].lower()}-{parts[1].lower()}")
        if override:
            return override
        normalised = f"{parts[0].lower()}-{parts[1].upper()}"
        return normalised if len(parts[1]) == 2 else lang

    low = lang.lower()
    shortcut = _BCP47_COUNTRY_SHORTCUTS.get(low)
    if shortcut:
        return shortcut

    iso3 = _ALIAS_TO_ISO3.get(low)
    return _ISO3_TO_BCP47.get(iso3, "") if iso3 else ""


_SUBTITLE_FLAGS = ("forced", "cc", "sdh", "hi", "default")


def resolve_ietf(value: str) -> str:
    """Region-preserving BCP-47 tag for muxers that store a language-ietf field (mkvmerge)."""
    raw = (value or "").strip()
    if not raw:
        return "und"

    low = raw.lower()
    for flag in _SUBTITLE_FLAGS:
        for sep in ("_", "-"):
            suffix = sep + flag
            if low.endswith(suffix):
                raw = raw[: -len(suffix)]
                low = raw.lower()
                break

    raw = raw.replace("_", "-")
    if not raw:
        return "und"

    parts = raw.split("-")
    result = []
    for i, part in enumerate(parts):
        if i == 0:
            if 2 <= len(part) <= 8 and part.isalpha():
                result.append(part.lower())
            else:
                return "und"
        elif i == 1:
            if len(part) == 2 and part.isalpha():  # region: 2 letters (US, FR, …)
                result.append(part.upper())
            elif len(part) == 3 and part.isdigit():  # numeric region: 3 digits (419, …)
                result.append(part)
            break

    return "-".join(result) if result else "und"


def resolve_iso639_2(lang: str) -> str:
    """Convert a language code or name to an ISO 639-2 code (e.g. "ita")."""
    raw = (lang or "").strip().lower()
    if not raw:
        return "und"

    if len(raw) == 3 and raw.isalpha():
        return raw

    if len(raw) == 2 and raw.isalpha():
        return _ALIAS_TO_ISO3.get(raw, "und")

    token = raw.split("-", 1)[0]
    token = token.split("_", 1)[0]
    if len(token) == 3 and token.isalpha():
        return token
    if len(token) == 2 and token.isalpha():
        return _ALIAS_TO_ISO3.get(token, "und")

    token = "".join(ch for ch in token if ch.isalpha())
    return _ALIAS_TO_ISO3.get(token, "und")


def resolve_iso639_1(lang: str) -> str:
    """Convert a language code or name to an ISO 639-1 (2-letter) code, e.g. "it"."""
    raw = (lang or "").strip().lower()
    if not raw:
        return ""

    # Look up the "iso1" field directly -- most entries in LANGUAGES have no "bcp47"/display info (they're subtitle/track-language only) 
    key = raw.split("-", 1)[0]
    iso3 = _ALIAS_TO_ISO3.get(key)
    if iso3:
        iso1 = LANGUAGES[iso3].get("iso1")
        if iso1:
            return iso1

    bcp47 = resolve_locale(lang)
    if bcp47:
        return bcp47.split("-", 1)[0].lower()

    if len(raw) == 2 and raw.isalpha():
        return raw
    return ""


def extract_lang_and_flags(lang_raw: str, track_info: dict = None):
    """Split a raw language string like ``en-us_cc`` into (base_lang, flags_set)."""
    import re as _re

    parts = _re.split(r"[-_]", lang_raw or "")
    flags = set()
    clean = []

    if track_info:
        if track_info.get("forced"):
            flags.add("forced")
        if track_info.get("sdh"):
            flags.add("sdh")
        if track_info.get("cc"):
            flags.add("cc")
        if track_info.get("default"):
            flags.add("default")

    for p in parts:
        if p.lower() in _SUBTITLE_FLAG_WORDS:
            flags.add(p.lower())
        else:
            clean.append(p)
    return "-".join(clean), flags


def subtitle_flags(lang_raw: str, track_info: dict = None) -> dict[str, bool]:
    """Return {'forced','cc','sdh','default'} booleans parsed from a raw language string and/or track dict."""
    _, flags = extract_lang_and_flags(lang_raw, track_info)
    forced = "forced" in flags
    return {
        "forced": forced,
        "sdh": "sdh" in flags,
        "cc": "cc" in flags or "hi" in flags,
        "default": "default" in flags and not forced,
    }


def language_variants(lang_raw: str) -> dict[str, str]:
    """Return the same language expressed as BCP-47, ISO 639-1 and ISO 639-2 codes."""
    base = (lang_raw or "").strip()
    return {
        "language_bcp47": resolve_locale(base) or base,
        "language_iso2": resolve_iso639_1(base),
        "language_iso3": resolve_iso639_2(base),
    }
