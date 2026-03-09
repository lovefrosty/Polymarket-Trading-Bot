import asyncio
import unittest

from core.metrics import Metrics
from core.order_book import OrderBook
from data.polymarket_ws import MarketWSClient, WSConfig


class _DummyTape:
    def write(self, **kwargs) -> None:  # type: ignore[no-untyped-def]
        _ = kwargs


class _DummyWS:
    async def send(self, payload: str) -> None:
        _ = payload


class TestMarketWSSubscribeAckError(unittest.IsolatedAsyncioTestCase):
    async def test_ack_error_is_captured_and_persisted(self) -> None:
        books = {
            "old_yes": OrderBook(asset_id="old_yes", bids={}, asks={}),
            "old_no": OrderBook(asset_id="old_no", bids={}, asks={}),
            "new_yes": OrderBook(asset_id="new_yes", bids={}, asks={}),
            "new_no": OrderBook(asset_id="new_no", bids={}, asks={}),
        }
        client = MarketWSClient(
            asset_ids=["old_yes", "old_no"],
            books=books,
            tape=_DummyTape(),
            metrics=Metrics(),
            config=WSConfig(reconnect_base_ms=25, reconnect_max_ms=100, confirm_min_updates_per_token=2),
            decision_engine=None,
        )
        client._ws = _DummyWS()  # type: ignore[attr-defined]

        task = asyncio.create_task(client.resubscribe(["new_yes", "new_no"], first_book_timeout_secs=0.05))
        await asyncio.sleep(0.01)

        await client._handle_dict_message(  # type: ignore[attr-defined]
            msg={"event_type": "error", "status": "rejected", "error": "auth_reject", "assets_ids": ["new_yes", "new_no"]},
            recv_mono_ns=30_000,
            recv_wall_ms=30_000,
            recv_wall_iso="2026-02-11T00:00:00.000Z",
        )

        result = await task
        self.assertEqual(result.status, "abort_timeout_waiting_confirmation")
        self.assertEqual(result.confirm_diag["ack_status"], "ERROR")
        self.assertIn("auth_reject", str(result.confirm_diag.get("last_ws_error") or ""))

        rows = client.drain_completed_subscribe_attempts()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["ack_status"], "ERROR")
        self.assertEqual(rows[0]["result"], "TIMEOUT")


if __name__ == "__main__":
    unittest.main()
