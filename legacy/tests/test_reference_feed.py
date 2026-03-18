from __future__ import annotations

import asyncio
import unittest
from unittest.mock import patch

from core.reference_feed import ReferenceFeed, ReferenceFeedConfig


class _DummyTape:
    def __init__(self) -> None:
        self.records = []

    def write(self, **kwargs) -> None:  # type: ignore[no-untyped-def]
        self.records.append(kwargs)


class _FlakyAdapter:
    def __init__(self, records) -> None:
        self._stop_event = asyncio.Event()
        self._records = list(records)

    def stop(self) -> None:
        self._stop_event.set()

    def _poll_symbol(self, symbol: str):  # type: ignore[no-untyped-def]
        if not self._records:
            raise AssertionError("no records left")
        item = self._records.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item


def _record(symbol: str, value: float) -> dict:
    return {
        "channel": "reference",
        "event_type": "reference_tick",
        "market": symbol,
        "asset_id": None,
        "t_event_ms": 1_000,
        "t_recv_wall_ms": 1_001,
        "t_recv_wall_iso": "2026-03-10T21:00:00.000Z",
        "t_recv_mono_ns": 123,
        "raw": {
            "source": "spot",
            "venue": "coinbase_spot",
            "symbol": symbol,
            "value": value,
            "bid": value - 0.5,
            "ask": value + 0.5,
            "t_event_ms": 1_000,
        },
        "parse_warnings": [],
        "out_of_order": False,
    }


class TestReferenceFeed(unittest.IsolatedAsyncioTestCase):
    async def test_poll_exception_emits_error_and_recovers(self) -> None:
        tape = _DummyTape()
        quotes = []
        feed = ReferenceFeed(
            aggregator=None,
            tape=tape,
            config=ReferenceFeedConfig(symbols=["BTC"], poll_interval_secs=0.01, source="poll_coinbase"),
            on_quote=quotes.append,
        )
        feed._adapter = _FlakyAdapter([RuntimeError("boom"), _record("BTC", 70_000.0)])  # type: ignore[attr-defined]

        real_handle = feed._handle_event

        def _handle_and_stop(record):  # type: ignore[no-untyped-def]
            real_handle(record)
            feed.stop()

        feed._handle_event = _handle_and_stop  # type: ignore[method-assign]
        await feed.run()

        statuses = [r for r in tape.records if r["event_type"] == "reference_feed_status"]
        ticks = [r for r in tape.records if r["event_type"] == "reference_tick"]
        self.assertEqual(len(ticks), 1)
        self.assertEqual(len(quotes), 1)
        self.assertEqual(statuses[0]["raw"]["status"], "error")
        self.assertEqual(statuses[0]["raw"]["venue"], "coinbase_spot")
        self.assertEqual(statuses[0]["raw"]["source"], "spot")
        self.assertEqual(statuses[0]["raw"]["symbol"], "BTC")
        self.assertEqual(statuses[0]["raw"]["consecutive_failures"], 1)
        self.assertEqual(statuses[0]["raw"]["error_class"], "RuntimeError")
        self.assertEqual(statuses[1]["raw"]["status"], "recovered")
        self.assertEqual(statuses[1]["raw"]["consecutive_failures"], 1)
        self.assertIsNone(statuses[1]["raw"]["error_class"])

    async def test_successful_polling_path_unchanged(self) -> None:
        tape = _DummyTape()
        quotes = []
        feed = ReferenceFeed(
            aggregator=None,
            tape=tape,
            config=ReferenceFeedConfig(symbols=["BTC"], poll_interval_secs=0.01, source="poll_coinbase"),
            on_quote=quotes.append,
        )
        feed._adapter = _FlakyAdapter([_record("BTC", 70_100.0)])  # type: ignore[attr-defined]

        real_handle = feed._handle_event

        def _handle_and_stop(record):  # type: ignore[no-untyped-def]
            real_handle(record)
            feed.stop()

        feed._handle_event = _handle_and_stop  # type: ignore[method-assign]
        await feed.run()

        self.assertEqual(len([r for r in tape.records if r["event_type"] == "reference_tick"]), 1)
        self.assertEqual(len([r for r in tape.records if r["event_type"] == "reference_feed_status"]), 0)
        self.assertEqual(len(quotes), 1)


if __name__ == "__main__":
    unittest.main()
