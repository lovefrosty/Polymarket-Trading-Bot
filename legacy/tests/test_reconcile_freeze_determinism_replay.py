import asyncio
import tempfile
import time
import unittest
from pathlib import Path

from core.broker_base import BrokerSnapshot, OrderIntent
from core.decision_tape import DecisionTape, TimeMapper
from core.order_book import OrderBook
from core.policy_gate import PolicyThresholds
from core.pstar import PStarBuilder
from core.sqlite_store import SQLiteStore
from core.trade_tape import TradeTape
from core.validators import OrderConstraints
from scripts.run_system import RuntimeEngine


class _PatternBroker:
    def __init__(self) -> None:
        self._idx = 0

    def submit(self, intent: OrderIntent):
        return []

    def cancel(self, order_id: str):
        return []

    def replace(self, order_id: str, new_intent: OrderIntent):
        return []

    def snapshot(self):
        pattern = [
            (10.0, 0.0),
            (10.0, 0.0),
            (10.0, 0.0),
            (0.0, 0.0),
            (0.0, 0.0),
            (0.0, 0.0),
        ]
        value = pattern[min(self._idx, len(pattern) - 1)]
        self._idx += 1
        return BrokerSnapshot(open_orders={}, meta={"broker_inventory": value[0], "onchain_inventory": value[1]})


def _build_runtime(db_path: Path, broker: _PatternBroker) -> RuntimeEngine:
    now_ms = int(time.time() * 1000)
    books = {"token-1": OrderBook(asset_id="token-1", bids={0.49: 10.0}, asks={0.51: 10.0})}
    constraints = {
        "token-1": OrderConstraints(
            min_tick=0.01,
            min_size=1.0,
            min_price=0.01,
            max_price=0.99,
            max_spread_bps=150.0,
            max_slippage_bps=120.0,
            max_book_staleness_ms=2000,
        )
    }
    store = SQLiteStore(db_path)
    return RuntimeEngine(
        mode="TRADE",
        db=store,
        decision_tape=DecisionTape(log_dir=str(db_path.parent), run_id="run-test"),
        trade_tape=TradeTape(log_dir=str(db_path.parent), run_id="run-test"),
        books=books,
        constraints=constraints,
        market_meta={"token-1": {"reference_symbol": "BTC", "slug": "m1"}},
        pstar_builder=PStarBuilder(max_age_ms=3000, freeze_disagree_bps=50.0),
        policy_thresholds=PolicyThresholds(max_book_age_ms=2000),
        constitution={
            "execution": {"maker_quote_size": 1.0},
            "policy": {},
            "trading": {
                "mismatch_freeze_cycles": 3,
                "reconcile_clean_unfreeze_cycles": 3,
                "mismatch_tolerance_qty": 0.01,
                "reconcile_period_ms": 1,
            },
        },
        time_mapper=TimeMapper.from_wall_and_mono(wall_ms=now_ms, mono_ns=time.monotonic_ns()),
        broker=broker,
        run_epoch_ms=now_ms,
        run_id="run-test",
    )


class TestReconcileFreezeDeterminismReplay(unittest.TestCase):
    def _run_pattern(self) -> tuple[list[tuple[str, int]], list[tuple[int, int]]]:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "runtime.db"
            runtime = _build_runtime(db_path=db_path, broker=_PatternBroker())
            base = 1_700_000_000_000
            for i in range(6):
                asyncio.run(runtime._record_reconciliation(base + i))
            edges = runtime.db.query(
                """
                SELECT code, ts_ms
                FROM alerts
                WHERE code IN ('RECONCILIATION_FROZEN_EDGE', 'RECONCILIATION_UNFROZEN_EDGE')
                ORDER BY ts_ms, code
                """
            )
            freeze_states = runtime.db.query(
                """
                SELECT ts_ms, freeze_state
                FROM reconciliation_stats
                ORDER BY ts_ms
                """
            )
            runtime.trade_tape.close()
            runtime.decision_tape.close()
            runtime.db.close()
            return [(str(code), int(ts)) for code, ts in edges], [(int(ts), int(state)) for ts, state in freeze_states]

    def test_replay_produces_same_freeze_edges(self) -> None:
        edges_1, states_1 = self._run_pattern()
        edges_2, states_2 = self._run_pattern()
        self.assertEqual(edges_1, edges_2)
        self.assertEqual(states_1, states_2)


if __name__ == "__main__":
    unittest.main()
