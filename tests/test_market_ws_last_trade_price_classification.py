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


class TestMarketWSLastTradePriceClassification(unittest.IsolatedAsyncioTestCase):
    async def test_last_trade_price_is_known_non_book_and_not_health_counted(self) -> None:
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
                "timestamp": 2_000,
            },
            recv_mono_ns=2_100,
            recv_wall_ms=2_100,
            recv_wall_iso="2026-02-11T00:00:00.000Z",
        )

        self.assertEqual(metrics.market_unknown_count(), 0)
        self.assertEqual(metrics.market_active_rate_per_min(2_100), 0.0)
        self.assertEqual(len(metrics._ws_lag_samples), 0)
        self.assertEqual(decision_engine.updates, 0)
        self.assertIsNone(books["active_token"].best_bid())
        self.assertIsNone(books["active_token"].best_ask())
        self.assertEqual(tape.records[-1]["event_type"], "last_trade_price")
        self.assertEqual(tape.records[-1]["raw"]["_sub_state"], "active")

    async def test_unknown_asset_last_trade_price_does_not_inflate_unknown_rate(self) -> None:
        metrics = Metrics()
        tape = _DummyTape()
        books = {"active_token": OrderBook(asset_id="active_token", bids={}, asks={})}
        client = MarketWSClient(
            asset_ids=["active_token"],
            books=books,
            tape=tape,
            metrics=metrics,
            config=WSConfig(reconnect_base_ms=50, reconnect_max_ms=250),
            decision_engine=None,
        )

        await client._handle_dict_message(  # type: ignore[attr-defined]
            msg={
                "event_type": "last_trade_price",
                "asset_id": "mystery_token",
                "price": "0.5",
                "size": "1.0",
                "side": "SELL",
                "timestamp": 3_000,
            },
            recv_mono_ns=3_050,
            recv_wall_ms=3_050,
            recv_wall_iso="2026-02-11T00:00:00.001Z",
        )

        self.assertEqual(metrics.market_unknown_count(), 0)
        self.assertEqual(metrics.market_unknown_rate_per_min(3_050), 0.0)
        self.assertEqual(tape.records[-1]["raw"]["_sub_state"], "unknown")
        self.assertIn("unknown_subscription_asset", tape.records[-1]["parse_warnings"])


if __name__ == "__main__":
    unittest.main()
