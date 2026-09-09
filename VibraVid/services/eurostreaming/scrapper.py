# 29.05.26
# By @UrloMythus

import difflib
import html
import logging
import re
import threading

from VibraVid.services._base.object import Episode, EpisodeManager, Season, SeasonManager
from VibraVid.utils.http_client import create_client, get_userAgent

logger = logging.getLogger(__name__)
_EP_RE = re.compile(r"(\d+)&#215;(\d{2})\b")
_BR_RE = re.compile(r"<br\s*/?>", re.IGNORECASE)
_YEAR_RE = re.compile(r"(?<![/\d])(19|20)\d{2}(?![/\d])")


class GetSerieInfo:
    def __init__(self, series_name: str, base_url: str):
        self.series_name = html.unescape(series_name)
        self.series_display_name = self.series_name
        self.base_url = base_url.rstrip("/")
        self.year = None
        self.seasons_manager = SeasonManager()
        self._content: str = ""
        self._loaded = False
        self._load_lock = threading.Lock()

    def _load(self) -> None:
        with self._load_lock:
            self._load_locked()

    def _load_locked(self) -> None:
        if self._loaded:
            return

        self._loaded = True
        headers = {"User-Agent": get_userAgent()}
        best_ratio = 0.0
        best_content = ""
        best_title = ""

        with create_client(headers=headers) as client:
            try:
                resp = client.get(
                    f"{self.base_url}/wp-json/wp/v2/search", params={"search": self.series_name, "_fields": "id"}
                )
                resp.raise_for_status()
                results = resp.json()
            except Exception as e:
                logger.error(f"[Eurostreaming] WP search failed for '{self.series_name}': {e}")
                return

            for item in results[:20]:
                post_id = item.get("id")
                if not post_id:
                    continue

                try:
                    post_resp = client.get(
                        f"{self.base_url}/wp-json/wp/v2/posts/{post_id}",
                        params={"_fields": "content,title"},
                    )
                    post_resp.raise_for_status()
                    data = post_resp.json()
                    title = html.unescape(data.get("title", {}).get("rendered", ""))
                    content = data.get("content", {}).get("rendered", "")

                    ratio = difflib.SequenceMatcher(None, title.lower(), self.series_name.lower()).ratio()
                    if ratio > best_ratio:
                        best_ratio = ratio
                        best_content = content
                        best_title = title

                except Exception as e:
                    logger.error(f"[Eurostreaming] Post fetch failed id={post_id}: {e}")

        if not best_content:
            logger.warning(f"[Eurostreaming] No post found for '{self.series_name}'")
            return

        self.series_display_name = best_title or self.series_name
        self._content = best_content
        year_m = _YEAR_RE.search(best_content)
        if year_m:
            self.year = year_m.group(0)

        self._parse_content()

    def _parse_content(self) -> None:
        seasons: dict[int, set[int]] = {}
        for m in _EP_RE.finditer(self._content):
            s, e = int(m.group(1)), int(m.group(2))
            seasons.setdefault(s, set()).add(e)

        for season_num in sorted(seasons):
            em = EpisodeManager()
            for ep_num in sorted(seasons[season_num]):
                title_m = re.search(
                    rf"{season_num}&#215;{ep_num:02d}\s*[-–]\s*([^<\n]+)",
                    self._content,
                )
                ep_title = html.unescape(title_m.group(1).strip()) if title_m else f"Episodio {ep_num}"
                em.add(Episode(id=ep_num, number=ep_num, name=ep_title))

            s = Season(id=season_num, number=season_num, name=f"Stagione {season_num}", slug="")
            s.episodes = em
            self.seasons_manager.add(s)

    def get_episode_link(self, season_number: int, episode_number: int) -> tuple[str | None, str | None]:
        """
        Return (url, host) for the best available hosting link for the episode.

        Priority: Loadm -> MaxStream (uprot.net/msf) -> None
        Host values: 'loadm', 'maxstream', None
        """
        if not self._loaded:
            self._load()

        ep_tag = f"{season_number}&#215;{episode_number:02d}"

        loadm_url = None
        maxstream_url = None

        for line in _BR_RE.split(self._content):
            if ep_tag not in line:
                continue

            if not loadm_url:
                m = re.search(r'href="(https?://loadm[^"]+)"', line, re.IGNORECASE)
                if m:
                    loadm_url = m.group(1)

            if not maxstream_url:
                m = re.search(r'href="(https://uprot\.net/msf/[^"]+)"', line, re.IGNORECASE)
                if m:
                    maxstream_url = m.group(1)

        if loadm_url:
            return loadm_url, "loadm"

        if maxstream_url:
            return maxstream_url, "maxstream"

        logger.warning(f"[Eurostreaming] No supported link for S{season_number}E{episode_number:02d}")
        return None, None

    def getNumberSeason(self) -> int:
        self._load()
        return len(self.seasons_manager.seasons)

    def getEpisodeSeasons(self, season_number: int) -> list:
        self._load()
        season = self.seasons_manager.get_season_by_number(season_number)
        return season.episodes.episodes if season else []
