import unittest

from core.metrics import Metrics
from core.order_book import OrderBook
from data.polymarket_ws import MarketWSClient, UserWSClient, WSConfig


class _DummyTape:
    def write(self, **kwargs) -> None:  # type: ignore[no-untyped-def]
        _ = kwargs


class _DummyWS:
    def __init__(self) -> None:
        self.payloads = []

    async def send(self, payload: str) -> None:
        self.payloads.append(payload)


class TestMarketWSSubscribeAttemptIds(unittest.IsolatedAsyncioTestCase):
    async def test_attempt_ids_are_monotonic_and_payload_assets_sorted(self) -> None:
        books = {
            "old_yes": OrderBook(asset_id="old_yes", bids={}, asks={}),
            "old_no": OrderBook(asset_id="old_no", bids={}, asks={}),
            "a": OrderBook(asset_id="a", bids={}, asks={}),
            "b": OrderBook(asset_id="b", bids={}, asks={}),
        }
        client = MarketWSClient(
            asset_ids=["old_yes", "old_no"],
            books=books,
            tape=_DummyTape(),
            metrics=Metrics(),
            config=WSConfig(reconnect_base_ms=25, reconnect_max_ms=100),
            decision_engine=None,
        )
        client._ws = _DummyWS()  # type: ignore[attr-defined]

        first = await client.resubscribe(["b", "a", "b"], first_book_timeout_secs=0.05)
        second = await client.resubscribe(["a", "b"], first_book_timeout_secs=0.05)

        self.assertEqual(first.status, "abort_timeout_waiting_confirmation")
        self.assertEqual(second.status, "abort_timeout_waiting_confirmation")
        self.assertIsNotNone(first.attempt_id)
        self.assertIsNotNone(second.attempt_id)
        self.assertEqual(int(second.attempt_id), int(first.attempt_id) + 1)

        rows = client.drain_completed_subscribe_attempts()
        self.assertGreaterEqual(len(rows), 2)
        rows = sorted(rows, key=lambda item: int(item.get("attempt_id", 0)))
        self.assertEqual(rows[0]["asset_ids_json"], "[\"a\",\"b\"]")
        self.assertEqual(rows[1]["asset_ids_json"], "[\"a\",\"b\"]")
        self.assertTrue(str(rows[0]["payload_json"]).startswith("{\"assets_ids\":[\"a\",\"b\"],\"type\":\"market\""))
        self.assertLessEqual(int(rows[0]["ts_ms"]), int(rows[1]["ts_ms"]))
        self.assertEqual(MarketWSClient._jitter(100), 150)
        self.assertEqual(UserWSClient._jitter(100), 150)


if __name__ == "__main__":
    unittest.main()
