import asyncio
import json
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


def _build_runtime(mode: str, db_path: Path) -> RuntimeEngine:
    now_ms = int(time.time() * 1000)
    books = {
        "token-1": OrderBook(asset_id="token-1", bids={0.49: 10.0}, asks={0.51: 10.0}),
    }
    books["token-1"].last_event_ts_ms = now_ms - 5
    books["token-1"].last_recv_mono_ns = time.monotonic_ns()
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
        mode=mode,
        db=SQLiteStore(db_path),
        decision_tape=DecisionTape(log_dir=str(db_path.parent), run_id=f"run-{mode.lower()}"),
        trade_tape=TradeTape(log_dir=str(db_path.parent), run_id=f"run-{mode.lower()}"),
        books=books,
        constraints=constraints,
        market_meta={"token-1": {"reference_symbol": "BTC", "slug": "m1"}},
        pstar_builder=PStarBuilder(max_age_ms=3000, freeze_disagree_bps=50.0),
        policy_thresholds=PolicyThresholds(max_book_age_ms=2000),
        constitution={"execution": {"maker_quote_size": 1.0}, "policy": {}, "trading": {}},
        time_mapper=TimeMapper.from_wall_and_mono(wall_ms=now_ms, mono_ns=time.monotonic_ns()),
        broker=None,
        run_epoch_ms=now_ms,
        run_id=f"run-{mode.lower()}",
    )


class TestModeAwareFreezeV1(unittest.TestCase):
    def test_observe_downgrades_pstar_invalid_to_degraded(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "runtime.db"
            runtime = _build_runtime("OBSERVE", db_path)
            now_ms = int(time.time() * 1000)
            asyncio.run(runtime.run_quote_cycle(now_ms))
            row = runtime.db.query(
                "SELECT is_frozen, reasons, payload_json FROM system_state ORDER BY as_of_ts DESC LIMIT 1"
            )[0]
            self.assertEqual(int(row[0]), 0)
            self.assertIn("A_PSTAR_INVALID", str(row[1]))
            payload = json.loads(str(row[2]))
            self.assertEqual(str(payload.get("alert_state")), "DEGRADED")
            runtime.trade_tape.close()
            runtime.decision_tape.close()
            runtime.db.close()

    def test_paper_keeps_fail_closed_on_pstar_invalid(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "runtime.db"
            runtime = _build_runtime("PAPER", db_path)
            now_ms = int(time.time() * 1000)
            asyncio.run(runtime.run_quote_cycle(now_ms))
            row = runtime.db.query(
                "SELECT is_frozen, reasons, payload_json FROM system_state ORDER BY as_of_ts DESC LIMIT 1"
            )[0]
            self.assertEqual(int(row[0]), 1)
            self.assertIn("A_PSTAR_INVALID", str(row[1]))
            payload = json.loads(str(row[2]))
            self.assertEqual(str(payload.get("alert_state")), "FROZEN")
            runtime.trade_tape.close()
            runtime.decision_tape.close()
            runtime.db.close()


if __name__ == "__main__":
    unittest.main()
