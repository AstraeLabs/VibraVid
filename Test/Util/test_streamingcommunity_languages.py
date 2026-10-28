"""Catalog coverage for series with episodes released in English first."""

import html
import json
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from VibraVid.player import vixcloud
from VibraVid.services import streamingcommunity
from VibraVid.services.streamingcommunity import scrapper
from VibraVid.services.streamingcommunity import downloader


class _Response:
    def __init__(self, payload):
        self.payload = payload
        self.status_code = 200
        self.text = '<div id="app" data-page="' + html.escape(json.dumps(payload), quote=True) + '"></div>'

    def raise_for_status(self):
        pass

    def json(self):
        return self.payload


class _Client:
    def __init__(self, pages, headers):
        self.pages = pages
        self.headers = headers

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        pass

    def close(self):
        pass

    def get(self, url):
        if url not in self.pages:
            raise OSError(f"No fixture for {url}")
        payload = self.pages[url]
        return _Response(payload() if callable(payload) else payload)


def _page(seasons):
    return {"version": "v1", "props": {"title": {"tmdb_id": 1416, "seasons": seasons}}}


def _season(number, season_id, episodes):
    return {"props": {"loadedSeason": {"id": season_id, "number": number, "episodes": episodes}}}


class StreamingCommunityLanguageTests(unittest.TestCase):
    def test_search_retries_a_temporary_dns_timeout(self):
        base = "https://example.test"
        root = Mock(side_effect=[OSError("Resolving timed out"), {"version": "v1"}])
        pages = {f"{base}/it": root,
                 f"{base}/it/search?q=grey": {"props": {"titles": []}}}
        with (
            patch.object(streamingcommunity, "site_constants", SimpleNamespace(FULL_URL=base, SITE_NAME="streamingcommunity")),
            patch.object(streamingcommunity, "_effective_languages", return_value=["it"]),
            patch.object(streamingcommunity, "create_client", side_effect=lambda headers: _Client(pages, headers)),
            patch("time.sleep"),
        ):
            self.assertEqual(streamingcommunity.title_search("grey"), 0)
        self.assertEqual(root.call_count, 2)

    def test_search_reports_unreachable_catalogs_instead_of_no_results(self):
        with (
            patch.object(streamingcommunity, "_effective_languages", return_value=["it", "en"]),
            patch.object(streamingcommunity, "create_client", side_effect=OSError("Resolving timed out")),
        ):
            with self.assertRaisesRegex(RuntimeError, "VPN/DNS connection"):
                streamingcommunity.title_search("grey")

    def test_partial_catalog_warning_is_cleared_after_recovery(self):
        base = "https://example.test"
        italian = Mock(side_effect=[OSError("Connection failed"), OSError("Connection failed"), {"version": "v1"}])
        title = {"id": 127, "name": "Grey's Anatomy", "type": "tv", "release_date": "2005-03-27"}
        pages = {f"{base}/it": italian, f"{base}/en": {"version": "v1"},
                 f"{base}/it/search?q=grey": {"props": {"titles": [title]}},
                 f"{base}/en/search?q=grey": {"props": {"titles": [title]}}}
        with (
            patch.object(streamingcommunity, "site_constants", SimpleNamespace(FULL_URL=base, SITE_NAME="streamingcommunity")),
            patch.object(streamingcommunity, "_effective_languages", return_value=["it", "en"]),
            patch.object(streamingcommunity, "create_client", side_effect=lambda headers: _Client(pages, headers)),
            patch("time.sleep"),
        ):
            self.assertEqual(streamingcommunity.title_search("grey"), 1)
            self.assertIn("IT", streamingcommunity.entries_manager.search_warnings[0])
            self.assertEqual(streamingcommunity.entries_manager.media_list[0].catalog_languages, ["en"])
            self.assertEqual(streamingcommunity.title_search("grey"), 1)
            self.assertEqual(streamingcommunity.entries_manager.search_warnings, [])
            self.assertEqual(streamingcommunity.entries_manager.media_list[0].catalog_languages, ["it", "en"])

    def test_english_only_new_season_is_visible_with_italian_title(self):
        base = "https://example.test"
        title = "/titles/42-greys-anatomy"
        pages = {
            f"{base}/it{title}": _page([{"id": 122, "number": 22}]),
            f"{base}/en{title}": _page([{"id": 222, "number": 22}, {"id": 223, "number": 23}]),
            f"{base}/it": {"version": "v1"},
            f"{base}/en": {"version": "v1"},
            # The site's IT endpoint falls back to season 1 with HTTP 200.
            f"{base}/it{title}/season-23": _season(1, 101, [
                {"id": 1000 + n, "number": n, "name": f"Old episode {n}"} for n in range(1, 10)
            ]),
            f"{base}/en{title}/season-23": _season(23, 223, [
                {"id": 2301, "number": 1, "name": "Collide"},
                {"id": 2302, "number": 2, "name": "Slippin' and Slidin"},
            ]),
        }
        with patch.object(scrapper, "create_client", side_effect=lambda headers: _Client(pages, headers)):
            series = scrapper.GetSerieInfo(f"{base}/it", 42, "greys-anatomy", provider_language="it", languages=["it", "en"])

            self.assertEqual(series.getNumberSeason(), 2)
            self.assertEqual([season.number for season in series.seasons_manager.seasons], [22, 23])
            self.assertEqual(series.seasons_manager.get_season_by_number(22).id, 122)
            self.assertEqual(series.seasons_manager.get_season_by_number(23).catalog_ids, {"en": 223})
            episodes = series.getEpisodeSeasons(23)
            self.assertEqual([(ep.number, ep.id, ep.language, ep.provider_language) for ep in episodes],
                             [(1, 2301, "en", "en"), (2, 2302, "en", "en")])

    def test_wrong_loaded_season_is_rejected_even_when_catalog_lists_it(self):
        base = "https://example.test"
        title = "/titles/42-greys-anatomy"
        pages = {
            f"{base}/it{title}": _page([{"id": 223, "number": 23}]),
            f"{base}/en{title}": _page([{"id": 223, "number": 23}]),
            f"{base}/it": {"version": "v1"},
            f"{base}/en": {"version": "v1"},
            f"{base}/it{title}/season-23": _season(1, 101, [{"id": 1001, "number": 1}]),
            f"{base}/en{title}/season-23": _season(23, 223, [{"id": 2301, "number": 1}]),
        }
        with patch.object(scrapper, "create_client", side_effect=lambda headers: _Client(pages, headers)):
            series = scrapper.GetSerieInfo(f"{base}/it", 42, "greys-anatomy")
            series.getNumberSeason()
            episodes = series.getEpisodeSeasons(23)
            self.assertEqual([(ep.id, ep.language) for ep in episodes], [(2301, "en")])

    def test_episode_union_keeps_correct_source_for_english_only_episode(self):
        base = "https://example.test"
        title = "/titles/42-greys-anatomy"
        pages = {
            f"{base}/it{title}": _page([{"id": 122, "number": 22}]),
            f"{base}/en{title}": _page([{"id": 122, "number": 22}]),
            f"{base}/it": {"version": "v1"},
            f"{base}/en": {"version": "v1"},
            f"{base}/it{title}/season-22": _season(22, 122, [{"id": 2201, "number": 1}]),
            f"{base}/en{title}/season-22": _season(22, 122, [
                {"id": 2201, "number": 1}, {"id": 2202, "number": 2}
            ]),
        }
        with patch.object(scrapper, "create_client", side_effect=lambda headers: _Client(pages, headers)):
            series = scrapper.GetSerieInfo(f"{base}/it", 42, "greys-anatomy")
            series.getNumberSeason()
            episodes = series.getEpisodeSeasons(22)
            self.assertEqual([(ep.number, ep.language, ep.provider_language) for ep in episodes],
                             [(1, "en,it", "it"), (2, "en", "en")])

    def test_title_falls_back_to_english_when_italian_page_fails(self):
        base = "https://example.test"
        pages = {f"{base}/en/titles/42-greys-anatomy": _page([{"id": 223, "number": 23}])}
        with patch.object(scrapper, "create_client", side_effect=lambda headers: _Client(pages, headers)):
            series = scrapper.GetSerieInfo(f"{base}/it", 42, "greys-anatomy", provider_language="it", languages=["it", "en"])

            self.assertEqual(series.getNumberSeason(), 1)
            self.assertEqual(series.get_tmdb_id(), 1416)

    def test_transient_english_catalog_failure_is_retried(self):
        base = "https://example.test"
        english = Mock(side_effect=[OSError("timeout"), _page([{"id": 223, "number": 23}])])
        pages = {
            f"{base}/it/titles/42-greys-anatomy": _page([{"id": 122, "number": 22}]),
            f"{base}/en/titles/42-greys-anatomy": english,
        }
        with patch.object(scrapper, "create_client", side_effect=lambda headers: _Client(pages, headers)):
            series = scrapper.GetSerieInfo(f"{base}/it", 42, "greys-anatomy")
            self.assertEqual(series.getNumberSeason(), 2)
            self.assertEqual(english.call_count, 2)

    def test_partial_catalog_is_recovered_on_next_metadata_request(self):
        base = "https://example.test"
        english = Mock(side_effect=[OSError("timeout"), OSError("timeout"), _page([
            {"id": 122, "number": 22}, {"id": 223, "number": 23},
        ])])
        pages = {
            f"{base}/it/titles/42-greys-anatomy": _page([{"id": 122, "number": 22}]),
            f"{base}/en/titles/42-greys-anatomy": english,
        }
        with patch.object(scrapper, "create_client", side_effect=lambda headers: _Client(pages, headers)):
            series = scrapper.GetSerieInfo(f"{base}/it", 42, "greys-anatomy")
            self.assertEqual(series.getNumberSeason(), 1)
            old_season = series.seasons_manager.seasons[0]
            old_season.episodes.add(scrapper.Episode(id=2201, number=1))
            self.assertEqual(series.getNumberSeason(), 2)
            self.assertEqual([season.number for season in series.seasons_manager.seasons], [22, 23])
            self.assertEqual(old_season.catalog_ids, {"it": 122, "en": 122})
            self.assertEqual(len(old_season.episodes), 0)

    def test_download_uses_english_source_for_english_only_episode(self):
        series = SimpleNamespace(base_url="https://example.test", provider_language="it",
                                 series_name="greys-anatomy", series_display_name="Grey's Anatomy", year=2005)
        episode = SimpleNamespace(id=2301, name="Collide", provider_language="en")
        source = Mock(url="https://example.test/it")
        source.get_playlist.return_value = "https://example.test/playlist.m3u8"
        with patch.multiple(downloader, start_message=Mock(), print_episode_header=Mock(),
                            tmdb_client=SimpleNamespace(api_key=None),
                            map_episode_path=Mock(return_value=([], "S23E01")),
                            series_folder=Mock(return_value="/tmp"), HLS_Downloader=Mock()):
            downloader.download_episode(episode, 23, 1, series, source)
        self.assertEqual(source.url, "https://example.test/en")
        source.get_iframe.assert_called_once_with(2301)

    def test_tmdb_player_resolution_preserves_english_catalog(self):
        pages = {"https://vixsrc.to/api/tv/1416/23/1?lang=en": {"src": "/tv/1416/23/1"}}
        with patch.object(vixcloud, "create_client", side_effect=lambda headers: _Client(pages, headers)):
            source = vixcloud.VideoSource("https://example.test/en", True, 42,
                                         tmdb_data={"id": 1416, "s": 23, "e": 1})
            source._resolve_tmdb_embed_url()
        self.assertEqual(source.iframe_src, "https://vixsrc.to/tv/1416/23/1")
