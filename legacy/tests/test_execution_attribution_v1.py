import asyncio
import tempfile
import time
import unittest
from pathlib import Path

from core.broker_base import BrokerEvent
from core.decision_tape import DecisionTape, TimeMapper
from core.order_book import OrderBook
from core.policy_gate import PolicyThresholds
from core.pstar import PStarBuilder
from core.sqlite_store import SQLiteStore
from core.trade_tape import TradeTape
from core.validators import OrderConstraints
from scripts.run_system import RuntimeEngine


class TestExecutionAttributionV1(unittest.TestCase):
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
            mode="OBSERVE",
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
                "trading": {"ws_starvation_max_ms": 100_000},
            },
            time_mapper=TimeMapper.from_wall_and_mono(wall_ms=now_ms, mono_ns=time.monotonic_ns()),
            broker=None,
            run_epoch_ms=now_ms,
            run_id="run-test",
        )

    def _set_mid(self, runtime: RuntimeEngine, ts_ms: int, mid: float) -> None:
        book = runtime.books["token-1"]
        bid = max(0.01, float(mid) - 0.01)
        ask = min(0.99, float(mid) + 0.01)
        book.apply_snapshot(
            bids=[(bid, 10.0)],
            asks=[(ask, 10.0)],
            event_ts_ms=int(ts_ms),
            recv_mono_ns=time.monotonic_ns(),
            last_hash=None,
        )
        runtime._snapshot_book("token-1", book, int(ts_ms))

    def test_execution_attribution_and_duplicate_fill_idempotency(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "runtime.db"
            runtime = self._build_runtime(db_path)
            base = int(time.time() * 1000)
            self._set_mid(runtime, base - 50, 0.5000)

            submit = BrokerEvent(
                event_type="order_submit",
                order_id="o-1",
                payload={
                    "t_event_wall_ms": base,
                    "t_send_wall_ms": base,
                    "price": 0.5000,
                    "size": 1.0,
                    "client_order_id": "c-1",
                    "asset_id": "token-1",
                    "side": "buy",
                    "mode": "MAKE",
                },
            )
            ack = BrokerEvent(
                event_type="order_ack",
                order_id="o-1",
                payload={
                    "t_event_wall_ms": base + 20,
                    "t_ack_wall_ms": base + 20,
                    "price": 0.5000,
                    "size": 1.0,
                    "client_order_id": "c-1",
                    "asset_id": "token-1",
                    "side": "buy",
                    "mode": "MAKE",
                },
            )
            fill = BrokerEvent(
                event_type="order_fill",
                order_id="o-1",
                payload={
                    "fill_event_id": "fill-1",
                    "t_event_wall_ms": base + 1000,
                    "t_fill_wall_ms": base + 1000,
                    "fill_price": 0.5000,
                    "fill_size": 1.0,
                    "fees_bps": 2.0,
                    "client_order_id": "c-1",
                    "asset_id": "token-1",
                    "side": "buy",
                    "mode": "MAKE",
                },
            )

            runtime._handle_broker_event("token-1", "buy", submit, "d-1")
            runtime._handle_broker_event("token-1", "buy", ack, "d-1")
            self._set_mid(runtime, base + 1000, 0.5010)
            runtime._handle_broker_event("token-1", "buy", fill, "d-1")
            self._set_mid(runtime, base + 2000, 0.5020)
            self._set_mid(runtime, base + 6000, 0.5030)
            self._set_mid(runtime, base + 31_000, 0.5040)

            asyncio.run(
                runtime.run_stats_cycle(
                    base + 31_001,
                    liveness_inputs={
                        "clock_drift_ms": 0.0,
                        "sequence_gap_rate_per_min": 0.0,
                        "sequence_gap_count_1m": 0,
                        "active_market_lag_ms": 0.0,
                    },
                )
            )

            rows = runtime.db.query(
                "SELECT COUNT(*), MAX(markout_5s_bps), MAX(net_edge_bps) FROM execution_quality"
            )
            self.assertEqual(int(rows[0][0]), 1)
            self.assertGreater(float(rows[0][1]), 0.0)
            self.assertIsNotNone(rows[0][2])

            # Replay identical fill event id: must be skipped.
            runtime._handle_broker_event("token-1", "buy", fill, "d-1")
            asyncio.run(
                runtime.run_stats_cycle(
                    base + 32_000,
                    liveness_inputs={
                        "clock_drift_ms": 0.0,
                        "sequence_gap_rate_per_min": 0.0,
                        "sequence_gap_count_1m": 0,
                        "active_market_lag_ms": 0.0,
                    },
                )
            )
            fills = runtime.db.query("SELECT COUNT(*) FROM fills")
            quality = runtime.db.query("SELECT COUNT(*) FROM execution_quality")
            self.assertEqual(int(fills[0][0]), 1)
            self.assertEqual(int(quality[0][0]), 1)

            runtime.trade_tape.close()
            runtime.decision_tape.close()
            runtime.db.close()


if __name__ == "__main__":
    unittest.main()
