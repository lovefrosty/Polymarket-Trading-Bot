import asyncio
import tempfile
import time
import unittest
from pathlib import Path

from core.broker_base import BrokerEvent, BrokerSnapshot, OrderIntent
from core.decision_tape import DecisionTape, TimeMapper
from core.order_book import OrderBook
from core.policy_gate import PolicyThresholds, PolicyVerdict
from core.pstar import PStarBuilder
from core.sqlite_store import SQLiteStore
from core.trade_tape import TradeTape
from core.validators import OrderConstraints
from scripts.run_system import RuntimeEngine


class _BrokerSpy:
    def __init__(self) -> None:
        self.submit_calls = []
        self.cancel_calls = []
        self.replace_calls = []

    def submit(self, intent: OrderIntent):
        self.submit_calls.append(intent.order_id)
        return []

    def cancel(self, order_id: str):
        self.cancel_calls.append(order_id)
        return [BrokerEvent(event_type="order_cancel", order_id=order_id, payload={"t_event_wall_ms": int(time.time() * 1000)})]

    def replace(self, order_id: str, new_intent: OrderIntent):
        self.replace_calls.append((order_id, new_intent.order_id))
        return []

    def snapshot(self):
        return BrokerSnapshot(open_orders={}, meta={})


class TestRestartAdoptsOpenOrdersNoDuplicates(unittest.TestCase):
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
        store = SQLiteStore(db_path)
        decision_tape = DecisionTape(log_dir=str(db_path.parent), run_id="run-test")
        trade_tape = TradeTape(log_dir=str(db_path.parent), run_id="run-test")
        return RuntimeEngine(
            mode="TRADE",
            db=store,
            decision_tape=decision_tape,
            trade_tape=trade_tape,
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

    def test_adopted_order_blocks_duplicate_submit_on_first_apply(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "runtime.db"
            broker = _BrokerSpy()
            runtime = self._build_runtime(db_path=db_path, broker=broker)
            now_ms = int(time.time() * 1000)
            snapshot = BrokerSnapshot(
                open_orders={
                    "order-a": {
                        "order_id": "order-a",
                        "token_id": "token-1",
                        "side": "buy",
                        "price": 0.49,
                        "size": 1.0,
                        "client_order_id": "client-a",
                        "quote_group_id": "g-a",
                    }
                },
                meta={},
            )
            diag = asyncio.run(runtime.adopt_open_orders_with_cleanup(snapshot=snapshot, now_ms=now_ms))
            self.assertEqual(diag["adopted"], 1)
            self.assertEqual(diag["duplicates_canceled"], 0)
            self.assertIn("buy", runtime.open_quotes["token-1"])

            asyncio.run(
                runtime._apply_side(
                    token_id="token-1",
                    side="buy",
                    price=0.49,
                    qty=1.0,
                    constraint=runtime.constraints["token-1"],
                    verdict=PolicyVerdict(allow=True, action="QUOTE", reason_codes=[], diagnostics={}),
                    now_ms=now_ms + 1,
                    decision_id="decision-1",
                )
            )
            self.assertEqual(broker.submit_calls, [])
            self.assertEqual(broker.replace_calls, [])

            runtime.trade_tape.close()
            runtime.decision_tape.close()
            runtime.db.close()

    def test_invariant_violation_fails_fast_on_duplicate_slot(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "runtime.db"
            broker = _BrokerSpy()
            runtime = self._build_runtime(db_path=db_path, broker=broker)
            now_ms = int(time.time() * 1000)
            snapshot = BrokerSnapshot(
                open_orders={
                    "order-a": {
                        "order_id": "order-a",
                        "token_id": "token-1",
                        "side": "buy",
                        "price": 0.49,
                        "size": 1.0,
                    },
                    "order-b": {
                        "order_id": "order-b",
                        "token_id": "token-1",
                        "side": "buy",
                        "price": 0.50,
                        "size": 1.0,
                    },
                },
                meta={},
            )
            with self.assertRaisesRegex(RuntimeError, "RECON_STARTUP_INVARIANT_VIOLATION"):
                asyncio.run(runtime.adopt_open_orders_with_cleanup(snapshot=snapshot, now_ms=now_ms))
            rows = runtime.db.query(
                "SELECT COUNT(*) FROM recovery_events WHERE recovery_action='STARTUP_QUOTING_INVARIANT_CHECK'"
            )
            self.assertEqual(int(rows[0][0]), 1)
            runtime.trade_tape.close()
            runtime.decision_tape.close()
            runtime.db.close()


if __name__ == "__main__":
    unittest.main()
