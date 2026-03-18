import asyncio
import tempfile
import time
import unittest
from pathlib import Path

from core.broker_base import BrokerSnapshot, OrderIntent
from core.decision_tape import DecisionTape, TimeMapper
from core.execution_fsm import ExecutionState
from core.order_book import OrderBook
from core.policy_gate import PolicyThresholds
from core.pstar import PStarBuilder
from core.sqlite_store import SQLiteStore
from core.trade_tape import TradeTape
from core.validators import OrderConstraints
from scripts.run_system import RuntimeEngine


class _BrokerWithMissedFill:
    def submit(self, intent: OrderIntent):
        return []

    def cancel(self, order_id: str):
        return []

    def replace(self, order_id: str, new_intent: OrderIntent):
        return []

    def snapshot(self):
        now_ms = int(time.time() * 1000)
        return BrokerSnapshot(
            open_orders={},
            meta={
                "broker_inventory": 1.0,
                "onchain_inventory": 1.0,
                "fill_events": [
                    {
                        "event_id": "fill-evt-1",
                        "order_id": "order-1",
                        "token_id": "token-1",
                        "side": "buy",
                        "fill_qty": 1.0,
                        "fill_price": 0.5,
                        "ts_ms": now_ms,
                    },
                    {
                        "event_id": "fill-evt-1",
                        "order_id": "order-1",
                        "token_id": "token-1",
                        "side": "buy",
                        "fill_qty": 1.0,
                        "fill_price": 0.5,
                        "ts_ms": now_ms,
                    },
                ],
            },
        )


class TestReconcileMissedFillUpdatesFSM(unittest.TestCase):
    def _build_runtime(self, db_path: Path, broker: _BrokerWithMissedFill) -> RuntimeEngine:
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
            constitution={"execution": {"maker_quote_size": 1.0}, "policy": {}, "trading": {}},
            time_mapper=TimeMapper.from_wall_and_mono(wall_ms=now_ms, mono_ns=time.monotonic_ns()),
            broker=broker,
            run_epoch_ms=now_ms,
            run_id="run-test",
        )

    def test_missed_fill_correction_updates_inventory_and_fsm(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "runtime.db"
            broker = _BrokerWithMissedFill()
            runtime = self._build_runtime(db_path=db_path, broker=broker)
            now_ms = int(time.time() * 1000)
            self.assertEqual(runtime.fsms["token-1"].status().state, ExecutionState.QUOTING_BOTH)

            asyncio.run(runtime._record_reconciliation(now_ms))
            asyncio.run(runtime._record_reconciliation(now_ms + 1))

            self.assertAlmostEqual(runtime.inventory_yes["token-1"], 1.0)
            self.assertEqual(runtime.fsms["token-1"].status().state, ExecutionState.ONE_SIDE_FILLED)
            rows = runtime.db.query(
                "SELECT COUNT(*) FROM recovery_events WHERE recovery_action='MISSED_FILL_CORRECTION'"
            )
            self.assertEqual(int(rows[0][0]), 1)
            dup_rows = runtime.db.query(
                "SELECT COUNT(*) FROM recovery_events WHERE recovery_action='MISSED_FILL_DUPLICATE_SKIPPED'"
            )
            self.assertGreaterEqual(int(dup_rows[0][0]), 1)
            seen = runtime.db.query("SELECT COUNT(*) FROM seen_fill_events")
            self.assertEqual(int(seen[0][0]), 1)

            runtime.trade_tape.close()
            runtime.decision_tape.close()
            runtime.db.close()

            runtime2 = self._build_runtime(db_path=db_path, broker=broker)
            self.assertEqual(runtime2.fsms["token-1"].status().state, ExecutionState.QUOTING_BOTH)
            asyncio.run(runtime2._record_reconciliation(now_ms + 2))
            self.assertAlmostEqual(runtime2.inventory_yes["token-1"], 0.0)
            rows_after_restart = runtime2.db.query(
                "SELECT COUNT(*) FROM recovery_events WHERE recovery_action='MISSED_FILL_CORRECTION'"
            )
            self.assertEqual(int(rows_after_restart[0][0]), 1)
            runtime2.trade_tape.close()
            runtime2.decision_tape.close()
            runtime2.db.close()


if __name__ == "__main__":
    unittest.main()
