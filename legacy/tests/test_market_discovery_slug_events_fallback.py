import asyncio
import unittest
from unittest import mock

from config.settings import AutoDiscoverSpec, MarketConfig
from core.market_discovery import resolve_markets


class TestMarketDiscoverySlugEventsFallback(unittest.TestCase):
    def test_falls_back_to_events_when_slug_discovery_empty(self) -> None:
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
                auto_discover=AutoDiscoverSpec(symbol="BTC", horizon="15m", mode="latest_active"),
            )
        ]

        now_ts = 1_800_000_000
        fallback_market = {
            "conditionId": "btc_fallback",
            "clobTokenIds": ["t1", "t2"],
            "outcomes": ["Down", "Up"],
            "slug": "btc-updown-15m-1800000000",
            "question": "BTC fallback",
            "endTime": now_ts + 300,
            "active": True,
            "closed": False,
            "accepting_orders": True,
        }
        summary = {}

        async def _noop(*_args, **_kwargs):
            return None

        with mock.patch(
            "core.market_discovery.discover_15m_crypto_by_slug",
            return_value={"BTC": []},
        ), mock.patch(
            "core.market_discovery.load_gamma_markets",
            return_value=[fallback_market],
        ), mock.patch(
            "core.market_discovery._enrich_fee_metadata",
            new=_noop,
        ):
            resolved, _ = asyncio.run(
                resolve_markets(
                    markets=market_cfg,
                    auto_discover=True,
                    cache_path=None,
                    now_ts=now_ts,
                    discovery_summary=summary,
                )
            )

        self.assertEqual(resolved[0].condition_id, "btc_fallback")
        self.assertEqual(summary.get("slug_discovery_empty_fallback"), "events")
        self.assertEqual(summary.get("events_fallback_market_count"), 1)


if __name__ == "__main__":
    unittest.main()
