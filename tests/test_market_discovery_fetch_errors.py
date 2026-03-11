from __future__ import annotations

import json
import socket
import unittest
from unittest import mock
from urllib.error import HTTPError

from core.market_discovery import GammaFetchError, _fetch_json


class TestMarketDiscoveryFetchErrors(unittest.TestCase):
    def test_http_error_exposes_status_code(self) -> None:
        err = HTTPError("https://example.test", 503, "unavailable", hdrs=None, fp=None)
        with (
            mock.patch("urllib.request.urlopen", side_effect=err),
            mock.patch("core.market_discovery._FETCH_SLEEP"),
        ):
            with self.assertRaises(GammaFetchError) as ctx:
                _fetch_json("https://example.test")
        self.assertEqual(ctx.exception.error_code, "HTTP_503")
        self.assertEqual(ctx.exception.status, 503)

    def test_timeout_exposes_timeout_code(self) -> None:
        with (
            mock.patch("urllib.request.urlopen", side_effect=TimeoutError("timed out")),
            mock.patch("core.market_discovery._FETCH_SLEEP"),
        ):
            with self.assertRaises(GammaFetchError) as ctx:
                _fetch_json("https://example.test")
        self.assertEqual(ctx.exception.error_code, "TIMEOUT")

    def test_invalid_json_exposes_invalid_payload_code(self) -> None:
        fake_resp = mock.MagicMock()
        fake_resp.read.return_value = b"{bad json"
        fake_resp.__enter__.return_value = fake_resp
        fake_resp.__exit__.return_value = False
        with mock.patch("urllib.request.urlopen", return_value=fake_resp):
            with self.assertRaises(GammaFetchError) as ctx:
                _fetch_json("https://example.test")
        self.assertEqual(ctx.exception.error_code, "INVALID_PAYLOAD")

    def test_network_error_exposes_network_code(self) -> None:
        with (
            mock.patch("urllib.request.urlopen", side_effect=socket.gaierror("dns")),
            mock.patch("core.market_discovery._FETCH_SLEEP"),
        ):
            with self.assertRaises(GammaFetchError) as ctx:
                _fetch_json("https://example.test")
        self.assertEqual(ctx.exception.error_code, "NETWORK_ERROR")


if __name__ == "__main__":
    unittest.main()
