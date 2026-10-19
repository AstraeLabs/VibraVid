# 19.10.26

import hashlib
import io
import logging
import threading
from pathlib import Path

from rich.color import Color
from rich.style import Style
from rich.text import Text

from VibraVid.utils import config_manager

logger = logging.getLogger(__name__)

ENABLED = True
MAX_BYTES = 6 * 1024 * 1024
FETCH_TIMEOUT = 8

_lock = threading.Lock()
_url_cache: dict[tuple, str | None] = {}
_series_ids: dict[tuple, int | None] = {}
_bytes_cache: dict[str, bytes | None] = {}


# ── which image ───────────────────────────────────────────────────────────

def _identity(item, site: str | None) -> tuple:
    return (
        str(getattr(item, "tmdb_id", "") or ""),
        str(getattr(item, "name", "") or ""),
        str(getattr(item, "slug", "") or ""),
        str(getattr(item, "year", "") or ""),
        str(site or ""),
    )


def _service_image(item) -> str | None:
    return getattr(item, "poster", None) or getattr(item, "image", None) or None


def _series_tmdb_id(item, site: str | None) -> int | None:
    from VibraVid.services._base import tmdb_artwork

    key = _identity(item, site)
    with _lock:
        if key in _series_ids:
            return _series_ids[key]
    found = tmdb_artwork.resolve_series_tmdb_id(
        tmdb_id=getattr(item, "tmdb_id", None),
        name=getattr(item, "name", None),
        slug=getattr(item, "slug", None),
        year=getattr(item, "year", None),
        site_name=site,
    )
    with _lock:
        _series_ids[key] = found
    return found


def _from_tmdb(item, site: str | None, season: int | None, episode: int | None) -> str | None:
    from VibraVid.services._base import tmdb_artwork

    name, slug, year, tmdb_id = (getattr(item, a, None) for a in ("name", "slug", "year", "tmdb_id"))
    if getattr(item, "is_movie", False):
        return tmdb_artwork.resolve_movie_poster_url(tmdb_id=tmdb_id, name=name, slug=slug, year=year)

    series_id = _series_tmdb_id(item, site)
    if not series_id:
        return None
    if season is not None and episode is not None:
        return tmdb_artwork.resolve_episode_artwork_url(series_id, season, episode)
    if season is not None:
        _, _, fallback = tmdb_artwork.resolve_season_artwork(series_id, season)
        return fallback
    return tmdb_artwork.resolve_series_poster_url(series_id)


def _from_providers(item, season: int | None, episode: int | None) -> str | None:
    """The other configured metadata providers (IMDb, TVDB...), in the order of ``DEFAULT.metadata_provider``."""
    from VibraVid.services._base import metadata

    is_movie = getattr(item, "is_movie", False)
    target = metadata.find_sidecar_target("movie" if is_movie else "tv", item)
    if not target:
        return None
    provider_name, found_id, _title_only = target
    provider = metadata.get_provider(provider_name)
    if provider is None:
        return None

    if is_movie:
        info = provider.movie(found_id)
        return info.image_url if info else None
    if season is not None and episode is not None:
        info = provider.episode(found_id, season, episode)
        if info and info.image_url:
            return info.image_url
    series = provider.series(found_id)
    if not series:
        return None
    if season is not None and series.season_posters.get(season):
        return series.season_posters[season]
    return series.image_url


def cover_url(item, site: str | None = None, *, season: int | None = None, episode: int | None = None, own_image: str | None = None) -> str | None:
    """URL of the best cover for *item* (a search entry), or ``None``."""
    if not ENABLED or item is None:
        return None

    if getattr(item, "is_song", False) or getattr(item, "is_album", False):
        return own_image or _service_image(item)

    key = (*_identity(item, site), season, episode, own_image)
    with _lock:
        if key in _url_cache:
            return _url_cache[key]

    url = None
    for source in (
        lambda: _from_tmdb(item, site, season, episode),
        lambda: _from_providers(item, season, episode),
    ):
        try:
            url = source()
        except Exception as exc:
            logger.debug("cover lookup failed for %s: %s", getattr(item, "name", "?"), exc)
            continue
        if url:
            break

    url = url or own_image or _service_image(item)
    with _lock:
        _url_cache[key] = url
    return url


# ── getting the bytes ─────────────────────────────────────────────────────

def _cache_file(url: str) -> Path:
    base = Path(config_manager.base_path or ".") / ".cache" / "covers"
    return base / (hashlib.sha1(url.encode("utf-8")).hexdigest() + ".img")


def fetch_cover(url: str | None) -> bytes | None:
    """Image bytes for *url* (memory, then ``.cache/covers``, then the network), or ``None`` when unavailable."""
    if not url or not ENABLED:
        return None
    with _lock:
        if url in _bytes_cache:
            return _bytes_cache[url]

    data = None
    path = _cache_file(url)
    try:
        if path.is_file():
            data = path.read_bytes()
        else:
            from VibraVid.utils.http_client import create_client

            with create_client(timeout=FETCH_TIMEOUT, follow_redirects=True) as client:
                response = client.get(url, headers={"Accept": "image/jpeg,image/png;q=0.9,*/*;q=0.1"})
            body = response.content or b""
            if response.status_code == 200 and 0 < len(body) <= MAX_BYTES:
                data = body
                path.parent.mkdir(parents=True, exist_ok=True)
                tmp = path.with_suffix(".tmp")
                tmp.write_bytes(body)
                tmp.replace(path)
    except Exception as exc:
        logger.debug("cover download failed (%s): %s", url, exc)
        data = None

    with _lock:
        _bytes_cache[url] = data
    return data


# ── drawing it ────────────────────────────────────────────────────────────

def render_cover(data: bytes, max_cells_w: int, max_cells_h: int) -> Text | None:
    """The image as half-block characters (``▀``: top pixel = foreground, bottom pixel = background), fitted into the given cell box."""
    try:
        from PIL import Image

        with Image.open(io.BytesIO(data)) as source:
            image = source.convert("RGB")
    except Exception as exc:
        logger.debug("cover is not a readable image: %s", exc)
        return None

    src_w, src_h = image.size
    if src_w < 1 or src_h < 1 or max_cells_w < 1 or max_cells_h < 1:
        return None

    scale = min(max_cells_w / src_w, (max_cells_h * 2) / src_h)
    out_w = max(1, int(src_w * scale))
    out_h = max(2, int(src_h * scale) // 2 * 2)
    image = image.resize((out_w, out_h), Image.LANCZOS)
    pixels = image.load()

    styles: dict[tuple, Style] = {}
    text = Text(no_wrap=True, overflow="crop")
    for y in range(0, out_h, 2):
        for x in range(out_w):
            top, bottom = pixels[x, y], pixels[x, y + 1]
            style = styles.get((top, bottom))
            if style is None:
                style = Style(color=Color.from_rgb(*top), bgcolor=Color.from_rgb(*bottom))
                styles[(top, bottom)] = style
            text.append("▀", style)
        if y + 2 < out_h:
            text.append("\n")
    return text
