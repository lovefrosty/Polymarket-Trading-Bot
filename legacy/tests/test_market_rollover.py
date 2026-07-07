import unittest

from core.market_discovery import ResolvedMarket
from core.market_rollover import (
    MarketRolloverConfig,
    MarketRolloverManager,
    MarketState,
    market_state_from_resolved,
)


class TestMarketRolloverManager(unittest.TestCase):
    def test_evaluate_reasons_includes_prefetch_and_stale(self) -> None:
        manager = MarketRolloverManager(
            current=MarketState(
                market_slug="btc-updown-15m-1700000900",
                condition_id="c1",
                token_ids=["t1", "t2"],
                market_end_ts_ms=1_700_000_900_000,
                market_end_source="metadata",
            ),
            config=MarketRolloverConfig(prefetch_ms=120_000, stale_ms=15_000, discovery_period_ms=30_000),
        )
        now_ms = 1_700_000_800_000
        reasons = manager.evaluate_reasons(now_ms=now_ms, last_book_recv_wall_ms=1_700_000_780_000, market_closed=False)
        self.assertIn("TIME_WINDOW_END", reasons)
        self.assertIn("NO_MESSAGES_STALE", reasons)

    def test_discovery_throttle(self) -> None:
        manager = MarketRolloverManager(
            current=MarketState(
                market_slug="btc-updown-15m-1700000900",
                condition_id="c1",
                token_ids=["t1", "t2"],
                market_end_ts_ms=1_700_000_900_000,
                market_end_source="metadata",
            ),
            config=MarketRolloverConfig(discovery_period_ms=30_000),
        )
        self.assertTrue(manager.should_attempt_discovery(100_000, ["TIME_WINDOW_END"]))
        manager.mark_discovery_attempt(100_000)
        self.assertFalse(manager.should_attempt_discovery(120_000, ["TIME_WINDOW_END"]))
        self.assertTrue(manager.should_attempt_discovery(131_000, ["TIME_WINDOW_END"]))

    def test_change_detection(self) -> None:
        manager = MarketRolloverManager(
            current=MarketState(
                market_slug="btc-updown-15m-1700000900",
                condition_id="c1",
                token_ids=["t1", "t2"],
                market_end_ts_ms=1_700_000_900_000,
                market_end_source="metadata",
            )
        )
        same = MarketState(
            market_slug="btc-updown-15m-1700000900",
            condition_id="c1",
            token_ids=["t2", "t1"],
            market_end_ts_ms=1_700_000_900_000,
            market_end_source="metadata",
        )
        changed_slug = MarketState(
            market_slug="btc-updown-15m-1700001800",
            condition_id="c2",
            token_ids=["n1", "n2"],
            market_end_ts_ms=1_700_001_800_000,
            market_end_source="metadata",
        )
        self.assertFalse(manager.has_market_changed(same))
        self.assertTrue(manager.has_market_changed(changed_slug))

    def test_can_commit_switch_waits_for_end_unless_stale(self) -> None:
        manager = MarketRolloverManager(
            current=MarketState(
                market_slug="btc-updown-15m-1700000900",
                condition_id="c1",
                token_ids=["t1", "t2"],
                market_end_ts_ms=1_700_000_900_000,
                market_end_source="metadata",
            )
        )
        self.assertFalse(manager.can_commit_switch(now_ms=1_700_000_850_000, trigger_reasons=["TIME_WINDOW_END"]))
        self.assertTrue(manager.can_commit_switch(now_ms=1_700_000_850_000, trigger_reasons=["NO_MESSAGES_STALE"]))
        self.assertTrue(manager.can_commit_switch(now_ms=1_700_000_900_000, trigger_reasons=["TIME_WINDOW_END"]))


class TestMarketStateFromResolved(unittest.TestCase):
    def test_prefers_metadata_end_ts(self) -> None:
        market = ResolvedMarket(
            name="BTC 15m",
            reference_symbol="BTC",
            slug_prefix=None,
            slug="btc-updown-15m-1700000900",
            condition_id="c1",
            token_ids=["t1", "t2"],
            outcomes=["Up", "Down"],
            outcome_by_token={"t1": "Up", "t2": "Down"},
            token_by_outcome={"Up": "t1", "Down": "t2"},
            question=None,
            min_tick=0.01,
            min_size=1.0,
            min_price=0.01,
            max_price=0.99,
        )
        state = market_state_from_resolved(
            market,
            {
                "t1": {"end_ts_ms": 1_700_000_900_000},
                "t2": {},
            },
        )
        self.assertEqual(state.market_end_ts_ms, 1_700_000_900_000)
        self.assertEqual(state.market_end_source, "metadata")

    def test_uses_slug_fallback_when_metadata_missing(self) -> None:
        market = ResolvedMarket(
            name="BTC 15m",
            reference_symbol="BTC",
            slug_prefix=None,
            slug="btc-updown-15m-1700000900",
            condition_id="c1",
            token_ids=["t1", "t2"],
            outcomes=["Up", "Down"],
            outcome_by_token={"t1": "Up", "t2": "Down"},
            token_by_outcome={"Up": "t1", "Down": "t2"},
            question=None,
            min_tick=0.01,
            min_size=1.0,
            min_price=0.01,
            max_price=0.99,
        )
        state = market_state_from_resolved(market, {"t1": {}, "t2": {}})
        self.assertEqual(state.market_end_ts_ms, 1_700_000_900_000)
        self.assertEqual(state.market_end_source, "slug_fallback")


if __name__ == "__main__":
    unittest.main()
