import asyncio
import tempfile
import unittest
from pathlib import Path

from core.broker_base import BrokerEvent, BrokerSnapshot
from core.decision_tape import TimeMapper
from core.order_book import OrderBook
from core.policy_gate import PolicyThresholds, PolicyVerdict
from core.pstar import PStarBuilder
from core.sqlite_store import SQLiteStore
from core.validators import OrderConstraints
from scripts.run_system import MarketReadinessConfig, RuntimeEngine


class _DummyDecisionTape:
    run_id = "test"

    def write(self, record) -> None:  # pragma: no cover
        _ = record


class _DummyTradeTape:
    run_id = "test"

    def __init__(self) -> None:
        self._event_id = 0

    def next_event_id(self) -> int:
        self._event_id += 1
        return self._event_id

    def write(self, payload) -> None:  # pragma: no cover
        _ = payload


class _Broker:
    def __init__(self) -> None:
        self.submit_calls = 0
        self.cancel_calls = 0
        self.replace_calls = 0

    def submit(self, intent):
        self.submit_calls += 1
        return [
            BrokerEvent("order_submit", intent.order_id, {"t_event_wall_ms": intent.as_of_ts_ms, "status": "submitted", "asset_id": intent.asset_id, "side": intent.side}),
            BrokerEvent("order_ack", intent.order_id, {"t_event_wall_ms": intent.as_of_ts_ms, "status": "accepted", "asset_id": intent.asset_id, "side": intent.side}),
        ]

    def cancel(self, order_id: str):
        self.cancel_calls += 1
        return [BrokerEvent("order_cancel", order_id, {"t_event_wall_ms": 0, "status": "canceled"})]

    def replace(self, order_id: str, intent):
        self.replace_calls += 1
        return [BrokerEvent("order_ack", intent.order_id, {"t_event_wall_ms": intent.as_of_ts_ms, "status": "accepted", "asset_id": intent.asset_id, "side": intent.side})]

    def snapshot(self):
        return BrokerSnapshot(open_orders={}, meta={})


def _constraints(tokens: list[str]) -> dict[str, OrderConstraints]:
    return {
        token: OrderConstraints(
            min_tick=0.01,
            min_size=1.0,
            min_price=0.01,
            max_price=0.99,
            max_spread_bps=1000.0,
            max_slippage_bps=1000.0,
            max_book_staleness_ms=30_000,
        )
        for token in tokens
    }


class TestQuietWindowBlocksQuotes(unittest.IsolatedAsyncioTestCase):
    async def test_quiet_window_suppresses_order_actions(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = SQLiteStore(Path(tmp) / "runtime.db")
            broker = _Broker()
            token = "tok_yes"
            book = OrderBook(asset_id=token, bids={}, asks={})
            book.apply_snapshot(
                bids=[(0.49, 10.0)],
                asks=[(0.51, 10.0)],
                event_ts_ms=10_000,
                recv_mono_ns=1_000_000_000,
                last_hash=None,
            )
            runtime = RuntimeEngine(
                mode="PAPER",
                db=db,
                decision_tape=_DummyDecisionTape(),
                trade_tape=_DummyTradeTape(),
                books={token: book},
                constraints=_constraints([token]),
                market_meta={token: {"reference_symbol": "BTC", "slug": "btc-updown-15m-1700000000"}},
                pstar_builder=PStarBuilder(max_age_ms=120_000, freeze_disagree_bps=1000.0),
                policy_thresholds=PolicyThresholds(
                    max_book_age_ms=20_000,
                    max_spread_bps=1000.0,
                    max_slippage_bps=1000.0,
                    max_signal_age_ms=120_000,
                    max_ws_lag_ms=120_000,
                ),
                constitution={"trading": {}, "policy": {}, "execution": {"maker_quote_size": 1.0}},
                time_mapper=TimeMapper.from_wall_and_mono(wall_ms=10_000, mono_ns=1_000_000_000),
                broker=broker,
                run_epoch_ms=10_000,
                readiness_config=MarketReadinessConfig(
                    book_max_age_ms=20_000,
                    book_max_spread_bps=1000.0,
                    depth_target_qty=1.0,
                    pstar_max_age_ms=120_000,
                ),
            )

            runtime.on_reference("spot", "BTC", 50_000.0, ts_event_ms=10_000, ts_recv_ms=10_000)
            runtime.on_reference("perp", "BTC", 50_000.0, ts_event_ms=10_000, ts_recv_ms=10_000)

            runtime.activate_rollover_guard([token], quiet_until_ms=11_000, require_readiness=True)
            await runtime.run_quote_cycle(10_100)
            self.assertEqual(broker.submit_calls, 0)
            self.assertEqual(broker.replace_calls, 0)
            self.assertEqual(broker.cancel_calls, 0)

            await runtime.run_quote_cycle(12_000)
            self.assertGreaterEqual(broker.submit_calls, 1)
            db.close()


if __name__ == "__main__":
    unittest.main()
