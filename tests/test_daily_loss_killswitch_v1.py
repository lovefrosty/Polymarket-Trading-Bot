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


class TestDailyLossKillswitchV1(unittest.TestCase):
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
                    "max_orders_per_min": 999,
                    "max_cancels_per_min": 999,
                    "max_daily_loss_usdc": 100.0,
                    "max_daily_notional_usdc": 10_000.0,
                },
            },
            time_mapper=TimeMapper.from_wall_and_mono(wall_ms=now_ms, mono_ns=time.monotonic_ns()),
            broker=None,
            run_epoch_ms=now_ms,
            run_id="run-test",
        )

    def test_daily_loss_triggers_killswitch_reason(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime = self._build_runtime(Path(tmp) / "runtime.db")
            now_ms = int(time.time() * 1000)
            runtime._reset_daily_counters_if_needed(now_ms)
            runtime._daily_loss_usdc = 120.0
            reasons = runtime._risk_budget_reasons(now_ms)
            self.assertIn("RISK_DAILY_LOSS_KILLSWITCH", reasons)
            runtime.trade_tape.close()
            runtime.decision_tape.close()
            runtime.db.close()


if __name__ == "__main__":
    unittest.main()
