import asyncio
import tempfile
import time
import unittest
from pathlib import Path

from core.decision_tape import DecisionTape, TimeMapper
from core.order_book import OrderBook
from core.policy_gate import PolicyThresholds
from core.pstar import PStarBuilder
from core.sqlite_store import SQLiteStore
from core.trade_tape import TradeTape
from core.validators import OrderConstraints
from scripts.run_system import RuntimeEngine


class TestClockDriftFreezeV1(unittest.TestCase):
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
        runtime = RuntimeEngine(
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
                "trading": {"clock_drift_max_ms": 250.0, "ws_starvation_max_ms": 10_000.0},
            },
            time_mapper=TimeMapper.from_wall_and_mono(wall_ms=now_ms, mono_ns=time.monotonic_ns()),
            broker=None,
            run_epoch_ms=now_ms,
            run_id="run-test",
        )
        runtime._snapshot_book("token-1", runtime.books["token-1"], now_ms)
        return runtime

    def test_clock_drift_triggers_liveness_freeze(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime = self._build_runtime(Path(tmp) / "runtime.db")
            now_ms = int(time.time() * 1000)
            asyncio.run(
                runtime.run_stats_cycle(
                    now_ms,
                    liveness_inputs={
                        "clock_drift_ms": 1000.0,
                        "sequence_gap_rate_per_min": 0.0,
                        "sequence_gap_count_1m": 0,
                        "active_market_lag_ms": 5.0,
                    },
                )
            )
            row = runtime.db.query(
                "SELECT freeze_state, reason_codes FROM liveness_stats ORDER BY ts_ms DESC LIMIT 1"
            )[0]
            self.assertEqual(int(row[0]), 1)
            self.assertIn("E_CLOCK_DRIFT_HIGH", str(row[1]))
            alerts = runtime.db.query(
                "SELECT COUNT(*) FROM alerts WHERE code='LIVENESS_FROZEN_EDGE'"
            )
            self.assertEqual(int(alerts[0][0]), 1)
            runtime.trade_tape.close()
            runtime.decision_tape.close()
            runtime.db.close()


if __name__ == "__main__":
    unittest.main()
