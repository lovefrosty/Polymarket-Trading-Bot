import asyncio
import tempfile
import time
import unittest
from pathlib import Path

from core.broker_base import BrokerEvent, OrderIntent
from core.decision_tape import DecisionTape, TimeMapper
from core.order_book import OrderBook
from core.policy_gate import PolicyThresholds, PolicyVerdict
from core.pstar import PStarBuilder
from core.sqlite_store import SQLiteStore
from core.trade_tape import TradeTape
from core.validators import OrderConstraints
from scripts.run_system import RuntimeEngine


class _BrokerSpy:
    def __init__(self) -> None:
        self.submit_calls = 0

    def submit(self, intent: OrderIntent):
        self.submit_calls += 1
        return [BrokerEvent("order_submit", intent.order_id, {"t_event_wall_ms": int(time.time() * 1000), "status": "submitted"})]

    def cancel(self, order_id: str):
        return []

    def replace(self, order_id: str, new_intent: OrderIntent):
        return []


class TestRunSystemPaperGuardBlocking(unittest.TestCase):
    def test_quote_actions_blocked_when_rollover_guard_active(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "runtime.db"
            broker = _BrokerSpy()
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
                broker=broker,
                run_epoch_ms=now_ms,
                run_id="run-test",
            )
            runtime.activate_rollover_guard(token_ids=["token-1"], quiet_until_ms=now_ms + 30_000, require_readiness=True)
            verdict = PolicyVerdict(allow=True, action="QUOTE", reason_codes=[], diagnostics={})

            asyncio.run(
                runtime._apply_side(
                    token_id="token-1",
                    side="buy",
                    price=0.5,
                    qty=1.0,
                    constraint=runtime.constraints["token-1"],
                    verdict=verdict,
                    now_ms=now_ms,
                    decision_id="d-1",
                )
            )

            self.assertEqual(broker.submit_calls, 0)
            runtime.trade_tape.close()
            runtime.decision_tape.close()
            runtime.db.close()


if __name__ == "__main__":
    unittest.main()
