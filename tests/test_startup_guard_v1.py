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
from scripts.run_system import RuntimeEngine, _handle_startup_guard_result


class TestStartupGuardV1(unittest.TestCase):
    def _build_runtime(self, db_path: Path) -> RuntimeEngine:
        now_ms = int(time.time() * 1000)
        books = {
            "token-1": OrderBook(asset_id="token-1", bids={0.49: 10.0}, asks={0.51: 10.0}),
        }
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
        runtime = RuntimeEngine(
            mode="OBSERVE",
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
            broker=None,
            run_epoch_ms=now_ms,
        )
        return runtime

    def test_no_ws_updates_blocks_live_modes_and_alerts_observe(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "runtime.db"
            runtime = self._build_runtime(db_path)
            ok, payload = asyncio.run(
                runtime.startup_feed_guard(
                    mode="PAPER",
                    tracked_symbols=["BTC"],
                    max_wait_secs=1,
                    min_updates_per_token=10,
                    max_book_age_ms=2000,
                    max_pstar_age_ms=3000,
                )
            )
            self.assertFalse(ok)
            self.assertTrue(_handle_startup_guard_result(runtime.db, "PAPER", ok, payload))
            self.assertFalse(_handle_startup_guard_result(runtime.db, "OBSERVE", ok, payload))
            paper_alerts = runtime.db.query("SELECT code FROM alerts WHERE code='FEED_NOT_WIRED'")
            observe_alerts = runtime.db.query("SELECT code FROM alerts WHERE code='FEED_NOT_WIRED_OBSERVE'")
            self.assertGreaterEqual(len(paper_alerts), 1)
            self.assertGreaterEqual(len(observe_alerts), 1)
            runtime.trade_tape.close()
            runtime.decision_tape.close()
            runtime.db.close()

    def test_minimal_fresh_updates_pass_guard(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "runtime.db"
            runtime = self._build_runtime(db_path)
            now_ms = int(time.time() * 1000)
            runtime.books["token-1"].last_event_ts_ms = now_ms - 20
            runtime.books["token-1"].last_recv_mono_ns = time.monotonic_ns()
            runtime.pstar_builder.ingest("spot", "BTC", 100.0, now_ms - 100, now_ms - 50)
            runtime.pstar_builder.ingest("perp", "BTC", 100.02, now_ms - 100, now_ms - 50)

            ok, payload = asyncio.run(
                runtime.startup_feed_guard(
                    mode="OBSERVE",
                    tracked_symbols=["BTC"],
                    max_wait_secs=1,
                    min_updates_per_token=10,
                    max_book_age_ms=2000,
                    max_pstar_age_ms=3000,
                )
            )
            self.assertTrue(ok, msg=str(payload))
            self.assertFalse(_handle_startup_guard_result(runtime.db, "OBSERVE", ok, payload))
            runtime.trade_tape.close()
            runtime.decision_tape.close()
            runtime.db.close()

    def test_partial_readiness_state_when_book_wired_but_reference_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "runtime.db"
            runtime = self._build_runtime(db_path)
            now_ms = int(time.time() * 1000)
            runtime.books["token-1"].last_event_ts_ms = now_ms - 10
            runtime.books["token-1"].last_recv_mono_ns = time.monotonic_ns()
            ok, payload = asyncio.run(
                runtime.startup_feed_guard(
                    mode="OBSERVE",
                    tracked_symbols=["BTC"],
                    max_wait_secs=1,
                    min_updates_per_token=1,
                    max_book_age_ms=2000,
                    max_pstar_age_ms=3000,
                )
            )
            self.assertFalse(ok)
            self.assertEqual(str(payload.get("readiness_state")), "PARTIAL")
            self.assertFalse(_handle_startup_guard_result(runtime.db, "OBSERVE", ok, payload))
            runtime.trade_tape.close()
            runtime.decision_tape.close()
            runtime.db.close()


if __name__ == "__main__":
    unittest.main()
