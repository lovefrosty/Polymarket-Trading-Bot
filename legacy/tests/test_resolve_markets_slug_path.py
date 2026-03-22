import asyncio
import unittest
from unittest import mock

from config.settings import MarketConfig
from core.market_discovery import resolve_markets


class TestResolveMarketsSlugPath(unittest.TestCase):
    def test_resolve_uses_slug_discovery(self) -> None:
        market_cfg = [
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
                discovery_backend=None,
                selection_regex=None,
                allow_unknown_symbol=False,
            )
        ]
        selected = {
            "conditionId": "0xabc",
            "clobTokenIds": ["t1", "t2"],
            "outcomes": ["Down", "Up"],
            "slug": "btc-updown-15m-1700000000",
            "question": "BTC Up or Down 15m",
        }

        async def _noop(*_args, **_kwargs):
            return None

        with mock.patch(
            "core.market_discovery.discover_15m_crypto_by_slug",
            return_value={"BTC": [selected]},
        ), mock.patch(
            "core.market_discovery._enrich_fee_metadata",
            new=_noop,
        ):
            resolved, _ = asyncio.run(
                resolve_markets(
                    markets=market_cfg,
                    auto_discover=True,
                    cache_path=None,
                    markets_data=None,
                    now_ts=1_700_000_000,
                )
            )

        self.assertEqual(len(resolved), 1)
        self.assertEqual(resolved[0].condition_id, "0xabc")
        self.assertEqual(resolved[0].token_ids, ["t1", "t2"])


if __name__ == "__main__":
    unittest.main()
