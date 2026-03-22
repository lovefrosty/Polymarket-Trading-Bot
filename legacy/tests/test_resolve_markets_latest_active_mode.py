import asyncio
import itertools
import unittest
from unittest import mock

from config.settings import AutoDiscoverSpec, MarketConfig
from core.market_discovery import NoActiveMarketError, resolve_markets


class TestResolveMarketsLatestActiveMode(unittest.TestCase):
    def _market_config(self) -> list[MarketConfig]:
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

    def test_resolve_mode_does_not_pick_last_discovered(self) -> None:
        now_ts = 1_700_000_000
        active = {
            "conditionId": "active",
            "clobTokenIds": ["a1", "a2"],
            "outcomes": ["Down", "Up"],
            "slug": "btc-updown-15m-1700000000",
            "question": "BTC active",
            "endTime": now_ts + 300,
            "active": True,
            "closed": False,
            "accepting_orders": True,
        }
        future = {
            "conditionId": "future",
            "clobTokenIds": ["f1", "f2"],
            "outcomes": ["Down", "Up"],
            "slug": "btc-updown-15m-1700000900",
            "question": "BTC future",
            "endTime": now_ts + 1_800,
            "active": True,
            "closed": False,
            "accepting_orders": True,
        }
        summary = {}

        async def _noop(*_args, **_kwargs):
            return None

        with mock.patch(
            "core.market_discovery.discover_15m_crypto_by_slug",
            return_value={"BTC": [active, future]},
        ), mock.patch(
            "core.market_discovery._enrich_fee_metadata",
            new=_noop,
        ):
            resolved, _ = asyncio.run(
                resolve_markets(
                    markets=self._market_config(),
                    auto_discover=True,
                    cache_path=None,
                    now_ts=now_ts,
                    discovery_summary=summary,
                )
            )

        self.assertEqual(resolved[0].condition_id, "active")
        requests = summary.get("discovery_requests", [])
        self.assertEqual(len(requests), 1)
        self.assertEqual(requests[0].get("selected_condition_id"), "active")

    def test_resolve_mode_is_deterministic_under_permutations(self) -> None:
        now_ts = 1_800_000_000
        candidates = [
            {
                "conditionId": "c1",
                "clobTokenIds": ["a1", "a2"],
                "outcomes": ["Down", "Up"],
                "slug": "btc-updown-15m-a",
                "endTime": now_ts + 600,
            },
            {
                "conditionId": "c2",
                "clobTokenIds": ["b1", "b2"],
                "outcomes": ["Down", "Up"],
                "slug": "btc-updown-15m-b",
                "endTime": now_ts + 600,
            },
            {
                "conditionId": "c3",
                "clobTokenIds": ["c1", "c2"],
                "outcomes": ["Down", "Up"],
                "slug": "btc-updown-15m-c",
                "endTime": now_ts + 600,
            },
        ]
        selected_conditions = set()

        async def _noop(*_args, **_kwargs):
            return None

        for perm in itertools.permutations(candidates):
            with mock.patch(
                "core.market_discovery.discover_15m_crypto_by_slug",
                return_value={"BTC": list(perm)},
            ), mock.patch(
                "core.market_discovery._enrich_fee_metadata",
                new=_noop,
            ):
                resolved, _ = asyncio.run(
                    resolve_markets(
                        markets=self._market_config(),
                        auto_discover=True,
                        cache_path=None,
                        now_ts=now_ts,
                        discovery_summary={},
                    )
                )
            selected_conditions.add(resolved[0].condition_id)

        self.assertEqual(len(selected_conditions), 1)
        self.assertIn("c3", selected_conditions)

    def test_resolve_mode_raises_no_active_market_error(self) -> None:
        now_ts = 1_900_000_000
        ended = {
            "conditionId": "ended",
            "clobTokenIds": ["e1", "e2"],
            "outcomes": ["Down", "Up"],
            "slug": "btc-updown-15m-1900000000",
            "endTime": now_ts - 1,
        }
        not_started = {
            "conditionId": "future",
            "clobTokenIds": ["f1", "f2"],
            "outcomes": ["Down", "Up"],
            "slug": "btc-updown-15m-1900000900",
            "endTime": now_ts + 1_400,
        }
        summary = {}

        async def _noop(*_args, **_kwargs):
            return None

        with mock.patch(
            "core.market_discovery.discover_15m_crypto_by_slug",
            return_value={"BTC": [ended, not_started]},
        ), mock.patch(
            "core.market_discovery._enrich_fee_metadata",
            new=_noop,
        ):
            with self.assertRaises(NoActiveMarketError):
                asyncio.run(
                    resolve_markets(
                        markets=self._market_config(),
                        auto_discover=True,
                        cache_path=None,
                        now_ts=now_ts,
                        discovery_summary=summary,
                    )
                )

        requests = summary.get("discovery_requests", [])
        self.assertEqual(len(requests), 1)
        self.assertEqual(requests[0].get("error_code"), "NO_ACTIVE_BTC_15M")


if __name__ == "__main__":
    unittest.main()
