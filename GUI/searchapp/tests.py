# 06-06-2025 By @FrancescoGrazioso -> "https://github.com/FrancescoGrazioso"


from django.test import TestCase, Client
from django.urls import reverse
from unittest.mock import patch
import json


class SeriesMetadataViewTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.url = reverse("series_metadata")

    def post_json(self, data):
        return self.client.post(
            self.url, data=json.dumps(data), content_type="application/json"
        )

    def test_film_returns_no_series(self):
        payload = {"type": "Film"}
        resp = self.post_json(
            {"source_alias": "streamingcommunity", "item_payload": payload}
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertFalse(data.get("isSeries"))
        self.assertEqual(data.get("seasonsCount"), 0)
        self.assertEqual(data.get("episodesPerSeason"), {})

    def test_ova_returns_no_series(self):
        payload = {"type": "OVA"}
        resp = self.post_json({"source_alias": "animeunity", "item_payload": payload})
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertFalse(data.get("isSeries"))
        self.assertEqual(data.get("seasonsCount"), 0)
        self.assertEqual(data.get("episodesPerSeason"), {})

    def test_unknown_site_returns_default(self):
        payload = {"type": "series"}
        resp = self.post_json({"source_alias": "unknown", "item_payload": payload})
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertFalse(data.get("isSeries"))

    @patch(
        "StreamingCommunity.Api.Site.streamingcommunity.util.ScrapeSerie.GetSerieInfo"
    )
    @patch(
        "StreamingCommunity.Util.config_json.config_manager.get_site",
        return_value="https://example.com",
    )
    def test_streamingcommunity_happy_path(self, _cfg_mock, getserieinfo_mock):
        instance = getserieinfo_mock.return_value
        instance.getNumberSeason.return_value = 2

        def _get_eps(season):
            return [object()] * (10 if season == 1 else 8)

        instance.getEpisodeSeasons.side_effect = _get_eps

        payload = {"type": "series", "id": 123, "slug": "my-show"}
        resp = self.post_json(
            {"source_alias": "streamingcommunity", "item_payload": payload}
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data.get("isSeries"))
        self.assertEqual(data.get("seasonsCount"), 2)
        self.assertEqual(data.get("episodesPerSeason"), {1: 10, 2: 8})

    @patch("StreamingCommunity.Api.Site.animeunity.util.ScrapeSerie.ScrapeSerieAnime")
    @patch(
        "StreamingCommunity.Util.config_json.config_manager.get_site",
        return_value="https://example.com",
    )
    def test_animeunity_single_season(self, _cfg_mock, scrape_mock):
        instance = scrape_mock.return_value
        instance.get_count_episodes.return_value = 24

        payload = {"type": "series", "id": 55, "slug": "anime-x"}
        resp = self.post_json({"source_alias": "animeunity", "item_payload": payload})
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data.get("isSeries"))
        self.assertEqual(data.get("seasonsCount"), 1)
        self.assertEqual(data.get("episodesPerSeason"), {1: 24})