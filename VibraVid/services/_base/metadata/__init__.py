# 01.10.26

import logging
import re

from VibraVid.services._base import tmdb_artwork
from VibraVid.utils import config_manager

from .base import BaseMetadataProvider, EpisodeInfo, MovieInfo, Person, SeriesInfo
from .imdb import ImdbProvider
from .tmdb import TmdbProvider
from .tvdb import TvdbProvider

__all__ = [
    "BaseMetadataProvider",
    "EpisodeInfo",
    "MovieInfo",
    "PROVIDERS",
    "Person",
    "SeriesInfo",
    "find_sidecar_target",
    "get_provider",
    "provider_names",
]

logger = logging.getLogger(__name__)
DEFAULT_PROVIDERS = "tmdb,imdb"
PROVIDERS: dict[str, type[BaseMetadataProvider]] = {
    TmdbProvider.NAME: TmdbProvider,
    ImdbProvider.NAME: ImdbProvider,
    TvdbProvider.NAME: TvdbProvider,
}


def provider_names(value: str | None = None) -> list[str]:
    """Providers to try, in order, from ``DEFAULT.metadata_provider`` ("tmdb", "imdb,tvdb", "tmdb|imdb"...)."""
    if value is None:
        value = config_manager.config.get("DEFAULT", "metadata_provider", default=DEFAULT_PROVIDERS)

    names: list[str] = []
    for token in re.split(r"[,|;\s]+", str(value or "").lower()):
        if token in PROVIDERS:
            if token not in names:
                names.append(token)
        elif token:
            logger.warning(f"metadata_provider: unknown provider '{token}' ignored (valid: {', '.join(PROVIDERS)})")
    
    return names or DEFAULT_PROVIDERS.split(",")


def get_provider(name: str) -> BaseMetadataProvider | None:
    cls = PROVIDERS.get(name)
    return cls() if cls else None


def find_sidecar_target(media_type: str, select_title) -> tuple[str, str, bool] | None:
    """Find the best matching metadata provider and its ID for a given title, or None if no match is found."""
    name = getattr(select_title, "name", None)
    slug = getattr(select_title, "slug", None)
    year = getattr(select_title, "year", None)
    site_tmdb_id = getattr(select_title, "tmdb_id", None)

    providers = []
    for provider_name in provider_names():
        provider = get_provider(provider_name)
        if provider is None or not provider.available():
            logger.debug(f"sidecars: provider '{provider_name}' unavailable (no API key?)")
            continue
        providers.append((provider_name, provider))
    
    if not providers:
        logger.info(f"sidecars skipped for '{name}': no configured provider is available (missing API key?)")
        return None

    # Series are matched strictly only for sites whose episode numbering is known to line up with the providers'.
    strict_allowed = media_type != "tv" or bool(site_tmdb_id) or tmdb_artwork.tv_matching_allowed()
    if strict_allowed:
        for provider_name, provider in providers:
            try:
                found = provider.find(media_type, name, slug, year, site_tmdb_id)
            except Exception as exc:
                logger.warning(f"sidecars: {provider_name} lookup failed for '{name}': {exc}")
                continue

            if found:
                logger.info(f"sidecars: '{name}' matched on {provider_name} ({found})")
                return provider_name, found, False

    if media_type == "tv":
        for provider_name, provider in providers:
            try:
                found = provider.find_by_title(media_type, name)
            except Exception as exc:
                logger.warning(f"sidecars: {provider_name} title lookup failed for '{name}': {exc}")
                continue
            
            if found:
                logger.info(f"sidecars: '{name}' is a title-only candidate on {provider_name} ({found}); each episode must match by title")
                return provider_name, found, True

    reason = "no reliable match" if strict_allowed else "site not trusted for series numbering and no unique title match"
    logger.info(f"sidecars skipped for '{name}': {reason} on {', '.join(n for n, _ in providers)}")
    return None
