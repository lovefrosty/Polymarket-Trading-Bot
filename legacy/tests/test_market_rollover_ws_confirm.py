import asyncio
import json
import unittest
from dataclasses import dataclass

from core.metrics import Metrics
from core.order_book import OrderBook
from data.polymarket_ws import MarketWSClient, ResubscribeResult, WSConfig
from scripts.run_system import (
    _confirm_diag_summary,
    _old_market_non_viable,
    _rollback_post_switch_abort,
    _should_adopt_switched_market_without_readiness,
)


class _DummyTape:
    def __init__(self) -> None:
        self.records = []

    def write(self, **kwargs) -> None:  # type: ignore[no-untyped-def]
        self.records.append(kwargs)


class _DummyWS:
    def __init__(self) -> None:
        self.payloads = []
        self.closed = False

    async def send(self, payload: str) -> None:
        self.payloads.append(payload)

    async def close(self) -> None:
        self.closed = True


@dataclass
class _RollbackRuntime:
    books: dict


class _RollbackClient:
    def __init__(self, result: ResubscribeResult) -> None:
        self.result = result
        self.calls = []
        self.books = None

    def set_books(self, books) -> None:  # type: ignore[no-untyped-def]
        self.books = books

    async def resubscribe(self, new_asset_ids, first_book_timeout_secs=5.0):  # type: ignore[no-untyped-def]
        self.calls.append((list(new_asset_ids), float(first_book_timeout_secs)))
        return self.result


def _book(asset_id: str) -> OrderBook:
    return OrderBook(asset_id=asset_id, bids={}, asks={})


class TestMarketRolloverWSConfirm(unittest.IsolatedAsyncioTestCase):
    def test_old_market_non_viable_at_boundary(self) -> None:
        self.assertTrue(_old_market_non_viable(now_ms=1_000, market_end_ts_ms=1_000))
        self.assertTrue(_old_market_non_viable(now_ms=1_001, market_end_ts_ms=1_000))
        self.assertFalse(_old_market_non_viable(now_ms=999, market_end_ts_ms=1_000))
        self.assertFalse(_old_market_non_viable(now_ms=1_000, market_end_ts_ms=None))

    def test_should_adopt_switched_market_without_readiness(self) -> None:
        self.assertTrue(
            _should_adopt_switched_market_without_readiness(
                token_ids_changed=True,
                switch_status="committed",
                commit_action="RETRY",
                old_market_non_viable=True,
            )
        )
        self.assertFalse(
            _should_adopt_switched_market_without_readiness(
                token_ids_changed=True,
                switch_status="committed",
                commit_action="COMMIT",
                old_market_non_viable=True,
            )
        )
        self.assertFalse(
            _should_adopt_switched_market_without_readiness(
                token_ids_changed=True,
                switch_status="noop_same_market",
                commit_action="RETRY",
                old_market_non_viable=True,
            )
        )
        self.assertFalse(
            _should_adopt_switched_market_without_readiness(
                token_ids_changed=True,
                switch_status="committed",
                commit_action="RETRY",
                old_market_non_viable=False,
            )
        )

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

    async def _emit_wrapped_snapshot(
        self,
        client: MarketWSClient,
        *,
        asset_id: str,
        seq: int,
        recv_wall_ms: int,
        t_event_ms: int,
    ) -> None:
        msg = {
            "type": "market",
            "data": {
                "asset_id": asset_id,
                "bids": [{"price": 0.49, "size": 10.0}],
                "asks": [{"price": 0.51, "size": 10.0}],
                "timestamp": t_event_ms,
                "sequence": seq,
            },
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
        summary = _confirm_diag_summary(result.confirm_diag, result.confirm_wait_ms)
        self.assertEqual(summary["required_updates_per_token"], 2)
        self.assertEqual(summary["counts_by_asset"], {"new_no": 0, "new_yes": 0})
        self.assertEqual(summary["missing_assets"], ["new_no", "new_yes"])
        self.assertEqual(summary["rejects_by_asset"], {"new_no": 0, "new_yes": 0})
        self.assertEqual(summary["reject_reasons_top"], [])
        self.assertEqual(summary["failure_class"], "NO_PENDING_MESSAGES")
        self.assertTrue(summary["reconnect_attempted"])
        self.assertTrue(summary["unsubscribe_before_subscribe"])

    async def test_wrapped_pending_snapshots_commit_successfully(self) -> None:
        client = self._build_client()
        task = asyncio.create_task(client.resubscribe(["new_yes", "new_no"], first_book_timeout_secs=1.0))
        await asyncio.sleep(0.01)

        await self._emit_wrapped_snapshot(client, asset_id="new_yes", seq=1, recv_wall_ms=50_000, t_event_ms=49_950)
        await self._emit_wrapped_snapshot(client, asset_id="new_no", seq=2, recv_wall_ms=50_050, t_event_ms=50_000)
        await self._emit_wrapped_snapshot(client, asset_id="new_yes", seq=3, recv_wall_ms=50_100, t_event_ms=50_050)
        await self._emit_wrapped_snapshot(client, asset_id="new_no", seq=4, recv_wall_ms=50_150, t_event_ms=50_100)

        result = await task
        self.assertEqual(result.status, "committed")
        self.assertGreaterEqual(int(result.confirm_diag["counts_by_asset"]["new_yes"]), 2)
        self.assertGreaterEqual(int(result.confirm_diag["counts_by_asset"]["new_no"]), 2)

    async def test_unsubscribe_sent_before_new_subscribe(self) -> None:
        client = self._build_client()

        async def _fail_reconnect(timeout_secs: float) -> bool:
            return False

        client._reconnect_pending_subscription = _fail_reconnect  # type: ignore[method-assign]
        result = await client.resubscribe(["new_yes", "new_no"], first_book_timeout_secs=0.05)
        self.assertEqual(result.status, "abort_timeout_waiting_confirmation")
        payloads = [json.loads(payload) for payload in client._ws.payloads]  # type: ignore[union-attr]
        self.assertEqual(payloads[0]["type"], "unsubscribe")
        self.assertEqual(sorted(payloads[0]["assets_ids"]), ["old_no", "old_yes"])
        self.assertEqual(payloads[1]["type"], "market")
        self.assertEqual(sorted(payloads[1]["assets_ids"]), ["new_no", "new_yes"])
        self.assertTrue(result.confirm_diag["unsubscribe_before_subscribe"])

    async def test_reconnect_fallback_success_commits(self) -> None:
        client = self._build_client()

        async def _reconnect_success(timeout_secs: float) -> bool:
            await self._emit_snapshot(client, asset_id="new_yes", seq=1, recv_wall_ms=60_000, t_event_ms=59_950)
            await self._emit_snapshot(client, asset_id="new_no", seq=2, recv_wall_ms=60_050, t_event_ms=60_000)
            await self._emit_snapshot(client, asset_id="new_yes", seq=3, recv_wall_ms=60_100, t_event_ms=60_050)
            await self._emit_snapshot(client, asset_id="new_no", seq=4, recv_wall_ms=60_150, t_event_ms=60_100)
            if client._pending_first_book_event is not None:
                client._pending_first_book_event.set()
            return True

        client._reconnect_pending_subscription = _reconnect_success  # type: ignore[method-assign]
        result = await client.resubscribe(["new_yes", "new_no"], first_book_timeout_secs=0.05)
        self.assertEqual(result.status, "committed")
        self.assertEqual(result.confirm_diag["failure_class"], "RECONNECTED_FOR_ROLLOVER")
        self.assertTrue(result.confirm_diag["reconnect_attempted"])

    async def test_reconnect_fallback_failure_is_deterministic(self) -> None:
        client = self._build_client()

        async def _reconnect_failure(timeout_secs: float) -> bool:
            return False

        client._reconnect_pending_subscription = _reconnect_failure  # type: ignore[method-assign]
        result = await client.resubscribe(["new_yes", "new_no"], first_book_timeout_secs=0.05)
        self.assertEqual(result.status, "abort_timeout_waiting_confirmation")
        self.assertEqual(result.confirm_diag["failure_class"], "NO_PENDING_MESSAGES")
        self.assertTrue(result.confirm_diag["reconnect_attempted"])

    async def test_post_switch_rollback_restores_previous_assets(self) -> None:
        books = {"old_yes": _book("old_yes"), "old_no": _book("old_no")}
        runtime = _RollbackRuntime(books=books)
        rollback_result = ResubscribeResult(
            status="committed",
            previous_asset_ids=["new_yes", "new_no"],
            new_asset_ids=["old_yes", "old_no"],
            active_subscription_id=9,
            confirm_diag={
                "pending_asset_ids": ["old_yes", "old_no"],
                "counts_by_asset": {"old_yes": 2, "old_no": 2},
                "rejects_by_asset": {"old_yes": 0, "old_no": 0},
                "required_updates_per_token": 2,
            },
            confirm_wait_ms=23.0,
            unsubscribe_ms=7.0,
        )
        client = _RollbackClient(rollback_result)
        payload = await _rollback_post_switch_abort(
            market_client=client,
            runtime=runtime,
            previous_token_ids=["old_yes", "old_no"],
            confirm_timeout_secs=5.0,
        )
        self.assertEqual(client.calls, [(["old_yes", "old_no"], 5.0)])
        self.assertIs(client.books, books)
        self.assertTrue(payload["post_switch_abort"])
        self.assertTrue(payload["rollback_attempted"])
        self.assertEqual(payload["rollback_status"], "committed")
        self.assertEqual(payload["rollback_confirm_diag_summary"]["missing_assets"], [])

    async def test_post_switch_rollback_failure_is_explicit(self) -> None:
        runtime = _RollbackRuntime(books={"old_yes": _book("old_yes")})
        rollback_result = ResubscribeResult(
            status="abort_timeout_waiting_confirmation",
            previous_asset_ids=["new_yes", "new_no"],
            new_asset_ids=["old_yes", "old_no"],
            active_subscription_id=8,
            abort_reason="WS_NOT_LIVE_CONFIRM_TIMEOUT",
            confirm_diag={
                "pending_asset_ids": ["old_yes", "old_no"],
                "counts_by_asset": {"old_yes": 0, "old_no": 0},
                "rejects_by_asset": {"old_yes": 0, "old_no": 0},
                "required_updates_per_token": 2,
                "failure_class": "NO_PENDING_MESSAGES",
            },
            confirm_wait_ms=50.0,
        )
        client = _RollbackClient(rollback_result)
        payload = await _rollback_post_switch_abort(
            market_client=client,
            runtime=runtime,
            previous_token_ids=["old_yes", "old_no"],
            confirm_timeout_secs=5.0,
        )
        self.assertEqual(payload["rollback_status"], "abort_timeout_waiting_confirmation")
        self.assertEqual(payload["rollback_confirm_diag_summary"]["failure_class"], "NO_PENDING_MESSAGES")
        self.assertEqual(payload["rollback_confirm_diag_summary"]["missing_assets"], ["old_no", "old_yes"])


if __name__ == "__main__":
    unittest.main()
