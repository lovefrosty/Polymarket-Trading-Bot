import tempfile
import time
import unittest
from pathlib import Path
import re

from core.broker_base import BrokerEvent
from core.decision_tape import DecisionTape, TimeMapper
from core.order_book import OrderBook
from core.policy_gate import PolicyThresholds
from core.pstar import PStarBuilder
from core.sqlite_store import SQLiteStore
from core.trade_tape import TradeTape
from core.validators import OrderConstraints
from scripts.run_system import RuntimeEngine


class TestSQLiteOrderEventContract(unittest.TestCase):
    EVENT_ID_RE = re.compile(r"^[a-z_]+:\d{10}:[0-9a-f]{12}$")

    def _runtime(self, db_path: Path) -> RuntimeEngine:
        now_ms = int(time.time() * 1000)
        return RuntimeEngine(
            mode="PAPER",
            db=SQLiteStore(db_path),
            decision_tape=DecisionTape(log_dir=str(db_path.parent), run_id="run-test"),
            trade_tape=TradeTape(log_dir=str(db_path.parent), run_id="run-test"),
            books={"token-1": OrderBook(asset_id="token-1", bids={}, asks={})},
            constraints={
                "token-1": OrderConstraints(
                    min_tick=0.01,
                    min_size=1.0,
                    min_price=0.01,
                    max_price=0.99,
                    max_spread_bps=200.0,
                    max_slippage_bps=150.0,
                    max_book_staleness_ms=5_000,
                )
            },
            market_meta={"token-1": {"slug": "btc-updown-15m-1", "condition_id": "cond-1", "reference_symbol": "BTC"}},
            pstar_builder=PStarBuilder(max_age_ms=3_000, freeze_disagree_bps=50.0),
            policy_thresholds=PolicyThresholds(max_book_age_ms=2_000),
            constitution={"execution": {"maker_quote_size": 1.0}, "policy": {}, "trading": {"ws_starvation_max_ms": 100_000}},
            time_mapper=TimeMapper.from_wall_and_mono(wall_ms=now_ms, mono_ns=time.monotonic_ns()),
            broker=None,
            run_epoch_ms=now_ms,
            run_id="run-test",
        )

    def test_required_order_and_fill_fields_written(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "runtime.db"
            runtime = self._runtime(db_path)
            base = int(time.time() * 1000)
            runtime._handle_broker_event(
                "token-1",
                "buy",
                BrokerEvent(
                    event_type="order_submit",
                    order_id="ord-1",
                    payload={"t_event_wall_ms": base, "status": "submitted", "size": 1.0, "price": 0.5},
                ),
                "d-1",
            )
            runtime._handle_broker_event(
                "token-1",
                "buy",
                BrokerEvent(
                    event_type="order_fill",
                    order_id="ord-1",
                    payload={
                        "fill_event_id": "fill-1",
                        "t_event_wall_ms": base + 1,
                        "t_fill_wall_ms": base + 1,
                        "fill_size": 1.0,
                        "fill_price": 0.5,
                        "mode": "MAKE",
                    },
                ),
                "d-1",
            )

            orders = runtime.db.query(
                """
                SELECT run_id, ts_ms, event_id, mode, market_slug, condition_id, token_id, order_id, side, price, qty, status, reason_code
                FROM orders
                ORDER BY ts_ms DESC
                LIMIT 1
                """
            )
            fills = runtime.db.query(
                """
                SELECT run_id, ts_ms, event_id, mode, market_slug, condition_id, token_id, order_id, side, fill_price, fill_qty, reason_code
                FROM fills
                ORDER BY ts_ms DESC
                LIMIT 1
                """
            )
            self.assertEqual(orders[0][0], "run-test")
            self.assertEqual(orders[0][3], "PAPER")
            self.assertEqual(orders[0][4], "btc-updown-15m-1")
            self.assertEqual(orders[0][5], "cond-1")
            self.assertEqual(fills[0][0], "run-test")
            self.assertEqual(fills[0][3], "PAPER")
            self.assertEqual(fills[0][4], "btc-updown-15m-1")
            self.assertEqual(fills[0][5], "cond-1")
            self.assertRegex(str(orders[0][2]), self.EVENT_ID_RE)
            self.assertEqual(str(fills[0][2]), "fill-1")
            runtime.trade_tape.close()
            runtime.decision_tape.close()
            runtime.db.close()

    def test_deterministic_fallback_event_ids_replay(self) -> None:
        def _run_once(db_path: Path) -> tuple[list[tuple[str, str, str]], list[tuple[str, str]], list[str]]:
            runtime = self._runtime(db_path)
            base = 1_700_000_123_000
            runtime._handle_broker_event(
                "token-1",
                "buy",
                BrokerEvent(
                    event_type="order_submit",
                    order_id="ord-deterministic",
                    payload={"t_event_wall_ms": base, "status": "submitted", "size": 1.0, "price": 0.5},
                ),
                "d-det",
            )
            runtime._handle_broker_event(
                "token-1",
                "buy",
                BrokerEvent(
                    event_type="order_ack",
                    order_id="ord-deterministic",
                    payload={
                        "t_event_wall_ms": base + 1,
                        "t_send_wall_ms": base,
                        "t_ack_wall_ms": base + 1,
                        "status": "accepted",
                    },
                ),
                "d-det",
            )
            runtime._handle_broker_event(
                "token-1",
                "buy",
                BrokerEvent(
                    event_type="order_fill",
                    order_id="ord-deterministic",
                    payload={
                        "t_event_wall_ms": base + 2,
                        "t_fill_wall_ms": base + 2,
                        "fill_size": 1.0,
                        "fill_price": 0.5,
                        "mode": "MAKE",
                    },
                ),
                "d-det",
            )
            orders = runtime.db.query("SELECT event_id, status, order_id FROM orders ORDER BY ts_ms, event_id")
            fills = runtime.db.query("SELECT event_id, order_id FROM fills ORDER BY ts_ms, event_id")
            lat = runtime.db.query("SELECT event_id FROM exec_latency ORDER BY ts_ms, event_id")
            runtime.trade_tape.close()
            runtime.decision_tape.close()
            runtime.db.close()
            return (
                [(str(event_id), str(status), str(order_id)) for event_id, status, order_id in orders],
                [(str(event_id), str(order_id)) for event_id, order_id in fills],
                [str(event_id) for (event_id,) in lat],
            )

        with tempfile.TemporaryDirectory() as tmp1, tempfile.TemporaryDirectory() as tmp2:
            run1 = _run_once(Path(tmp1) / "runtime.db")
            run2 = _run_once(Path(tmp2) / "runtime.db")
            self.assertEqual(run1, run2)
            self.assertTrue(all(self.EVENT_ID_RE.match(event_id) for event_id, _, _ in run1[0]))
            self.assertTrue(all(event_id.startswith("derived:") for event_id, _ in run1[1]))
            self.assertTrue(all(self.EVENT_ID_RE.match(event_id) for event_id in run1[2]))

    def test_partial_fill_updates_inventory_and_next_quote_bias(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "runtime.db"
            runtime = self._runtime(db_path)
            base = 1_700_000_456_000
            token_id = "token-1"
            constraint = runtime.constraints[token_id]

            before_bid, before_ask = runtime._compute_quotes(
                q=0.50,
                mid=0.50,
                constraint=constraint,
                half_spread_bps=40.0,
                inventory_skew=float(runtime.inventory_yes[token_id] * 0.0025),
                risk_padding_bps=5.0,
            )

            runtime._handle_broker_event(
                token_id,
                "buy",
                BrokerEvent(
                    event_type="order_submit",
                    order_id="ord-partial",
                    payload={"t_event_wall_ms": base, "status": "submitted", "size": 10.0, "price": 0.50},
                ),
                "d-partial",
            )
            runtime._handle_broker_event(
                token_id,
                "buy",
                BrokerEvent(
                    event_type="order_fill",
                    order_id="ord-partial",
                    payload={
                        "t_event_wall_ms": base + 5,
                        "t_fill_wall_ms": base + 5,
                        "fill_size": 4.0,
                        "fill_price": 0.50,
                        "mode": "MAKE",
                    },
                ),
                "d-partial",
            )

            self.assertAlmostEqual(float(runtime.inventory_yes[token_id]), 4.0, places=9)
            self.assertEqual(len(runtime._partial_fill_ts_by_token[token_id]), 1)

            after_bid, after_ask = runtime._compute_quotes(
                q=0.50,
                mid=0.50,
                constraint=constraint,
                half_spread_bps=40.0,
                inventory_skew=float(runtime.inventory_yes[token_id] * 0.0025),
                risk_padding_bps=5.0,
            )
            self.assertLess(after_bid, before_bid)
            self.assertLess(after_ask, before_ask)
            runtime.trade_tape.close()
            runtime.decision_tape.close()
            runtime.db.close()


if __name__ == "__main__":
    unittest.main()
