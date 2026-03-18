import unittest

from core.decision_tape import TimeMapper
from core.order_book import OrderBook
from core.policy_gate import PolicyThresholds
from core.pstar import PStarBuilder
from core.validators import OrderConstraints
from scripts.run_system import MarketReadinessConfig, RuntimeEngine


class _DummyDB:
    def insert(self, table: str, row: dict) -> None:  # pragma: no cover
        _ = (table, row)


class _DummyDecisionTape:
    run_id = "test"

    def write(self, record) -> None:  # pragma: no cover
        _ = record


class _DummyTradeTape:
    run_id = "test"

    def next_event_id(self) -> int:  # pragma: no cover
        return 1

    def write(self, payload) -> None:  # pragma: no cover
        _ = payload


def _runtime(tokens: list[str]) -> RuntimeEngine:
    books = {token: OrderBook(asset_id=token, bids={}, asks={}) for token in tokens}
    constraints = {
        token: OrderConstraints(
            min_tick=0.01,
            min_size=1.0,
            min_price=0.01,
            max_price=0.99,
            max_spread_bps=2000.0,
            max_slippage_bps=2000.0,
            max_book_staleness_ms=30_000,
        )
        for token in tokens
    }
    market_meta = {token: {"reference_symbol": "BTC"} for token in tokens}
    thresholds = PolicyThresholds(
        max_book_age_ms=30_000,
        max_spread_bps=2000.0,
        max_slippage_bps=2000.0,
        max_signal_age_ms=30_000,
        max_ws_lag_ms=30_000,
    )
    return RuntimeEngine(
        mode="OBSERVE",
        db=_DummyDB(),
        decision_tape=_DummyDecisionTape(),
        trade_tape=_DummyTradeTape(),
        books=books,
        constraints=constraints,
        market_meta=market_meta,
        pstar_builder=PStarBuilder(max_age_ms=60_000, freeze_disagree_bps=1000.0),
        policy_thresholds=thresholds,
        constitution={"trading": {}, "policy": {}, "execution": {"maker_quote_size": 1.0}},
        time_mapper=TimeMapper.from_wall_and_mono(wall_ms=1_000_000, mono_ns=1_000_000_000),
        broker=None,
        run_epoch_ms=1_000_000,
        readiness_config=MarketReadinessConfig(
            book_max_age_ms=30_000,
            book_max_spread_bps=500.0,
            depth_target_qty=1.0,
            pstar_max_age_ms=30_000,
        ),
    )


class TestRuntimeNoActiveMarketHaltQuoting(unittest.TestCase):
    def test_rollover_guard_blocks_quote_actions_during_none_found_retry_window(self) -> None:
        runtime = _runtime(["yes", "no"])
        now_ms = 20_000
        runtime.activate_rollover_guard(
            token_ids=["yes", "no"],
            quiet_until_ms=now_ms + 10_000,
            require_readiness=True,
        )
        reasons = runtime.quote_guard_reasons("yes", now_ms + 1_000)
        self.assertIn("ROLLOVER_QUIET_WINDOW", reasons)
        self.assertIn("ROLLOVER_READINESS_BLOCK", reasons)


if __name__ == "__main__":
    unittest.main()
