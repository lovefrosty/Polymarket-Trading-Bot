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


class TestPaperDecisionTraceEnvelope(unittest.TestCase):
    def test_decision_trace_written_per_cycle(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "runtime.db"
            now_ms = int(time.time() * 1000)
            runtime = RuntimeEngine(
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
                        max_spread_bps=150.0,
                        max_slippage_bps=120.0,
                        max_book_staleness_ms=2_000,
                    )
                },
                market_meta={"token-1": {"reference_symbol": "BTC", "slug": "btc-updown-15m-1"}},
                pstar_builder=PStarBuilder(max_age_ms=3_000, freeze_disagree_bps=50.0),
                policy_thresholds=PolicyThresholds(max_book_age_ms=2_000),
                constitution={"execution": {"maker_quote_size": 1.0}, "policy": {}, "trading": {"ws_starvation_max_ms": 100_000}},
                time_mapper=TimeMapper.from_wall_and_mono(wall_ms=now_ms, mono_ns=time.monotonic_ns()),
                broker=None,
                run_epoch_ms=now_ms,
                run_id="run-test",
            )
            runtime.books["token-1"].apply_snapshot(
                bids=[(0.49, 10.0)],
                asks=[(0.51, 10.0)],
                event_ts_ms=now_ms - 20,
                recv_mono_ns=time.monotonic_ns(),
                last_hash=None,
            )
            runtime.on_reference("spot", "BTC", 0.5, ts_event_ms=now_ms - 30, ts_recv_ms=now_ms - 10)

            asyncio.run(runtime.run_quote_cycle(now_ms))
            rows = runtime.db.query(
                """
                SELECT decision_id, token_id, market_slug, action, allow_action, input_asof_ts_ms, gate_reason_codes
                FROM decision_trace
                ORDER BY ts_ms DESC
                LIMIT 1
                """
            )
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0][1], "token-1")
            self.assertEqual(rows[0][2], "btc-updown-15m-1")
            self.assertIn(rows[0][3], {"QUOTE", "SKIP", "FREEZE"})
            runtime.trade_tape.close()
            runtime.decision_tape.close()
            runtime.db.close()


if __name__ == "__main__":
    unittest.main()
