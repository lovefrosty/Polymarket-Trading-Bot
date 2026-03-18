import asyncio
import json
import unittest
from pathlib import Path

from config.settings import MarketConfig
from core.market_discovery import resolve_markets, select_latest_by_prefix


class TestMarketDiscovery(unittest.TestCase):
    def setUp(self) -> None:
        fixture_path = Path("tests/fixtures/gamma_markets_sample.json")
        self.data = json.loads(fixture_path.read_text())

    def test_selects_newest_by_slug_timestamp(self) -> None:
        market = select_latest_by_prefix(self.data, "btc-updown-15m-", now_ts=1690000000)
        self.assertIsNotNone(market)
        self.assertEqual(market.get("conditionId"), "0xbtc2")

    def test_resolve_maps_outcomes_by_index(self) -> None:
        configs = [
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
        resolved, _ = asyncio.run(
            resolve_markets(
                markets=configs,
                auto_discover=True,
                cache_path=Path("/tmp/ignore_cache.json"),
                markets_data=self.data,
                now_ts=1690000000,
            )
        )
        self.assertEqual(resolved[0].token_ids, ["btc_new_down", "btc_new_up"])
        self.assertEqual(resolved[0].outcomes, ["Down", "Up"])


if __name__ == "__main__":
    unittest.main()
