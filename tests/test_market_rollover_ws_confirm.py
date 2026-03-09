import asyncio
import unittest

from core.metrics import Metrics
from core.order_book import OrderBook
from data.polymarket_ws import MarketWSClient, WSConfig
from scripts.run_system import _confirm_diag_summary


class _DummyTape:
    def __init__(self) -> None:
        self.records = []

    def write(self, **kwargs) -> None:  # type: ignore[no-untyped-def]
        self.records.append(kwargs)


class _DummyWS:
    def __init__(self) -> None:
        self.payloads = []

    async def send(self, payload: str) -> None:
        self.payloads.append(payload)


def _book(asset_id: str) -> OrderBook:
    return OrderBook(asset_id=asset_id, bids={}, asks={})


class TestMarketRolloverWSConfirm(unittest.IsolatedAsyncioTestCase):
    def _build_client(self) -> MarketWSClient:
        books = {
            "old_yes": _book("old_yes"),
            "old_no": _book("old_no"),
            "new_yes": _book("new_yes"),
            "new_no": _book("new_no"),
        }
        client = MarketWSClient(
            asset_ids=["old_yes", "old_no"],
            books=books,
            tape=_DummyTape(),
            metrics=Metrics(),
            config=WSConfig(
                reconnect_base_ms=50,
                reconnect_max_ms=250,
                confirm_min_updates_per_token=2,
                confirm_book_freshness_ms=5_000,
            ),
            decision_engine=None,
        )
        client._ws = _DummyWS()  # type: ignore[attr-defined]
        return client

    async def _emit_snapshot(
        self,
        client: MarketWSClient,
        *,
        asset_id: str,
        seq: int,
        recv_wall_ms: int,
        t_event_ms: int,
        include_levels: bool = True,
    ) -> None:
        bids = [{"price": 0.49, "size": 10.0}] if include_levels else []
        asks = [{"price": 0.51, "size": 10.0}] if include_levels else []
        msg = {
            "asset_id": asset_id,
            "bids": bids,
            "asks": asks,
            "timestamp": t_event_ms,
            "sequence": seq,
        }
        await client._handle_dict_message(  # type: ignore[attr-defined]
            msg=msg,
            recv_mono_ns=recv_wall_ms * 1_000_000,
            recv_wall_ms=recv_wall_ms,
            recv_wall_iso="2026-02-11T00:00:00.000Z",
        )

    async def test_requires_two_valid_updates_per_token(self) -> None:
        client = self._build_client()
        task = asyncio.create_task(client.resubscribe(["new_yes", "new_no"], first_book_timeout_secs=1.0))
        await asyncio.sleep(0.01)

        await self._emit_snapshot(client, asset_id="new_yes", seq=1, recv_wall_ms=10_000, t_event_ms=9_900)
        await self._emit_snapshot(client, asset_id="new_no", seq=2, recv_wall_ms=10_050, t_event_ms=9_950)
        self.assertFalse(task.done())

        await self._emit_snapshot(client, asset_id="new_yes", seq=3, recv_wall_ms=10_100, t_event_ms=10_000)
        self.assertFalse(task.done())

        await self._emit_snapshot(client, asset_id="new_no", seq=4, recv_wall_ms=10_150, t_event_ms=1)
        self.assertFalse(task.done())
        await self._emit_snapshot(client, asset_id="new_no", seq=5, recv_wall_ms=10_200, t_event_ms=10_100)
        self.assertFalse(task.done())
        await self._emit_snapshot(client, asset_id="new_no", seq=6, recv_wall_ms=10_250, t_event_ms=10_150)

        result = await task
        self.assertEqual(result.status, "committed")
        self.assertGreaterEqual(int(result.confirm_diag["counts_by_asset"]["new_yes"]), 2)
        self.assertGreaterEqual(int(result.confirm_diag["counts_by_asset"]["new_no"]), 2)
        self.assertIn("STALE_EVENT_TS", result.confirm_diag["reasons"])

    async def test_empty_snapshot_then_real_snapshot(self) -> None:
        client = self._build_client()
        task = asyncio.create_task(client.resubscribe(["new_yes", "new_no"], first_book_timeout_secs=1.0))
        await asyncio.sleep(0.01)

        await self._emit_snapshot(
            client, asset_id="new_yes", seq=1, recv_wall_ms=20_000, t_event_ms=19_950, include_levels=False
        )
        await self._emit_snapshot(
            client, asset_id="new_no", seq=2, recv_wall_ms=20_050, t_event_ms=20_000, include_levels=False
        )
        self.assertFalse(task.done())

        await self._emit_snapshot(client, asset_id="new_yes", seq=3, recv_wall_ms=20_100, t_event_ms=20_050)
        await self._emit_snapshot(client, asset_id="new_no", seq=4, recv_wall_ms=20_150, t_event_ms=20_100)
        await self._emit_snapshot(client, asset_id="new_yes", seq=5, recv_wall_ms=20_200, t_event_ms=20_150)
        await self._emit_snapshot(client, asset_id="new_no", seq=6, recv_wall_ms=20_250, t_event_ms=20_200)

        result = await task
        self.assertEqual(result.status, "committed")
        self.assertIn("MISSING_TOP_OF_BOOK", result.confirm_diag["reasons"])

    async def test_out_of_order_and_subset_updates_do_not_commit_early(self) -> None:
        client = self._build_client()
        task = asyncio.create_task(client.resubscribe(["new_yes", "new_no"], first_book_timeout_secs=1.0))
        await asyncio.sleep(0.01)

        await self._emit_snapshot(client, asset_id="new_yes", seq=10, recv_wall_ms=30_000, t_event_ms=29_950)
        await self._emit_snapshot(client, asset_id="new_yes", seq=9, recv_wall_ms=30_050, t_event_ms=30_000)
        await self._emit_snapshot(client, asset_id="new_yes", seq=11, recv_wall_ms=30_100, t_event_ms=30_050)
        self.assertFalse(task.done())

        await self._emit_snapshot(client, asset_id="new_no", seq=12, recv_wall_ms=30_150, t_event_ms=30_100)
        self.assertFalse(task.done())
        await self._emit_snapshot(client, asset_id="new_no", seq=13, recv_wall_ms=30_200, t_event_ms=30_150)
        await self._emit_snapshot(client, asset_id="new_yes", seq=14, recv_wall_ms=30_250, t_event_ms=30_200)

        result = await task
        self.assertEqual(result.status, "committed")
        self.assertIn("SEQUENCE_OUT_OF_ORDER", result.confirm_diag["reasons"])

    async def test_old_active_messages_do_not_satisfy_pending_confirmation(self) -> None:
        client = self._build_client()
        task = asyncio.create_task(client.resubscribe(["new_yes", "new_no"], first_book_timeout_secs=1.0))
        await asyncio.sleep(0.01)

        await self._emit_snapshot(client, asset_id="old_yes", seq=1, recv_wall_ms=40_000, t_event_ms=39_950)
        await self._emit_snapshot(client, asset_id="old_no", seq=2, recv_wall_ms=40_050, t_event_ms=40_000)
        self.assertFalse(task.done())

        await self._emit_snapshot(client, asset_id="new_yes", seq=3, recv_wall_ms=40_100, t_event_ms=40_050)
        await self._emit_snapshot(client, asset_id="new_no", seq=4, recv_wall_ms=40_150, t_event_ms=40_100)
        await self._emit_snapshot(client, asset_id="new_yes", seq=5, recv_wall_ms=40_200, t_event_ms=40_150)
        await self._emit_snapshot(client, asset_id="new_no", seq=6, recv_wall_ms=40_250, t_event_ms=40_200)

        result = await task
        self.assertEqual(result.status, "committed")
        self.assertEqual(set(result.confirm_diag["counts_by_asset"].keys()), {"new_yes", "new_no"})

    async def test_timeout_summary_reports_missing_assets_deterministically(self) -> None:
        client = self._build_client()
        result = await client.resubscribe(["new_yes", "new_no"], first_book_timeout_secs=0.05)
        self.assertEqual(result.status, "abort_timeout_waiting_confirmation")
        self.assertIsNotNone(result.confirm_diag.get("attempt_id"))
        self.assertIn("preclass_pending_hits_by_asset", result.confirm_diag)
        self.assertIn("preclass_msgs_by_sub_id", result.confirm_diag)
        self.assertIn("parse_drop_counts", result.confirm_diag)
        self.assertIn("subscribe_payload_echo", result.confirm_diag)
        summary = _confirm_diag_summary(result.confirm_diag, result.confirm_wait_ms)
        self.assertEqual(summary["required_updates_per_token"], 2)
        self.assertEqual(summary["counts_by_asset"], {"new_no": 0, "new_yes": 0})
        self.assertEqual(summary["missing_assets"], ["new_no", "new_yes"])
        self.assertEqual(summary["rejects_by_asset"], {"new_no": 0, "new_yes": 0})
        self.assertEqual(summary["reject_reasons_top"], [])


if __name__ == "__main__":
    unittest.main()
