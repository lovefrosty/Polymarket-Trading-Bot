import asyncio
import unittest
from unittest import mock

from config.settings import AutoDiscoverSpec, MarketConfig
from core.market_discovery import NoActiveMarketError, resolve_markets


def _market_config() -> list[MarketConfig]:
    return [
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


class TestRolloverCandidateBoundFallback(unittest.TestCase):
    def test_fail_closed_when_bounded_candidates_non_tradable(self) -> None:
        now_ts = 1_700_000_000
        # Both active-now but non-tradable by explicit metadata.
        closed_1 = {
            "conditionId": "closed1",
            "clobTokenIds": ["c1", "c2"],
            "outcomes": ["Down", "Up"],
            "slug": "btc-updown-15m-1700000000",
            "endTime": now_ts + 500,
            "active": True,
            "closed": True,
            "accepting_orders": False,
        }
        closed_2 = {
            "conditionId": "closed2",
            "clobTokenIds": ["d1", "d2"],
            "outcomes": ["Down", "Up"],
            "slug": "btc-updown-15m-1699999900",
            "endTime": now_ts + 400,
            "active": True,
            "closed": True,
            "accepting_orders": False,
        }
        summary = {}

        async def _noop(*_args, **_kwargs):
            return None

        with mock.patch(
            "core.market_discovery.discover_15m_crypto_by_slug",
            return_value={"BTC": [closed_1, closed_2]},
        ), mock.patch("core.market_discovery._enrich_fee_metadata", new=_noop):
            with self.assertRaises(NoActiveMarketError):
                asyncio.run(
                    resolve_markets(
                        markets=_market_config(),
                        auto_discover=True,
                        cache_path=None,
                        now_ts=now_ts,
                        discovery_summary=summary,
                        max_candidates_considered=1,
                    )
                )

        req = summary.get("discovery_requests", [{}])[0]
        self.assertEqual(req.get("status"), "NONE_FOUND")
        self.assertEqual(req.get("max_candidates_considered"), 1)
        self.assertEqual(req.get("candidates_considered"), 1)


if __name__ == "__main__":
    unittest.main()

