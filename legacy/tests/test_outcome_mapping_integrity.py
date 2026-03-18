import asyncio
import json
import unittest
from pathlib import Path

from config.settings import MarketConfig
from core.market_discovery import resolve_markets


class TestOutcomeMappingIntegrity(unittest.TestCase):
    def test_outcome_mapping_by_index(self) -> None:
        data = json.loads(Path("tests/fixtures/gamma_markets_sample.json").read_text())
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
        resolved, asset_meta = asyncio.run(
            resolve_markets(
                markets=markets,
                auto_discover=True,
                cache_path=Path("/tmp/ignore_cache.json"),
                markets_data=data,
                now_ts=1690000000,
            )
        )
        mapping = resolved[0].outcome_by_token
        self.assertEqual(mapping["btc_new_down"], "Down")
        self.assertEqual(mapping["btc_new_up"], "Up")
        self.assertEqual(asset_meta["btc_new_down"]["outcome"], "Down")


if __name__ == "__main__":
    unittest.main()
