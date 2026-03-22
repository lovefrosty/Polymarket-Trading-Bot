from __future__ import annotations

import asyncio
import json
import unittest
from unittest.mock import patch

from core.reference_ws import (
    ReferenceWSClient,
    ReferenceWSConfig,
    _parse_kraken_futures_message,
    _resolve_kraken_futures_product_ids,
)


class _DummyTape:
    def __init__(self) -> None:
        self.records = []

    def write(self, **kwargs) -> None:  # type: ignore[no-untyped-def]
        self.records.append(kwargs)


class _FakeWS:
    def __init__(self, recv_items, sends=None) -> None:
        self._recv_items = list(recv_items)
        self.sends = sends if sends is not None else []

    async def send(self, payload: str) -> None:
        self.sends.append(payload)

    async def recv(self):
        if not self._recv_items:
            raise AssertionError("no recv items left")
        item = self._recv_items.pop(0)
        if isinstance(item, BaseException):
            raise item
        if callable(item):
            return await item()
        return item


class _FakeConnect:
    def __init__(self, ws: _FakeWS) -> None:
        self.ws = ws

    async def __aenter__(self):
        return self.ws

    async def __aexit__(self, exc_type, exc, tb):
        return False


class TestReferenceWSKrakenFutures(unittest.IsolatedAsyncioTestCase):
    def test_resolves_active_btc_perpetual_from_instruments(self) -> None:
        payload = {
            "instruments": [
                {"symbol": "FI_XBTUSD_260327", "tradeable": True, "lastTradingTime": 1770000000000},
                {"symbol": "PF_XBTUSD", "tradeable": True, "lastTradingTime": None},
                {"symbol": "PI_XBTUSD", "tradeable": True, "lastTradingTime": None},
            ]
        }
        with patch("core.reference_ws._fetch_json", return_value=payload):
            self.assertEqual(_resolve_kraken_futures_product_ids(["BTC"]), ["PI_XBTUSD"])

    def test_parses_futures_ticker_preferring_mark_price(self) -> None:
        msg = {
            "time": 1773159414077,
            "product_id": "PI_XBTUSD",
            "feed": "ticker",
            "bid": 71243.5,
            "ask": 71277.0,
            "last": 71186.0,
            "markPrice": 71267.0378466331,
            "pair": "XBT:USD",
        }
        updates = _parse_kraken_futures_message(msg)
        self.assertEqual(len(updates), 1)
        update = updates[0]
        self.assertEqual(update["source"], "perp")
        self.assertEqual(update["venue"], "kraken_futures_perp")
        self.assertEqual(update["symbol"], "BTC")
        self.assertEqual(update["t_event_ms"], 1773159414077)
        self.assertAlmostEqual(update["mid"], 71267.0378466331)

    def test_ignores_subscribe_ack_and_non_ticker_messages(self) -> None:
        self.assertEqual(
            _parse_kraken_futures_message({"event": "subscribed", "feed": "ticker", "product_ids": ["PI_XBTUSD"]}),
            [],
        )
        self.assertEqual(_parse_kraken_futures_message({"event": "info", "version": 1}), [])

    async def test_emits_perp_reference_tick_and_quote(self) -> None:
        tape = _DummyTape()
        quotes = []
        client = ReferenceWSClient(
            tape=tape,
            config=ReferenceWSConfig(venue="kraken_futures", symbols=["BTC"]),
            on_quote=quotes.append,
        )
        msg = {
            "time": 1773159414077,
            "product_id": "PI_XBTUSD",
            "feed": "ticker",
            "bid": 71243.5,
            "ask": 71277.0,
            "last": 71186.0,
            "markPrice": 71267.0378466331,
            "pair": "XBT:USD",
        }
        await client._handle_message(  # type: ignore[attr-defined]
            raw=json.dumps(msg),
            recv_mono_ns=123456789,
            recv_wall_ms=1773159414100,
            recv_wall_iso="2026-03-10T16:30:14.100Z",
        )
        self.assertEqual(len(tape.records), 1)
        self.assertEqual(tape.records[0]["raw"]["source"], "perp")
        self.assertEqual(tape.records[0]["raw"]["venue"], "kraken_futures_perp")
        self.assertEqual(len(quotes), 1)
        self.assertEqual(quotes[0].source, "perp")
        self.assertEqual(quotes[0].symbol, "BTC")

    async def test_disconnect_triggers_reconnect_and_status_events(self) -> None:
        tape = _DummyTape()
        first = _FakeWS([RuntimeError("socket_lost")])
        second = _FakeWS(
            [
                json.dumps(
                    {
                        "time": 1773159414077,
                        "product_id": "PI_XBTUSD",
                        "feed": "ticker",
                        "bid": 71243.5,
                        "ask": 71277.0,
                        "markPrice": 71267.0,
                        "pair": "XBT:USD",
                    }
                )
            ]
        )
        client = ReferenceWSClient(tape=tape, config=ReferenceWSConfig(venue="kraken_futures", symbols=["BTC"]))

        def _connect(*args, **kwargs):
            ws = first if not first.sends else second
            return _FakeConnect(ws)

        with (
            patch("core.reference_ws.websockets.connect", side_effect=_connect),
            patch("core.reference_ws._resolve_kraken_futures_product_ids", return_value=["PI_XBTUSD"]),
            patch("core.reference_ws.asyncio.sleep", new=unittest.mock.AsyncMock()),
        ):
            original = client._handle_message

            async def _handle_and_stop(raw, recv_mono_ns, recv_wall_ms, recv_wall_iso):
                await original(raw, recv_mono_ns, recv_wall_ms, recv_wall_iso)
                client.stop()

            client._handle_message = _handle_and_stop  # type: ignore[method-assign]
            await client.run()

        statuses = [r for r in tape.records if r["event_type"] == "reference_ws_status"]
        self.assertTrue(any(r["raw"]["status"] == "disconnected" for r in statuses))
        self.assertTrue(any(r["raw"]["status"] == "reconnecting" for r in statuses))
        self.assertTrue(any(r["raw"]["status"] == "reconnected" for r in statuses))
        self.assertEqual(first.sends, [json.dumps({"event": "subscribe", "feed": "ticker", "product_ids": ["PI_XBTUSD"]})])
        self.assertEqual(second.sends, [json.dumps({"event": "subscribe", "feed": "ticker", "product_ids": ["PI_XBTUSD"]})])

    async def test_inactivity_timeout_triggers_reconnect(self) -> None:
        tape = _DummyTape()

        async def _stall():
            await asyncio.get_running_loop().create_future()

        first = _FakeWS([_stall])
        second = _FakeWS(
            [
                json.dumps(
                    {
                        "time": 1773159415077,
                        "product_id": "PI_XBTUSD",
                        "feed": "ticker",
                        "bid": 71240.0,
                        "ask": 71270.0,
                        "markPrice": 71260.0,
                        "pair": "XBT:USD",
                    }
                )
            ]
        )
        client = ReferenceWSClient(
            tape=tape,
            config=ReferenceWSConfig(
                venue="kraken_futures",
                symbols=["BTC"],
                inactivity_timeout_secs=0.01,
            ),
        )
        connects = [first, second]

        def _connect(*args, **kwargs):
            return _FakeConnect(connects.pop(0))

        with (
            patch("core.reference_ws.websockets.connect", side_effect=_connect),
            patch("core.reference_ws._resolve_kraken_futures_product_ids", return_value=["PI_XBTUSD"]),
            patch("core.reference_ws.asyncio.sleep", new=unittest.mock.AsyncMock()),
        ):
            original = client._handle_message

            async def _handle_and_stop(raw, recv_mono_ns, recv_wall_ms, recv_wall_iso):
                await original(raw, recv_mono_ns, recv_wall_ms, recv_wall_iso)
                client.stop()

            client._handle_message = _handle_and_stop  # type: ignore[method-assign]
            await client.run()

        statuses = [r for r in tape.records if r["event_type"] == "reference_ws_status"]
        timeout_errors = [r for r in statuses if r["raw"]["status"] == "error"]
        self.assertTrue(timeout_errors)
        self.assertEqual(timeout_errors[0]["raw"]["error_class"], "TimeoutError")
        self.assertEqual(timeout_errors[0]["raw"]["error_detail"], "inactivity_timeout")
        self.assertTrue(any(r["raw"]["status"] == "reconnected" for r in statuses))

    async def test_reconnect_preserves_perp_parsing(self) -> None:
        tape = _DummyTape()
        first = _FakeWS([RuntimeError("boom")])
        second = _FakeWS(
            [
                json.dumps(
                    {
                        "time": 1773159416077,
                        "product_id": "PI_XBTUSD",
                        "feed": "ticker",
                        "bid": 71210.0,
                        "ask": 71220.0,
                        "last": 71215.0,
                        "pair": "XBT:USD",
                    }
                )
            ]
        )
        client = ReferenceWSClient(tape=tape, config=ReferenceWSConfig(venue="kraken_futures", symbols=["BTC"]))
        connects = [first, second]

        def _connect(*args, **kwargs):
            return _FakeConnect(connects.pop(0))

        with (
            patch("core.reference_ws.websockets.connect", side_effect=_connect),
            patch("core.reference_ws._resolve_kraken_futures_product_ids", return_value=["PI_XBTUSD"]),
            patch("core.reference_ws.asyncio.sleep", new=unittest.mock.AsyncMock()),
        ):
            original = client._handle_message

            async def _handle_and_stop(raw, recv_mono_ns, recv_wall_ms, recv_wall_iso):
                await original(raw, recv_mono_ns, recv_wall_ms, recv_wall_iso)
                client.stop()

            client._handle_message = _handle_and_stop  # type: ignore[method-assign]
            await client.run()

        ticks = [r for r in tape.records if r["event_type"] == "reference_tick"]
        self.assertEqual(len(ticks), 1)
        self.assertEqual(ticks[0]["raw"]["source"], "perp")
        self.assertEqual(ticks[0]["raw"]["venue"], "kraken_futures_perp")


if __name__ == "__main__":
    unittest.main()
