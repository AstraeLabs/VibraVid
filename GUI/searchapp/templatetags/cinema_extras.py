# 28.07.26

from django import template

register = template.Library()
_PROVIDER_LABELS = {
    "animeunity": "AnimeUnity",
    "animeworld": "AnimeWorld",
    "annasarchive": "Anna's Archive",
    "appletv": "Apple TV",
    "discoveryplus": "Discovery+",
    "dmax": "DMAX",
    "foodnetwork": "Food Network",
    "homegardentv": "Home & Garden TV",
    "libgen": "Library Genesis",
    "mediasetinfinity": "Mediaset Infinity",
    "mostraguarda": "MostraGuarda",
    "myanonamouse": "MyAnonaMouse",
    "primevideo": "Prime Video",
    "raiplay": "RaiPlay",
    "realtime": "Real Time",
    "streamingcommunity": "StreamingCommunity",
    "tubitv": "Tubi TV",
    "zlibrary": "Z-Library",
}


@register.filter
def provider(alias: str) -> str:
    """Return a human-readable provider name for a given alias."""
    key = str(alias or "").strip()
    if not key:
        return ""

    known = _PROVIDER_LABELS.get(key.lower())
    if known:
        return known

    words = key.replace("_", " ").replace("-", " ").split()
    return " ".join(w[:1].upper() + w[1:] if w.islower() else w for w in words)


__all__ = ["provider"]
