import json
import os
import tempfile
import unittest
from pathlib import Path

from core.market_discovery import _load_cache


class TestMarketDiscoveryCache(unittest.TestCase):
    def test_load_cache_none_returns_none(self) -> None:
        self.assertIsNone(_load_cache(None, cache_ttl_secs=60))
        self.assertIsNone(_load_cache("", cache_ttl_secs=60))

    def test_load_cache_str_path_reads(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "cache.json"
            payload = {"timestamp": 0, "markets": [{"slug": "btc"}]}
            path.write_text(json.dumps(payload), encoding="utf-8")
            result = _load_cache(str(path), cache_ttl_secs=60)
            self.assertEqual(result, payload["markets"])

    def test_load_cache_path_reads(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "cache.json"
            payload = {"timestamp": 0, "markets": [{"slug": "eth"}]}
            path.write_text(json.dumps(payload), encoding="utf-8")
            result = _load_cache(path, cache_ttl_secs=60)
            self.assertEqual(result, payload["markets"])

    def test_load_cache_ttl_expired_returns_none(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "cache.json"
            payload = {"timestamp": 0, "markets": [{"slug": "sol"}]}
            path.write_text(json.dumps(payload), encoding="utf-8")
            old_time = 1
            os.utime(path, (old_time, old_time))
            result = _load_cache(path, cache_ttl_secs=1)
            self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
