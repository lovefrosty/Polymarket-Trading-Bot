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


class TestExecutionQualityStatsV1(unittest.TestCase):
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
            constitution={"execution": {"maker_quote_size": 1.0}, "policy": {}, "trading": {}},
            time_mapper=TimeMapper.from_wall_and_mono(wall_ms=now_ms, mono_ns=time.monotonic_ns()),
            broker=None,
            run_epoch_ms=now_ms,
            run_id="run-test",
        )

    def test_hourly_rollup_is_deterministic(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "runtime.db"
            runtime = self._build_runtime(db_path)
            now_ms = int(time.time() * 1000)
            for idx, net_edge in enumerate([10.0, 20.0, 30.0]):
                runtime.db.insert(
                    "execution_quality",
                    {
                        "ts_ms": now_ms - 10_000 + idx,
                        "event_id": f"eq-{idx}",
                        "run_id": "run-test",
                        "mode": "OBSERVE",
                        "token_id": "token-1",
                        "order_id": f"o-{idx}",
                        "side": "buy",
                        "fill_ts_ms": now_ms - 10_000 + idx,
                        "fill_price": 0.5,
                        "fill_qty": 1.0,
                        "fee_bps": 1.0,
                        "mid_at_send": 0.501,
                        "mid_at_ack": 0.502,
                        "mid_at_fill": 0.503,
                        "mid_1s": 0.504,
                        "mid_5s": 0.505,
                        "mid_30s": 0.506,
                        "realized_spread_bps": net_edge + 1.0,
                        "markout_1s_bps": net_edge + 2.0,
                        "markout_5s_bps": net_edge + 3.0,
                        "markout_30s_bps": net_edge + 4.0,
                        "net_edge_bps": net_edge,
                        "payload_json": "{}",
                    },
                )
            runtime._record_execution_quality_stats(now_ms)
            rows = runtime.db.query(
                """
                SELECT token_id, sample_count, p50_net_edge_bps, p95_net_edge_bps
                FROM execution_quality_stats
                ORDER BY ts_ms DESC, token_id
                """
            )
            by_token = {str(row[0]): row for row in rows}
            self.assertIn("token-1", by_token)
            self.assertIn("__all__", by_token)
            token_row = by_token["token-1"]
            self.assertEqual(int(token_row[1]), 3)
            self.assertEqual(float(token_row[2]), 20.0)
            self.assertEqual(float(token_row[3]), 30.0)

            runtime.trade_tape.close()
            runtime.decision_tape.close()
            runtime.db.close()


if __name__ == "__main__":
    unittest.main()
