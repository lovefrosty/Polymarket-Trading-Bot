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


class _BrokerRecoveringMismatch:
    def __init__(self) -> None:
        self._idx = 0

    def submit(self, intent: OrderIntent):
        return []

    def cancel(self, order_id: str):
        return []

    def replace(self, order_id: str, new_intent: OrderIntent):
        return []

    def snapshot(self):
        self._idx += 1
        if self._idx <= 3:
            return BrokerSnapshot(open_orders={}, meta={"broker_inventory": 10.0, "onchain_inventory": 0.0})
        return BrokerSnapshot(open_orders={}, meta={"broker_inventory": 0.0, "onchain_inventory": 0.0})


class TestMismatchRecoversUnfreezes(unittest.TestCase):
    def _build_runtime(self, db_path: Path, broker: _BrokerRecoveringMismatch) -> RuntimeEngine:
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
                    "mismatch_tolerance_qty": 0.01,
                    "reconcile_period_ms": 1,
                    "reconcile_clean_unfreeze_cycles": 3,
                },
            },
            time_mapper=TimeMapper.from_wall_and_mono(wall_ms=now_ms, mono_ns=time.monotonic_ns()),
            broker=broker,
            run_epoch_ms=now_ms,
            run_id="run-test",
        )

    def test_mismatch_resolution_clears_freeze(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "runtime.db"
            broker = _BrokerRecoveringMismatch()
            runtime = self._build_runtime(db_path=db_path, broker=broker)
            now_ms = int(time.time() * 1000)
            for i in range(3):
                asyncio.run(runtime._record_reconciliation(now_ms + i))
            self.assertTrue(runtime._reconciliation_frozen)

            asyncio.run(runtime._record_reconciliation(now_ms + 5))
            self.assertTrue(runtime._reconciliation_frozen)
            asyncio.run(runtime._record_reconciliation(now_ms + 6))
            self.assertTrue(runtime._reconciliation_frozen)
            asyncio.run(runtime._record_reconciliation(now_ms + 7))
            self.assertFalse(runtime._reconciliation_frozen)
            resolved = runtime.db.query(
                "SELECT COUNT(*) FROM alerts WHERE code='RECONCILIATION_MISMATCH_RESOLVED'"
            )
            self.assertGreaterEqual(int(resolved[0][0]), 1)
            unfreeze_edge = runtime.db.query(
                "SELECT COUNT(*) FROM alerts WHERE code='RECONCILIATION_UNFROZEN_EDGE'"
            )
            self.assertEqual(int(unfreeze_edge[0][0]), 1)

            runtime.trade_tape.close()
            runtime.decision_tape.close()
            runtime.db.close()


if __name__ == "__main__":
    unittest.main()
