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


class _BrokerSpy:
    def __init__(self) -> None:
        self.cancel_calls = []

    def submit(self, intent: OrderIntent):
        _ = intent
        return []

    def cancel(self, order_id: str):
        self.cancel_calls.append(order_id)
        return [BrokerEvent(event_type="order_cancel", order_id=order_id, payload={"t_event_wall_ms": int(time.time() * 1000)})]

    def replace(self, order_id: str, new_intent: OrderIntent):
        _ = (order_id, new_intent)
        return []

    def snapshot(self):
        return BrokerSnapshot(open_orders={}, meta={})


class TestUnknownOrderQuarantineStartupV1(unittest.TestCase):
    def _build_runtime(self, db_path: Path, broker: _BrokerSpy) -> RuntimeEngine:
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
                    "single_level_quoting": True,
                    "startup_allow_exact_duplicate_cleanup": False,
                },
            },
            time_mapper=TimeMapper.from_wall_and_mono(wall_ms=now_ms, mono_ns=time.monotonic_ns()),
            broker=broker,
            run_epoch_ms=now_ms,
            run_id="run-test",
        )

    def test_unknown_orders_are_quarantined_without_auto_cancel(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            broker = _BrokerSpy()
            runtime = self._build_runtime(Path(tmp) / "runtime.db", broker)
            now_ms = int(time.time() * 1000)
            snapshot = BrokerSnapshot(
                open_orders={
                    "order-x": {
                        "order_id": "order-x",
                        "token_id": "token-other",
                        "side": "buy",
                        "price": 0.60,
                        "size": 2.0,
                        "updated_ts_ms": now_ms,
                    }
                },
                meta={},
            )
            diag = asyncio.run(runtime.adopt_open_orders_with_cleanup(snapshot=snapshot, now_ms=now_ms))
            self.assertEqual(diag["unknown_quarantined"], 1)
            self.assertEqual(diag["duplicates_canceled"], 0)
            self.assertEqual(broker.cancel_calls, [])
            self.assertTrue(runtime._reconciliation_frozen)
            self.assertEqual(runtime._reconciliation_freeze_reason, "RECON_UNKNOWN_ORDER_QUARANTINE")
            alerts = runtime.db.query(
                "SELECT COUNT(*) FROM alerts WHERE code='RECON_UNKNOWN_ORDER_QUARANTINE'"
            )
            self.assertEqual(int(alerts[0][0]), 1)
            runtime.trade_tape.close()
            runtime.decision_tape.close()
            runtime.db.close()


if __name__ == "__main__":
    unittest.main()
