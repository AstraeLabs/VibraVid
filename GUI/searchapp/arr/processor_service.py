# 07.05.26

import logging

from .clients.radarr_client import RadarrClient
from .clients.sonarr_client import SonarrClient

logger = logging.getLogger("ARR")


class ArrProcessorService:
    def __init__(
        self,
        sonarr: SonarrClient,
        radarr: RadarrClient,
        *,
        tags_mode: str = "BLACKLIST",
        active_tag_ids: list[int] | None = None,
    ):
        self.sonarr = sonarr
        self.radarr = radarr
        self.tags_mode = tags_mode.upper()  # BLACKLIST | WHITELIST
        self.active_tag_ids: list[int] = active_tag_ids or []

        # Tag maps {id: label_lowercase}
        self._sonarr_tags: dict[int, str] = {}
        self._radarr_tags: dict[int, str] = {}
        self._logged_skipped: set[str] = set()

    # ── public ───────────────────────────────────────────

    def get_missing_items(self) -> list[dict]:
        """Return all missing items (series + movies) ready for download."""
        self._logged_skipped.clear()
        self._sonarr_tags = self.sonarr.get_tags_map() if self.sonarr else {}
        self._radarr_tags = self.radarr.get_tags_map() if self.radarr else {}

        items: list[dict] = []

        if self.sonarr:
            try:
                sonarr_items = self._get_sonarr_missing()
                items.extend(sonarr_items)
            except Exception as exc:
                logger.error(f"Failed to fetch Sonarr missing: {exc}")

        if self.radarr:
            try:
                radarr_items = self._get_radarr_missing()
                items.extend(radarr_items)
            except Exception as exc:
                logger.error(f"Failed to fetch Radarr missing: {exc}")

        return items

    # ── Sonarr ───────────────────────────────────────────

    def _get_sonarr_missing(self) -> list[dict]:
        missing = self.sonarr.get_all_missing()
        reduced: list[dict] = []

        for elem in missing:
            if self._filter_sonarr(elem):
                self._reduce_sonarr(reduced, elem)

        for serie in reduced:
            serie["provider"] = self._extract_provider(serie["tags"], "sonarr")
            serie["seasons"].sort(key=lambda s: s["number"])
            for season in serie["seasons"]:
                season["episodes"].sort(key=lambda e: (e["seasonNumber"], e["episodeNumber"]))

        return reduced

    def _filter_sonarr(self, elem: dict) -> bool:
        series_title = elem["series"]["title"]
        season_num = elem["seasonNumber"]
        tag_ids = elem["series"]["tags"]

        if not self._check_tags_validity(series_title, tag_ids):
            return False

        tag_names = [self._sonarr_tags.get(t, "") for t in tag_ids]

        # Hold / Pause
        if "hold" in tag_names or "pausa" in tag_names:
            key = f"{series_title}_hold"
            if key not in self._logged_skipped:
                logger.info(f"'{series_title}' in PAUSA (remove 'hold' tag to resume)")
                self._logged_skipped.add(key)
            return False

        # Skip specific seasons (e.g. tag 'skip-s1')
        target_tag = f"skip-s{season_num}"
        if target_tag in tag_names:
            key = f"{series_title}_skip_{season_num}"
            if key not in self._logged_skipped:
                logger.info(f"Season {season_num} of '{series_title}' skipped (tag {target_tag})")
                self._logged_skipped.add(key)
            return False

        # Always skip Season 0 (Specials)
        if season_num == 0:
            return False

        return True

    def _reduce_sonarr(self, base: list[dict], elem: dict) -> None:
        serie = next((s for s in base if s["id"] == elem["series"]["id"]), None)
        if not serie:
            serie = {
                "content_type": "serie",
                "title": elem["series"]["title"],
                "path": elem["series"]["path"],
                "id": elem["series"]["id"],
                "tags": elem["series"]["tags"],
                "year": elem["series"].get("year"),
                "tmdbId": elem["series"].get("tmdbId"),
                "tvdbId": elem["series"].get("tvdbId"),
                "imdbId": elem["series"].get("imdbId"),
                "monitored": elem["series"].get("monitored", True),
                "seasons": [],
            }
            base.append(serie)

        season = next((s for s in serie["seasons"] if s["number"] == elem["seasonNumber"]), None)
        if not season:
            season = {"number": elem["seasonNumber"], "episodes": []}
            serie["seasons"].append(season)

        season["episodes"].append(
            {
                "id": elem["id"],
                "title": elem["title"],
                "seasonNumber": elem["seasonNumber"],
                "episodeNumber": elem["episodeNumber"],
                "absoluteEpisodeNumber": elem.get("absoluteEpisodeNumber"),
                "monitored": elem.get("monitored", True),
            }
        )

    # ── Radarr ───────────────────────────────────────────

    def _get_radarr_missing(self) -> list[dict]:
        missing = self.radarr.get_all_missing()
        valid: list[dict] = []

        for elem in missing:
            if self._filter_radarr(elem):
                valid.append(
                    {
                        "content_type": "movie",
                        "id": elem["id"],
                        "title": elem["title"],
                        "year": elem.get("year"),
                        "path": elem["path"],
                        "tags": elem["tags"],
                        "tmdbId": elem.get("tmdbId"),
                        "imdbId": elem.get("imdbId"),
                        "monitored": elem.get("monitored", True),
                        "provider": self._extract_provider(elem["tags"], "radarr"),
                    }
                )

        return valid

    def _filter_radarr(self, elem: dict) -> bool:
        title = elem["title"]
        tag_ids = elem.get("tags", [])

        if not self._check_tags_validity(title, tag_ids):
            return False

        tag_names = [self._radarr_tags.get(t, "") for t in tag_ids]

        if "hold" in tag_names or "pausa" in tag_names:
            if title not in self._logged_skipped:
                logger.info(f"'{title}' in PAUSA (remove 'hold' tag to resume)")
                self._logged_skipped.add(title)
            return False

        return True

    # ── utilities ────────────────────────────────────────

    def _check_tags_validity(self, title: str, item_tags: list) -> bool:
        item_has_active = any(t in self.active_tag_ids for t in item_tags)

        if self.tags_mode == "BLACKLIST" and item_has_active:
            if title not in self._logged_skipped:
                logger.debug(f"'{title}' skipped (blacklisted tag)")
                self._logged_skipped.add(title)
            return False

        if self.tags_mode == "WHITELIST" and not item_has_active:
            if title not in self._logged_skipped:
                logger.debug(f"'{title}' skipped (no whitelisted tag)")
                self._logged_skipped.add(title)
            return False

        return True

    def _extract_provider(self, tag_ids: list[int], source: str) -> str:
        tags_map = self._sonarr_tags if source == "sonarr" else self._radarr_tags
        for t_id in tag_ids:
            label = tags_map.get(t_id, "")
            if label.startswith("provider-"):
                return label.replace("provider-", "").strip()
        return ""
