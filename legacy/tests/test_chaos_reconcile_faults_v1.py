import asyncio
import tempfile
import time
import unittest
from pathlib import Path

from core.broker_base import BrokerEvent, BrokerSnapshot, OrderIntent
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
        self.idx = 0
        self.snapshots = [
            BrokerSnapshot(open_orders={"b1": {"order_id": "b1", "token_id": "token-1", "side": "buy", "price": 0.49, "size": 1.0}}, meta={}),
            BrokerSnapshot(open_orders={"b1": {"order_id": "b1", "token_id": "token-1", "side": "buy", "price": 0.49, "size": 1.0}}, meta={}),
            BrokerSnapshot(open_orders={"b1": {"order_id": "b1", "token_id": "token-1", "side": "buy", "price": 0.49, "size": 1.0}}, meta={}),
            BrokerSnapshot(open_orders={}, meta={}),
            BrokerSnapshot(open_orders={}, meta={}),
            BrokerSnapshot(open_orders={}, meta={}),
        ]

    def snapshot(self):
        snap = self.snapshots[min(self.idx, len(self.snapshots) - 1)]
        self.idx += 1
        return snap

    def submit(self, intent: OrderIntent):
        _ = intent
        return []

    def cancel(self, order_id: str):
        _ = order_id
        return []

    def replace(self, order_id: str, new_intent: OrderIntent):
        _ = (order_id, new_intent)
        return []


class TestChaosReconcileFaultsV1(unittest.TestCase):
    def _build_runtime(self, db_path: Path) -> RuntimeEngine:
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
        return RuntimeEngine(
            mode="TRADE",
            db=SQLiteStore(db_path),
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
                    "onchain_disagree_freeze_cycles": 6,
                    "reconcile_clean_unfreeze_cycles": 3,
                    "ws_starvation_max_ms": 100_000.0,
                },
                "reconciliation": {},
            },
            time_mapper=TimeMapper.from_wall_and_mono(wall_ms=now_ms, mono_ns=time.monotonic_ns()),
            broker=_PatternBroker(),
            run_epoch_ms=now_ms,
            run_id="run-test",
        )

    def _run_fault_sequence(self, runtime: RuntimeEngine) -> list[str]:
        base = int(time.time() * 1000)
        submit = BrokerEvent(
            event_type="order_submit",
            order_id="o-1",
            payload={"t_event_wall_ms": base, "t_send_wall_ms": base, "price": 0.5, "size": 1.0},
        )
        delayed_ack = BrokerEvent(
            event_type="order_ack",
            order_id="o-1",
            payload={"t_event_wall_ms": base + 500, "t_ack_wall_ms": base + 500, "price": 0.5, "size": 1.0},
        )
        fill = BrokerEvent(
            event_type="order_fill",
            order_id="o-1",
            payload={
                "fill_event_id": "fill-1",
                "t_event_wall_ms": base + 1000,
                "t_fill_wall_ms": base + 1000,
                "fill_price": 0.5,
                "fill_size": 1.0,
                "fees_bps": 2.0,
            },
        )
        runtime._handle_broker_event("token-1", "buy", submit, "d-1")
        runtime._handle_broker_event("token-1", "buy", delayed_ack, "d-1")
        runtime._handle_broker_event("token-1", "buy", fill, "d-1")
        runtime._handle_broker_event("token-1", "buy", fill, "d-1")  # duplicate replay
        self.assertEqual(len(runtime.send_ack_samples), 1)
        self.assertEqual(float(runtime.send_ack_samples[0]), 500.0)
        fills = runtime.db.query("SELECT COUNT(*) FROM fills")
        self.assertEqual(int(fills[0][0]), 1)

        now = base + 2_000
        for _ in range(6):
            asyncio.run(runtime._record_reconciliation(now))
            now += 1_000
        rows = runtime.db.query(
            """
            SELECT code
            FROM alerts
            WHERE code IN ('RECONCILIATION_FROZEN_EDGE', 'RECONCILIATION_UNFROZEN_EDGE')
            ORDER BY ts_ms, code
            """
        )
        return [str(row[0]) for row in rows]

    def test_fault_replay_is_deterministic(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db1 = Path(tmp) / "r1.db"
            runtime1 = self._build_runtime(db1)
            seq1 = self._run_fault_sequence(runtime1)
            runtime1.trade_tape.close()
            runtime1.decision_tape.close()
            runtime1.db.close()

            db2 = Path(tmp) / "r2.db"
            runtime2 = self._build_runtime(db2)
            seq2 = self._run_fault_sequence(runtime2)
            runtime2.trade_tape.close()
            runtime2.decision_tape.close()
            runtime2.db.close()

            self.assertEqual(seq1, seq2)
            self.assertEqual(seq1, ["RECONCILIATION_FROZEN_EDGE", "RECONCILIATION_UNFROZEN_EDGE"])


if __name__ == "__main__":
    unittest.main()
