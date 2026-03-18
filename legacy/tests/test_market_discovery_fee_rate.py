import asyncio
import json
import unittest
from pathlib import Path

from config.settings import MarketConfig
from core.market_discovery import resolve_markets


class TestMarketDiscoveryFeeRate(unittest.TestCase):
    def test_fee_rate_selection_without_slug(self) -> None:
        data = json.loads(Path("tests/fixtures/gamma_markets_sample.json").read_text())
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

        def fee_rate_fetcher(token_id: str):
            return 15.0 if token_id == "btc_new_down" else 0.0

        resolved, _ = asyncio.run(
            resolve_markets(
                markets=markets,
                auto_discover=True,
                cache_path=Path("/tmp/ignore_cache.json"),
                markets_data=data,
                now_ts=1690000000,
                fee_rate_fetcher=fee_rate_fetcher,
            )
        )
        self.assertEqual(resolved[0].condition_id, "0xbtc2")
        self.assertEqual(resolved[0].token_ids, ["btc_new_down", "btc_new_up"])


if __name__ == "__main__":
    unittest.main()
