import asyncio
from datetime import datetime, timezone
import json
import tempfile
import time
import unittest
from pathlib import Path

from core.broker_sim import SimBroker, SimBrokerConfig
from core.decision_tape import DecisionTape, TimeMapper
from core.order_book import OrderBook
from core.policy_gate import PolicyThresholds
from core.pstar import PStarBuilder
from core.sqlite_store import SQLiteStore
from core.trade_tape import TradeTape
from core.validators import OrderConstraints
from scripts.run_system import RuntimeEngine


def _build_runtime(db_path: Path) -> RuntimeEngine:
    now_ms = int(time.time() * 1000)
    mono_ns = time.monotonic_ns()
    books = {"token-1": OrderBook(asset_id="token-1", bids={}, asks={})}
    books["token-1"].apply_snapshot(
        bids=[(0.49, 10.0)],
        asks=[(0.51, 10.0)],
        event_ts_ms=now_ms - 5,
        recv_mono_ns=mono_ns,
    )
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
    time_mapper = TimeMapper.from_wall_and_mono(wall_ms=now_ms, mono_ns=mono_ns)
    broker = SimBroker(
        books=books,
        constraints=constraints,
        time_mapper=time_mapper,
        config=SimBrokerConfig(latency_ms=7, fee_mode="TAKE"),
    )
    return RuntimeEngine(
        mode="PAPER",
        db=SQLiteStore(db_path),
        decision_tape=DecisionTape(log_dir=str(db_path.parent), run_id="run-paper-risk"),
        trade_tape=TradeTape(log_dir=str(db_path.parent), run_id="run-paper-risk"),
        books=books,
        constraints=constraints,
        market_meta={"token-1": {"reference_symbol": "BTC", "slug": "btc-updown-15m-1"}},
        pstar_builder=PStarBuilder(max_age_ms=3000, freeze_disagree_bps=50.0),
        policy_thresholds=PolicyThresholds(max_book_age_ms=2000),
        constitution={
            "execution": {
                "maker_quote_size": 1.5,
                "maker_half_spread_bps": 40.0,
                "inventory_skew_per_unit": 0.01,
                "risk_padding_bps": 5.0,
            },
            "policy": {},
            "trading": {"ws_starvation_max_ms": 100_000},
        },
        time_mapper=time_mapper,
        broker=broker,
        run_epoch_ms=now_ms,
        run_id="run-paper-risk",
    )


class TestRunSystemPaperRiskSnapshot(unittest.TestCase):
    def test_system_state_writes_paper_profile_and_utilization(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "runtime.db"
            runtime = _build_runtime(db_path)
            now_ms = int(time.time() * 1000)

            runtime._max_orders_per_min = 1
            runtime._max_cancels_per_min = 1
            runtime._max_daily_notional_usdc = 10.0
            runtime._max_daily_loss_usdc = 1.0
            runtime._daily_bucket_utc = datetime.fromtimestamp(now_ms / 1000.0, tz=timezone.utc).strftime("%Y-%m-%d")
            runtime._submit_event_ts.extend([now_ms - 100, now_ms - 200])
            runtime._cancel_event_ts.extend([now_ms - 300])
            runtime._daily_notional_usdc = 15.0
            runtime._daily_loss_usdc = 2.0
            runtime.inventory_yes["token-1"] = 3.0
            runtime.inventory_no["token-1"] = 1.0
            runtime._last_q_by_token["token-1"] = 0.60

            asyncio.run(runtime.run_quote_cycle(now_ms))

            row = runtime.db.query(
                "SELECT payload_json FROM system_state ORDER BY as_of_ts DESC LIMIT 1"
            )[0]
            payload = json.loads(str(row[0]))
            profile = payload.get("paper_trading_profile") or {}
            utilization = payload.get("paper_trading_utilization") or {}

            self.assertEqual(profile.get("fill_model"), "book_vwap")
            self.assertEqual(int(profile.get("sim_latency_ms")), 7)
            self.assertEqual(str(profile.get("sim_fee_mode")), "TAKE")
            self.assertEqual(float(profile.get("maker_quote_size")), 1.5)

            self.assertEqual(int(utilization.get("orders_per_min")), 2)
            self.assertEqual(int(utilization.get("cancels_per_min")), 1)
            self.assertAlmostEqual(float(utilization.get("daily_notional_usdc")), 15.0)
            self.assertAlmostEqual(float(utilization.get("daily_loss_usdc")), 2.0)
            self.assertGreaterEqual(float(utilization.get("orders_per_min_ratio")), 2.0)
            self.assertGreaterEqual(float(utilization.get("daily_notional_ratio")), 1.5)
            self.assertIn("RISK_ORDER_RATE_LIMIT", utilization.get("active_risk_reasons", []))
            self.assertIn("RISK_CANCEL_RATE_LIMIT", utilization.get("active_risk_reasons", []))
            self.assertIn("RISK_DAILY_NOTIONAL_LIMIT", utilization.get("active_risk_reasons", []))
            self.assertIn("RISK_DAILY_LOSS_KILLSWITCH", utilization.get("active_risk_reasons", []))
            token_cap = utilization.get("cap_state_by_token", {}).get("token-1", {})
            self.assertAlmostEqual(float(token_cap.get("yes_qty")), 3.0)
            self.assertAlmostEqual(float(token_cap.get("no_qty")), 1.0)
            self.assertIn("gross_limit", utilization.get("portfolio_cap_state", {}))

            runtime.trade_tape.close()
            runtime.decision_tape.close()
            runtime.db.close()


if __name__ == "__main__":
    unittest.main()
