import asyncio
import unittest

from config.settings import MarketConfig, validate_markets_config
from core.market_discovery import resolve_markets
from pathlib import Path
import json


class TestConfigValidation(unittest.TestCase):
    def test_missing_ids_without_discovery_raises(self) -> None:
        markets = [
            MarketConfig(
                name="BTC 15m",
                condition_id=None,
                token_ids=[],
                slug_prefix=None,
                reference_symbol="BTC",
                min_tick=0.01,
                min_size=1.0,
                max_price=0.99,
                min_price=0.01,
            )
        ]
        with self.assertRaises(ValueError):
            validate_markets_config(markets, auto_discover=False)

    def test_auto_discover_allows_missing_ids(self) -> None:
        fixture_path = Path("tests/fixtures/gamma_markets_sample.json")
        data = json.loads(fixture_path.read_text())
        markets = [
            MarketConfig(
                name="BTC 15m",
                condition_id=None,
                token_ids=[],
                slug_prefix="btc-updown-15m-",
                reference_symbol="BTC",
                min_tick=0.01,
                min_size=1.0,
                max_price=0.99,
                min_price=0.01,
                discovery_backend=None,
                selection_regex=None,
                allow_unknown_symbol=False,
            )
        ]
        validate_markets_config(markets, auto_discover=True)
        resolved, _ = asyncio.run(
            resolve_markets(
                markets=markets,
                auto_discover=True,
                cache_path=Path("/tmp/ignore_cache.json"),
                markets_data=data,
                now_ts=1690000000,
            )
        )
        self.assertEqual(resolved[0].condition_id, "0xbtc2")
        self.assertEqual(resolved[0].token_ids, ["btc_new_down", "btc_new_up"])


if __name__ == "__main__":
    unittest.main()
