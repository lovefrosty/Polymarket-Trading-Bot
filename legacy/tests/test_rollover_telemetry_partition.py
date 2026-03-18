import unittest

from core.metrics import Metrics
from core.order_book import OrderBook
from data.polymarket_ws import MarketWSClient, WSConfig


class _DummyTape:
    def __init__(self) -> None:
        self.records = []

    def write(self, **kwargs) -> None:  # type: ignore[no-untyped-def]
        self.records.append(kwargs)


def _book(asset_id: str) -> OrderBook:
    return OrderBook(asset_id=asset_id, bids={}, asks={})


class TestRolloverTelemetryPartition(unittest.IsolatedAsyncioTestCase):
    async def test_ignored_old_traffic_does_not_update_active_health(self) -> None:
        metrics = Metrics()
        tape = _DummyTape()
        books = {
            "new_yes": _book("new_yes"),
            "old_yes": _book("old_yes"),
        }
        client = MarketWSClient(
            asset_ids=["new_yes"],
            books=books,
            tape=tape,
            metrics=metrics,
            config=WSConfig(reconnect_base_ms=50, reconnect_max_ms=250),
            decision_engine=None,
        )
        client._active_asset_ids = ["new_yes"]  # type: ignore[attr-defined]
        client._active_asset_set = {"new_yes"}  # type: ignore[attr-defined]
        client._ignored_asset_set = {"old_yes"}  # type: ignore[attr-defined]
        client._active_subscription_id = 7  # type: ignore[attr-defined]

        await client._handle_dict_message(  # type: ignore[attr-defined]
            msg={
                "asset_id": "old_yes",
                "bids": [{"price": 0.40, "size": 10.0}],
                "asks": [{"price": 0.60, "size": 10.0}],
                "timestamp": 1_000,
            },
            recv_mono_ns=2_000,
            recv_wall_ms=2_000,
            recv_wall_iso="2026-02-11T00:00:00.000Z",
        )
        self.assertIsNone(client.active_last_book_recv_wall_ms())
        self.assertEqual(len(metrics._ws_lag_samples), 0)
        old_record = tape.records[-1]
        self.assertEqual(old_record["raw"]["_sub_state"], "ignored_old")
        self.assertEqual(old_record["source"], "market_ws:sub:ignored_old")

        await client._handle_dict_message(  # type: ignore[attr-defined]
            msg={
                "asset_id": "new_yes",
                "bids": [{"price": 0.49, "size": 10.0}],
                "asks": [{"price": 0.51, "size": 10.0}],
                "timestamp": 2_200,
            },
            recv_mono_ns=2_500,
            recv_wall_ms=2_500,
            recv_wall_iso="2026-02-11T00:00:00.001Z",
        )
        self.assertEqual(client.active_last_book_recv_wall_ms(), 2_500)
        self.assertEqual(len(metrics._ws_lag_samples), 1)
        active_record = tape.records[-1]
        self.assertEqual(active_record["raw"]["_sub_state"], "active")


if __name__ == "__main__":
    unittest.main()
