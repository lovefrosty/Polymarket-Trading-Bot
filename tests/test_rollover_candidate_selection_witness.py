import asyncio
import unittest
from unittest import mock

from config.settings import AutoDiscoverSpec, MarketConfig
from core.market_discovery import resolve_markets


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


class TestRolloverCandidateSelectionWitness(unittest.TestCase):
    def test_selection_prefers_known_tradable_and_emits_witness(self) -> None:
        now_ts = 1_700_000_000
        # Active but explicitly non-tradable.
        closed_latest = {
            "conditionId": "closed_latest",
            "clobTokenIds": ["c1", "c2"],
            "outcomes": ["Down", "Up"],
            "slug": "btc-updown-15m-1700000000",
            "endTime": now_ts + 500,
            "active": True,
            "closed": True,
            "accepting_orders": False,
        }
        # Active and unknown metadata.
        unknown_newer = {
            "conditionId": "unknown_newer",
            "clobTokenIds": ["u1", "u2"],
            "outcomes": ["Down", "Up"],
            "slug": "btc-updown-15m-1699999900",
            "endTime": now_ts + 400,
        }
        # Active and known tradable (should win over unknown).
        known_tradable = {
            "conditionId": "known_tradable",
            "clobTokenIds": ["k1", "k2"],
            "outcomes": ["Down", "Up"],
            "slug": "btc-updown-15m-1699999800",
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
            return_value={"BTC": [closed_latest, unknown_newer, known_tradable]},
        ), mock.patch("core.market_discovery._enrich_fee_metadata", new=_noop):
            resolved, _asset_meta = asyncio.run(
                resolve_markets(
                    markets=_market_config(),
                    auto_discover=True,
                    cache_path=None,
                    now_ts=now_ts,
                    discovery_summary=summary,
                    max_candidates_considered=10,
                )
            )

        self.assertEqual(resolved[0].condition_id, "known_tradable")
        req = summary.get("discovery_requests", [{}])[0]
        self.assertEqual(req.get("selected_tradable_meta_state"), "KNOWN_TRADABLE")
        self.assertEqual(req.get("max_candidates_considered"), 10)
        self.assertEqual(req.get("selection_witness", {}).get("ws_confirm_state"), "PENDING")
        rejected = req.get("rejected_reason_counts", {})
        self.assertEqual(rejected.get("NON_TRADABLE_CLOSED"), 1)


if __name__ == "__main__":
    unittest.main()

