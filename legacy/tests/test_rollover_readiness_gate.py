import unittest

from core.decision_tape import TimeMapper
from core.order_book import OrderBook
from core.policy_gate import PolicyThresholds
from core.pstar import PStarBuilder
from core.validators import OrderConstraints
from scripts.run_system import (
    MarketReadinessConfig,
    RuntimeEngine,
    _pending_books_liveness,
    _rollover_commit_decision,
)


class _DummyDB:
    def insert(self, table: str, row: dict) -> None:  # pragma: no cover
        _ = (table, row)


class _DummyDecisionTape:
    run_id = "test"

    def write(self, record) -> None:  # pragma: no cover
        _ = record


class _DummyTradeTape:
    run_id = "test"

    def __init__(self) -> None:
        self._eid = 0

    def next_event_id(self) -> int:  # pragma: no cover
        self._eid += 1
        return self._eid

    def write(self, payload) -> None:  # pragma: no cover
        _ = payload


def _constraints(tokens: list[str]) -> dict[str, OrderConstraints]:
    return {
        token: OrderConstraints(
            min_tick=0.01,
            min_size=1.0,
            min_price=0.01,
            max_price=0.99,
            max_spread_bps=1000.0,
            max_slippage_bps=1000.0,
            max_book_staleness_ms=20_000,
        )
        for token in tokens
    }


def _runtime(tokens: list[str]) -> RuntimeEngine:
    books = {token: OrderBook(asset_id=token, bids={}, asks={}) for token in tokens}
    market_meta = {token: {"reference_symbol": "BTC", "slug": "btc-updown-15m-1700000000"} for token in tokens}
    thresholds = PolicyThresholds(
        max_book_age_ms=10_000,
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
        constraints=_constraints(tokens),
        market_meta=market_meta,
        pstar_builder=PStarBuilder(max_age_ms=60_000, freeze_disagree_bps=1000.0),
        policy_thresholds=thresholds,
        constitution={"trading": {}, "policy": {}, "execution": {"maker_quote_size": 1.0}},
        time_mapper=TimeMapper.from_wall_and_mono(wall_ms=1_000_000, mono_ns=1_000_000_000),
        broker=None,
        run_epoch_ms=1_000_000,
        readiness_config=MarketReadinessConfig(
            book_max_age_ms=5_000,
            book_max_spread_bps=100.0,
            depth_target_qty=1.0,
            pstar_max_age_ms=5_000,
        ),
    )


class TestRolloverReadinessGate(unittest.TestCase):
    def test_readiness_blocks_commit_until_escape_hatch_then_quiet_window(self) -> None:
        runtime = _runtime(["yes", "no"])
        now_ms = 10_000

        for token in ["yes", "no"]:
            book = runtime.books[token]
            book.apply_snapshot(
                bids=[(0.10, 0.05)],
                asks=[(0.90, 0.05)],
                event_ts_ms=now_ms - 50,
                recv_mono_ns=1_000_000_000,
                last_hash=None,
            )

        runtime.on_reference("spot", "BTC", 50000.0, ts_event_ms=now_ms - 20, ts_recv_ms=now_ms - 20)
        runtime.on_reference("perp", "BTC", 50000.0, ts_event_ms=now_ms - 20, ts_recv_ms=now_ms - 20)

        readiness = runtime.evaluate_market_readiness(["yes", "no"], now_ms=now_ms)
        self.assertFalse(readiness.ready)
        self.assertIn("C_SPREAD_TOO_WIDE", readiness.reason_codes)
        self.assertIn("C_DEPTH_TOO_THIN", readiness.reason_codes)

        liveness_ok, liveness_diag = _pending_books_liveness(["yes", "no"], runtime.books)
        self.assertTrue(liveness_ok)
        self.assertTrue(liveness_diag["all_tokens_seen"])

        pre_grace = _rollover_commit_decision(
            now_ms=now_ms,
            readiness_ready=readiness.ready,
            escape_hatch_open=False,
            liveness_ok=liveness_ok,
        )
        self.assertEqual(pre_grace.action, "RETRY")

        post_grace = _rollover_commit_decision(
            now_ms=now_ms,
            readiness_ready=readiness.ready,
            escape_hatch_open=True,
            liveness_ok=liveness_ok,
        )
        self.assertEqual(post_grace.action, "COMMIT")
        self.assertTrue(post_grace.force_observe_only)

        runtime.activate_rollover_guard(["yes", "no"], quiet_until_ms=now_ms + 500, require_readiness=True)
        reasons = runtime.quote_guard_reasons("yes", now_ms + 100)
        self.assertIn("ROLLOVER_QUIET_WINDOW", reasons)
        self.assertIn("ROLLOVER_READINESS_BLOCK", reasons)

    def test_adopted_market_guard_clears_after_quiet_window_without_full_readiness(self) -> None:
        runtime = _runtime(["yes", "no"])
        now_ms = 10_000

        for token in ["yes", "no"]:
            book = runtime.books[token]
            book.apply_snapshot(
                bids=[(0.10, 5.0)],
                asks=[(0.11, 5.0)],
                event_ts_ms=now_ms - 50,
                recv_mono_ns=1_000_000_000,
                last_hash=None,
            )

        runtime.on_reference("spot", "BTC", 50000.0, ts_event_ms=now_ms - 20, ts_recv_ms=now_ms - 20)
        runtime.on_reference("perp", "BTC", 50000.0, ts_event_ms=now_ms - 20, ts_recv_ms=now_ms - 20)

        runtime.activate_rollover_guard(["yes", "no"], quiet_until_ms=now_ms + 100, require_readiness=False)
        reasons = runtime.quote_guard_reasons("yes", now_ms + 200)
        self.assertEqual(reasons, [])
        self.assertFalse(runtime.rollover_guard_status(now_ms + 200)["active"])


if __name__ == "__main__":
    unittest.main()
