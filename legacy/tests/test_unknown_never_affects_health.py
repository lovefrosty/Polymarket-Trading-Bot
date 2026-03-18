import unittest

from core.metrics import Metrics
from core.order_book import OrderBook
from data.polymarket_ws import MarketWSClient, WSConfig


class _DummyTape:
    def __init__(self) -> None:
        self.records = []

    def write(self, **kwargs) -> None:  # type: ignore[no-untyped-def]
        self.records.append(kwargs)


class _DecisionEngine:
    def __init__(self) -> None:
        self.updates = 0

    def on_book_update(self, asset_id: str, recv_mono_ns: int) -> None:
        _ = (asset_id, recv_mono_ns)
        self.updates += 1


class TestUnknownNeverAffectsHealth(unittest.IsolatedAsyncioTestCase):
    async def test_unknown_messages_increment_counter_but_not_health_or_decisions(self) -> None:
        metrics = Metrics()
        tape = _DummyTape()
        decision_engine = _DecisionEngine()
        books = {"active_token": OrderBook(asset_id="active_token", bids={}, asks={})}
        client = MarketWSClient(
            asset_ids=["active_token"],
            books=books,
            tape=tape,
            metrics=metrics,
            config=WSConfig(reconnect_base_ms=50, reconnect_max_ms=250),
            decision_engine=decision_engine,
        )

        await client._handle_dict_message(  # type: ignore[attr-defined]
            msg={
                "asset_id": "mystery_token",
                "bids": [{"price": 0.4, "size": 2.0}],
                "asks": [{"price": 0.6, "size": 2.0}],
                "timestamp": 1_000,
            },
            recv_mono_ns=2_000,
            recv_wall_ms=2_000,
            recv_wall_iso="2026-02-11T00:00:00.000Z",
        )

        self.assertEqual(metrics.market_unknown_count(), 1)
        breakdown = metrics.market_unknown_breakdown_per_min(2_000)
        self.assertGreaterEqual(float(breakdown.get("unknown_channel", 0.0)), 1.0)
        sig_top = metrics.market_unknown_signature_top(2_000, limit=3)
        self.assertTrue(any("unknown_subscription_asset" in key for key in sig_top.keys()))
        self.assertEqual(len(metrics._ws_lag_samples), 0)
        self.assertEqual(decision_engine.updates, 0)
        self.assertEqual(tape.records[-1]["raw"]["_sub_state"], "unknown")

        await client._handle_dict_message(  # type: ignore[attr-defined]
            msg={
                "asset_id": "active_token",
                "bids": [{"price": 0.49, "size": 5.0}],
                "asks": [{"price": 0.51, "size": 5.0}],
                "timestamp": 2_100,
            },
            recv_mono_ns=2_500,
            recv_wall_ms=2_500,
            recv_wall_iso="2026-02-11T00:00:00.001Z",
        )

        self.assertEqual(metrics.market_unknown_count(), 1)
        self.assertEqual(len(metrics._ws_lag_samples), 1)
        self.assertEqual(decision_engine.updates, 1)

    async def test_known_non_book_messages_are_observability_only(self) -> None:
        metrics = Metrics()
        tape = _DummyTape()
        decision_engine = _DecisionEngine()
        books = {"active_token": OrderBook(asset_id="active_token", bids={}, asks={})}
        client = MarketWSClient(
            asset_ids=["active_token"],
            books=books,
            tape=tape,
            metrics=metrics,
            config=WSConfig(reconnect_base_ms=50, reconnect_max_ms=250),
            decision_engine=decision_engine,
        )

        await client._handle_dict_message(  # type: ignore[attr-defined]
            msg={
                "event_type": "last_trade_price",
                "asset_id": "active_token",
                "price": "0.99",
                "size": "1.0",
                "side": "BUY",
                "timestamp": 5_000,
            },
            recv_mono_ns=5_100,
            recv_wall_ms=5_100,
            recv_wall_iso="2026-02-11T00:00:00.010Z",
        )

        self.assertEqual(metrics.market_unknown_count(), 0)
        self.assertEqual(metrics.market_active_rate_per_min(5_100), 0.0)
        self.assertEqual(len(metrics._ws_lag_samples), 0)
        self.assertEqual(decision_engine.updates, 0)
        self.assertEqual(tape.records[-1]["raw"]["_sub_state"], "active")


if __name__ == "__main__":
    unittest.main()
