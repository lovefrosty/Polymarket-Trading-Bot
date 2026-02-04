import json
import unittest
from unittest import mock
from urllib.error import HTTPError

import core.market_discovery as md


class _DummyResponse:
    def __init__(self, payload: object) -> None:
        self._payload = payload

    def read(self) -> bytes:
        return json.dumps(self._payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None


class TestGammaLoaderShapes(unittest.TestCase):
    def setUp(self) -> None:
        self._sleep = md._FETCH_SLEEP
        md._FETCH_SLEEP = lambda _seconds: None

    def tearDown(self) -> None:
        md._FETCH_SLEEP = self._sleep

    def test_gamma_shape_dict_markets(self) -> None:
        with mock.patch("urllib.request.urlopen", return_value=_DummyResponse({"markets": [{"slug": "btc"}]})):
            results = md.load_gamma_markets("http://example", cache_path=None, limit=2)
            self.assertEqual(len(results), 1)

    def test_gamma_shape_list(self) -> None:
        with mock.patch("urllib.request.urlopen", return_value=_DummyResponse([{"slug": "eth"}])):
            results = md.load_gamma_markets("http://example", cache_path=None, limit=2)
            self.assertEqual(len(results), 1)

    def test_retry_on_403_then_success(self) -> None:
        def side_effect(req, timeout=10, context=None):
            if not hasattr(side_effect, "count"):
                side_effect.count = 0
            side_effect.count += 1
            if side_effect.count == 1:
                raise HTTPError(req.full_url, 403, "forbidden", None, None)
            return _DummyResponse([{"slug": "btc"}])

        with mock.patch("urllib.request.urlopen", side_effect=side_effect):
            results = md.load_gamma_markets("http://example", cache_path=None, limit=2)
            self.assertEqual(len(results), 1)

    def test_retry_on_429_then_success(self) -> None:
        def side_effect(req, timeout=10, context=None):
            if not hasattr(side_effect, "count"):
                side_effect.count = 0
            side_effect.count += 1
            if side_effect.count == 1:
                raise HTTPError(req.full_url, 429, "rate", None, None)
            return _DummyResponse({"markets": [{"slug": "btc"}]})

        with mock.patch("urllib.request.urlopen", side_effect=side_effect):
            results = md.load_gamma_markets("http://example", cache_path=None, limit=2)
            self.assertEqual(len(results), 1)

    def test_retry_on_500_then_success(self) -> None:
        def side_effect(req, timeout=10, context=None):
            if not hasattr(side_effect, "count"):
                side_effect.count = 0
            side_effect.count += 1
            if side_effect.count == 1:
                raise HTTPError(req.full_url, 500, "server", None, None)
            return _DummyResponse({"markets": [{"slug": "btc"}]})

        with mock.patch("urllib.request.urlopen", side_effect=side_effect):
            results = md.load_gamma_markets("http://example", cache_path=None, limit=2)
            self.assertEqual(len(results), 1)

    def test_fetch_json_mock(self) -> None:
        original = md._fetch_json
        try:
            md._fetch_json = lambda _url: {"markets": [{"slug": "btc"}]}
            results = md.load_gamma_markets("http://example", cache_path=None, limit=2)
            self.assertEqual(len(results), 1)
        finally:
            md._fetch_json = original


if __name__ == "__main__":
    unittest.main()
