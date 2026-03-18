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


class _BrokerCancelSpy:
    def __init__(self) -> None:
        self.cancel_calls = []

    def submit(self, intent: OrderIntent):
        return []

    def cancel(self, order_id: str):
        self.cancel_calls.append(order_id)
        return [BrokerEvent(event_type="order_cancel", order_id=order_id, payload={"t_event_wall_ms": int(time.time() * 1000)})]

    def replace(self, order_id: str, new_intent: OrderIntent):
        return []

    def snapshot(self):
        return BrokerSnapshot(open_orders={}, meta={})


class TestDuplicateOpenOrdersCancelExtras(unittest.TestCase):
    def _build_runtime(
        self,
        db_path: Path,
        broker: _BrokerCancelSpy,
        *,
        allow_exact_duplicate_cleanup: bool,
    ) -> RuntimeEngine:
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
                    "single_level_quoting": True,
                    "startup_allow_exact_duplicate_cleanup": bool(allow_exact_duplicate_cleanup),
                },
            },
            time_mapper=TimeMapper.from_wall_and_mono(wall_ms=now_ms, mono_ns=time.monotonic_ns()),
            broker=broker,
            run_epoch_ms=now_ms,
            run_id="run-test",
        )

    def test_cancel_extras_deterministically(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "runtime.db"
            broker = _BrokerCancelSpy()
            runtime = self._build_runtime(
                db_path=db_path,
                broker=broker,
                allow_exact_duplicate_cleanup=True,
            )
            now_ms = int(time.time() * 1000)
            snapshot = BrokerSnapshot(
                open_orders={
                    "order-1": {
                        "order_id": "order-1",
                        "token_id": "token-1",
                        "side": "buy",
                        "price": 0.49,
                        "size": 1.0,
                        "updated_ts_ms": 10,
                    },
                    "order-2": {
                        "order_id": "order-2",
                        "token_id": "token-1",
                        "side": "buy",
                        "price": 0.49,
                        "size": 1.0,
                        "updated_ts_ms": 20,
                    },
                },
                meta={},
            )
            diag = asyncio.run(runtime.adopt_open_orders_with_cleanup(snapshot=snapshot, now_ms=now_ms))
            self.assertEqual(diag["adopted"], 1)
            self.assertEqual(diag["duplicates_canceled"], 1)
            self.assertEqual(broker.cancel_calls, ["order-1"])
            self.assertEqual(runtime.open_quotes["token-1"]["buy"].order_id, "order-2")

            rows = runtime.db.query(
                "SELECT COUNT(*) FROM recovery_events WHERE recovery_action='CANCEL_DUPLICATE_OPEN_ORDER'"
            )
            self.assertEqual(int(rows[0][0]), 1)
            invariant = runtime.db.query(
                "SELECT COUNT(*) FROM recovery_events WHERE recovery_action='STARTUP_QUOTING_INVARIANT_CHECK'"
            )
            self.assertEqual(int(invariant[0][0]), 1)

            runtime.trade_tape.close()
            runtime.decision_tape.close()
            runtime.db.close()

    def test_strict_mode_raises_on_non_exact_duplicates(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "runtime.db"
            broker = _BrokerCancelSpy()
            runtime = self._build_runtime(
                db_path=db_path,
                broker=broker,
                allow_exact_duplicate_cleanup=False,
            )
            now_ms = int(time.time() * 1000)
            snapshot = BrokerSnapshot(
                open_orders={
                    "order-1": {
                        "order_id": "order-1",
                        "token_id": "token-1",
                        "side": "buy",
                        "price": 0.48,
                        "size": 1.0,
                    },
                    "order-2": {
                        "order_id": "order-2",
                        "token_id": "token-1",
                        "side": "buy",
                        "price": 0.49,
                        "size": 1.0,
                    },
                },
                meta={},
            )
            with self.assertRaisesRegex(RuntimeError, "RECON_STARTUP_INVARIANT_VIOLATION"):
                asyncio.run(runtime.adopt_open_orders_with_cleanup(snapshot=snapshot, now_ms=now_ms))
            self.assertEqual(broker.cancel_calls, [])
            runtime.trade_tape.close()
            runtime.decision_tape.close()
            runtime.db.close()


if __name__ == "__main__":
    unittest.main()
