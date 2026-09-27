# 27.09.26

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup

_YEAR_RE = re.compile(r"\((19|20)\d{2}\)\s*$")
_TAG_RE = re.compile(r"\s*\[[^\]]+\]\s*")
_QUALITY_RE = re.compile(r"\b(2160p|1080p|720p|576p|480p|4k|uhd|fullhd|hd)\b", re.IGNORECASE)


@dataclass
class SearchResult:
    title: str
    url: str
    image: str | None = None
    year: str | None = None
    raw_title: str | None = None


@dataclass
class CineblogSource:
    section: str
    label: str
    host: str
    url: str
    quality: str | None = None
    verification_required: bool = False


def _clean_title(raw_title: str) -> tuple[str, str | None]:
    raw = " ".join((raw_title or "").split())
    year_match = _YEAR_RE.search(raw)
    year = year_match.group(0)[1:5] if year_match else None

    title = _YEAR_RE.sub("", raw).strip()
    title = _TAG_RE.sub(" ", title)
    title = " ".join(title.split()).strip()

    return title or raw, year


def parse_search_results(html: str, base_url: str) -> list[SearchResult]:
    """Parse all CB01 WordPress search cards without choosing a first match."""
    soup = BeautifulSoup(html, "html.parser")
    results: list[SearchResult] = []
    seen: set[str] = set()

    for card in soup.select("div.card.mp-post.horizontal"):
        link = card.select_one("h3.card-title a")
        if not link or not link.get("href"):
            continue

        url = urljoin(base_url, str(link.get("href")))
        if url in seen:
            continue
        seen.add(url)

        raw_title = link.get_text(" ", strip=True)
        title, year = _clean_title(raw_title)

        image_tag = card.find("img")
        image = None
        if image_tag:
            image = image_tag.get("src") or image_tag.get("data-src")
            if image:
                image = urljoin(base_url, str(image))

        results.append(
            SearchResult(
                title=title,
                url=url,
                image=image,
                year=year,
                raw_title=raw_title,
            )
        )

    return results


def _section_name(table) -> str:
    heading = table.find_previous(
        lambda tag: tag.name in {"p", "h2", "h3", "h4", "strong"}
        and any(
            marker in tag.get_text(" ", strip=True).lower()
            for marker in ("streaming", "download")
        )
    )
    if not heading:
        return "unknown"

    text = heading.get_text(" ", strip=True).lower()
    if "download hd" in text:
        return "download_hd"
    if "streaming hd" in text:
        return "streaming_hd"
    if "download" in text:
        return "download"
    if "streaming" in text:
        return "streaming"
    return "unknown"


def _quality_from_text(*parts: str) -> str | None:
    text = " ".join(p for p in parts if p)
    match = _QUALITY_RE.search(text)
    return match.group(1).upper() if match else None


def _source_host(url: str) -> str:
    return (urlsplit(url).hostname or "").lower()


def _verification_required(host: str) -> bool:
    return host.endswith("stayonline.pro")


def parse_detail_sources(html: str, base_url: str) -> list[CineblogSource]:
    """Extract public source links and player data-src values from a CB01 detail page."""
    soup = BeautifulSoup(html, "html.parser")
    sources: list[CineblogSource] = []
    seen: set[tuple[str, str]] = set()

    for table in soup.select("table.tableinside"):
        section = _section_name(table)
        for link in table.find_all("a", href=True):
            url = urljoin(base_url, str(link.get("href")))
            label = link.get_text(" ", strip=True) or _source_host(url)
            key = (section, url)
            if key in seen:
                continue
            seen.add(key)

            host = _source_host(url)
            sources.append(
                CineblogSource(
                    section=section,
                    label=label,
                    host=host,
                    url=url,
                    quality=_quality_from_text(label, section),
                    verification_required=_verification_required(host),
                )
            )

    for player in soup.select("div.tabs-catch-all[data-src]"):
        raw_url = player.get("data-src")
        if not raw_url:
            continue

        url = urljoin(base_url, str(raw_url))
        key = ("player", url)
        if key in seen:
            continue
        seen.add(key)

        host = _source_host(url)
        sources.append(
            CineblogSource(
                section="player",
                label=host or "player",
                host=host,
                url=url,
                quality=_quality_from_text(player.get_text(" ", strip=True)),
                verification_required=_verification_required(host),
            )
        )

    for iframe in soup.find_all("iframe", src=True):
        raw_url = str(iframe.get("src") or "").strip()
        if not raw_url or raw_url.lower() == "about:blank":
            continue

        url = urljoin(base_url, raw_url)
        key = ("player", url)
        if key in seen:
            continue
        seen.add(key)

        host = _source_host(url)
        sources.append(
            CineblogSource(
                section="player",
                label=iframe.get("title") or host or "iframe",
                host=host,
                url=url,
                quality=_quality_from_text(
                    str(iframe.get("title") or ""),
                    iframe.get_text(" ", strip=True),
                ),
                verification_required=_verification_required(host),
            )
        )

    return sources


def source_kind(source: CineblogSource) -> str:
    """Classify a source so the downloader can reuse existing VibraVid resolvers."""
    path = urlsplit(source.url).path.lower()

    if ".m3u8" in path:
        return "hls"
    if source.host.endswith("vidxgo.co") or "vidxgo" in source.host:
        return "vidxgo"
    if source.host.endswith("uprot.net") or "maxstream" in source.host:
        return "maxstream"
    if source.host.endswith("stayonline.pro") or "mixdrop" in source.host:
        return "stayonline"
    return "external"
