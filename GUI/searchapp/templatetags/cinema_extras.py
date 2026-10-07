# 28.07.26

from django import template

register = template.Library()
_PROVIDER_LABELS = {
    "7movies": "7Movies",
    "animeunity": "AnimeUnity",
    "animeworld": "AnimeWorld",
    "annasarchive": "Anna's Archive",
    "appletv": "Apple TV",
    "discoveryplus": "Discovery+",
    "disney": "Disney+",
    "dmax": "DMAX",
    "foodnetwork": "Food Network",
    "hbomax": "HBO Max",
    "homegardentv": "Home & Garden TV",
    "la7": "La7",
    "libgen": "Library Genesis",
    "mediasetinfinity": "Mediaset Infinity",
    "mostraguarda": "MostraGuarda",
    "primevideo": "Prime Video",
    "raiplay": "RaiPlay",
    "realtime": "Real Time",
    "streamingcommunity": "StreamingCommunity",
    "tubitv": "Tubi TV"
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
