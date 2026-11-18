"""TMDB artwork requests recover from temporary DNS failures without caching errors."""

import unittest
from types import SimpleNamespace
from unittest.mock import Mock, call, patch

from curl_cffi.const import CurlECode

from VibraVid.provider import tmdb


class _DNSFailure(Exception):
    code = CurlECode.COULDNT_RESOLVE_HOST


class _HTTPFailure(Exception):
    def __init__(self, status, message="HTTP failure"):
        super().__init__(message)
        self.response = SimpleNamespace(status_code=status)


class TMDBNetworkRetryTests(unittest.TestCase):
    def setUp(self):
        self.tmdb = tmdb.TMDBClient(api_key="test-secret-key")
        self.http = Mock()
        self.http.__enter__ = Mock(return_value=self.http)
        self.http.__exit__ = Mock(return_value=False)
        self.response = Mock()
        self.response.json.return_value = {"id": 23, "episodes": [{"episode_number": 1}, {"episode_number": 2}]}

    def test_dns_failure_retries_and_returns_season_data(self):
        self.http.get.side_effect = [_DNSFailure("Could not resolve host: api.themoviedb.org"), self.response]
        with patch.object(tmdb, "create_client", return_value=self.http), patch.object(tmdb.time, "sleep") as sleep:
            result = self.tmdb._make_request("tv/1416/season/23")
        self.assertEqual(len(result["episodes"]), 2)
        self.assertEqual(self.http.get.call_count, 2)
        sleep.assert_called_once_with(1)

    def test_persistent_dns_failure_stops_at_retry_limit_and_is_not_cached(self):
        self.http.get.side_effect = _DNSFailure("Could not resolve host")
        with patch.object(tmdb, "create_client", return_value=self.http), patch.object(tmdb.time, "sleep") as sleep:
            self.assertEqual(self.tmdb._make_request("tv/1416/season/23", retries=2), {})
        self.assertEqual(self.http.get.call_count, 3)
        self.assertEqual(sleep.call_args_list, [call(1), call(2)])
        self.assertEqual(self.tmdb._cache, {})
        self.http.get.side_effect = None
        self.http.get.return_value = self.response
        with patch.object(tmdb, "create_client", return_value=self.http):
            self.assertEqual(self.tmdb._make_request("tv/1416/season/23"), self.response.json.return_value)

    def test_missing_season_is_not_retried(self):
        self.http.get.side_effect = _HTTPFailure(404)
        with patch.object(tmdb, "create_client", return_value=self.http), patch.object(tmdb.time, "sleep") as sleep:
            self.assertEqual(self.tmdb._make_request("tv/1416/season/23"), {})
        self.assertEqual(self.http.get.call_count, 1)
        sleep.assert_not_called()

    def test_temporary_http_failure_is_still_retried(self):
        self.http.get.side_effect = [_HTTPFailure(503), self.response]
        with patch.object(tmdb, "create_client", return_value=self.http), patch.object(tmdb.time, "sleep") as sleep:
            self.assertEqual(self.tmdb._make_request("tv/1416/season/23"), self.response.json.return_value)
        self.assertEqual(self.http.get.call_count, 2)
        sleep.assert_called_once_with(1)

    def test_authentication_failure_is_not_retried_and_key_is_redacted(self):
        self.http.get.side_effect = _HTTPFailure(401, "Unauthorized: api_key=test-secret-key")
        with (
            patch.object(tmdb, "create_client", return_value=self.http),
            patch.object(tmdb.time, "sleep") as sleep,
            patch.object(tmdb, "console") as console,
        ):
            self.assertEqual(self.tmdb._make_request("tv/1416/season/23"), {})
        self.assertEqual(self.http.get.call_count, 1)
        sleep.assert_not_called()
        self.assertNotIn("test-secret-key", str(console.log.call_args_list))
