# 30.10.26

import logging
import re
import threading
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from VibraVid.provider.tmdb import tmdb_client
from VibraVid.services._base.object import Entries, Episode, Season, SeasonManager

from .client import LoonexClient

logger = logging.getLogger(__name__)

_SLUG_STAMP_RE = re.compile(r"-\d{8,}$")
_SEASON_NUMBER_RE = re.compile(r"^(?:stagione|season)\s*(\d+)", re.I)
_EPISODE_CODE_RE = re.compile(r"\bS\d{1,2}\s*E(\d{1,3})\b", re.I)
_EPISODE_TITLE_PREFIX_RE = re.compile(r"^(?:episodio\s*)?(?:\d+\s*x\s*)?\d+(?!\d|\s*-\s*\d)\s*[-:.]?\s*", re.I)


def _clean_text(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def search_loonex(query: str) -> list[Entries]:
    """Search the Loonex catalog (first results page, A-Z sorted by the site)."""
    client = LoonexClient()
    html = client.get_html(f"{client.base_url}/cartoni/index.php", params={"search": query})
    soup = BeautifulSoup(html, "html.parser")

    results: list[Entries] = []
    for card in soup.select(".cartoon-col"):
        link = card.select_one('a[href^="?c="]')
        title = card.select_one(".card-title-cine-v2, .card-title-cine")
        if not link or not title:
            continue

        name = _clean_text(title.get("title") or title.get_text())
        raw_slug = link["href"].split("=", 1)[1]
        image = card.select_one("img.card-img-bg")
        results.append(
            Entries(
                id=raw_slug,
                name=name,
                slug=tmdb_client._slugify(name) or _SLUG_STAMP_RE.sub("", raw_slug),  # the site's own slug can append the original title ("x-the-x"), which TMDB cannot match
                path_id=raw_slug,
                type="movie" if card.select_one(".movie-badge") else "tv",
                url=urljoin(f"{client.base_url}/cartoni/", link["href"]),
                image=image.get("src") if image else None,
                year="9999",
            )
        )

    return results


class GetSerieInfo:
    def __init__(self, series_url: str, series_name: str = None, year: str = None, tmdb_id: str = None):
        self.client = LoonexClient()
        self.series_url = series_url
        self.series_name = series_name
        self.series_display_name = series_name
        self.year = year
        self.tmdb_id = tmdb_id
        self.seasons_manager = SeasonManager()
        self.movie_url: str | None = None
        self._episodes_by_season: dict[int, list[Episode]] = {}
        self._loaded = False
        self._load_lock = threading.Lock()

    def _load(self) -> None:
        with self._load_lock:
            if self._loaded:
                return

            self._loaded = True
            try:
                self._parse(BeautifulSoup(self.client.get_html(self.series_url), "html.parser"))
            except Exception as error:
                logger.error(f"[Loonex] Series load failed for {self.series_url}: {error}")

    def _decode_play_link(self, container) -> str | None:
        """Decoded play URL of the first obfuscated link inside *container* (episode row or movie card)."""
        anchor = container.select_one("a[data-stream]")
        if not anchor:
            return None
        return self.client.decode_link(anchor["data-stream"], int(anchor.get("data-s") or 50))

    @staticmethod
    def _episode_number(title: str, used: set[int], fallback: int) -> int:
        match = _EPISODE_CODE_RE.search(title) or re.search(r"\d+\s*x\s*(\d+)", title, re.I) or re.match(r"\s*(\d+)", title) or re.search(r"episodio\s*(\d+)", title, re.I)
        number = int(match.group(1)) if match else fallback
        return number if number not in used else fallback

    @staticmethod
    def _season_numbers(labels: list[str]) -> list[int]:
        """Number the tabs: "Stagione N" keeps N, anything else (extras, specials) gets the next free number."""
        numbers: list[int | None] = []
        taken: set[int] = set()
        for label in labels:
            match = _SEASON_NUMBER_RE.match(label)
            number = int(match.group(1)) if match and int(match.group(1)) not in taken else None
            numbers.append(number)
            if number is not None:
                taken.add(number)

        next_free = max(taken, default=0)
        for index, number in enumerate(numbers):
            if number is None:
                next_free += 1
                numbers[index] = next_free
        return numbers

    def _parse(self, soup: BeautifulSoup) -> None:
        if not self.series_name:
            heading = soup.select_one("h1")
            self.series_name = self.series_display_name = _clean_text(heading.get_text()) if heading else "Loonex"

        buttons = soup.select('button[id^="season-tab-"]')
        panes = [(_clean_text(b.get_text(" ")), soup.select_one(b.get("data-bs-target") or "")) for b in buttons]
        panes = [(label, pane) for label, pane in panes if pane is not None]

        for number, (label, pane) in zip(self._season_numbers([label for label, _ in panes]), panes, strict=True):
            episodes: list[Episode] = []
            used: set[int] = set()
            for index, row in enumerate(pane.select(".ep-row"), 1):
                play_url = self._decode_play_link(row)
                if not play_url:
                    continue

                title = _clean_text(row.get("data-ep-title") or row.get("data-ep-label") or "")
                episode_number = self._episode_number(title, used, index)
                used.add(episode_number)
                episodes.append(
                    Episode(
                        id=episode_number,
                        number=episode_number,
                        name=_EPISODE_TITLE_PREFIX_RE.sub("", _EPISODE_CODE_RE.sub("", title)).strip().lstrip("-:. ") or f"Episodio {episode_number}",
                        url=play_url,
                        year=self.year,
                    )
                )

            if episodes:
                self._episodes_by_season[number] = episodes
                self.seasons_manager.add(Season(id=number, number=number, name=label, slug=f"stagione-{number}", type="season"))

        if not self._episodes_by_season:
            card = soup.select_one(".quality-card")
            self.movie_url = self._decode_play_link(card) if card else None

    def is_movie(self) -> bool:
        """True when the page has no episodes but a single playable item (film)."""
        self._load()
        return not self._episodes_by_season and bool(self.movie_url)

    # ------------- FOR GUI -------------
    def getNumberSeason(self) -> int:
        self._load()
        return len(self.seasons_manager.seasons)

    def getEpisodeSeasons(self, season_number: int) -> list:
        self._load()
        return self._episodes_by_season.get(season_number, [])
