from __future__ import annotations

import asyncio
import json
import ssl
from collections.abc import Mapping, Sequence as SequenceABC
from dataclasses import dataclass
from typing import Callable, List, Optional, Sequence

from core_mm.book_manager import BookManager

try:
    import websockets
except ModuleNotFoundError:  # pragma: no cover
    websockets = None

try:  # pragma: no cover
    import certifi
except ModuleNotFoundError:  # pragma: no cover
    certifi = None


MARKET_WS_URL = "wss://ws-subscriptions-clob.polymarket.com/ws/market"


@dataclass(frozen=True)
class MarketFeedStatus:
    connected: bool
    subscribed_token_ids: tuple[str, ...]
    received_messages: int
    applied_book_updates: int


class PolymarketMarketFeed:
    def __init__(
        self,
        *,
        book_manager: BookManager,
        token_ids: Sequence[str],
        on_applied_update: Optional[Callable[[], None]] = None,
        reconnect_base_secs: float = 1.0,
        reconnect_max_secs: float = 30.0,
    ) -> None:
        self._book_manager = book_manager
        self._on_applied_update = on_applied_update
        self._desired_token_ids = tuple(sorted({str(token_id) for token_id in token_ids if token_id}))
        self._active_token_ids: tuple[str, ...] = ()
        self._reconnect_base_secs = float(reconnect_base_secs)
        self._reconnect_max_secs = float(reconnect_max_secs)
        self._stop_event = asyncio.Event()
        self._reconnect_event = asyncio.Event()
        self._ws = None
        self._connected = False
        self._received_messages = 0
        self._applied_book_updates = 0

    def set_token_ids(self, token_ids: Sequence[str]) -> bool:
        normalized = tuple(sorted({str(token_id) for token_id in token_ids if token_id}))
        if normalized == self._desired_token_ids:
            return False
        self._desired_token_ids = normalized
        self._reconnect_event.set()
        return True

    def stop(self) -> None:
        self._stop_event.set()
        self._reconnect_event.set()

    def status(self) -> MarketFeedStatus:
        return MarketFeedStatus(
            connected=bool(self._connected),
            subscribed_token_ids=self._active_token_ids,
            received_messages=int(self._received_messages),
            applied_book_updates=int(self._applied_book_updates),
        )

    async def run(self) -> None:
        if websockets is None:
            raise RuntimeError("websockets_not_installed")
        backoff = self._reconnect_base_secs
        while not self._stop_event.is_set():
            if not self._desired_token_ids:
                await asyncio.sleep(0.25)
                continue
            try:
                ssl_context = _ssl_context()
                async with websockets.connect(
                    MARKET_WS_URL,
                    ping_interval=20,
                    ping_timeout=20,
                    ssl=ssl_context,
                ) as ws:
                    self._ws = ws
                    self._connected = True
                    await ws.send(json.dumps(_market_subscribe_payload(self._desired_token_ids)))
                    self._active_token_ids = self._desired_token_ids
                    backoff = self._reconnect_base_secs
                    await self._receive_loop(ws)
            except Exception:
                if self._stop_event.is_set():
                    break
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2.0, self._reconnect_max_secs)
            finally:
                self._connected = False
                self._ws = None
                self._active_token_ids = ()
                self._reconnect_event.clear()

    async def _receive_loop(self, ws) -> None:
        while not self._stop_event.is_set():
            recv_task = asyncio.create_task(ws.recv())
            reconnect_task = asyncio.create_task(self._reconnect_event.wait())
            done, pending = await asyncio.wait(
                {recv_task, reconnect_task},
                return_when=asyncio.FIRST_COMPLETED,
            )
            for task in pending:
                task.cancel()
            if reconnect_task in done and self._reconnect_event.is_set():
                await ws.close()
                return
            if recv_task not in done:
                continue
            raw = recv_task.result()
            self._received_messages += 1
            try:
                message = json.loads(raw)
            except json.JSONDecodeError:
                continue
            applied = _apply_market_message(self._book_manager, message)
            self._applied_book_updates += applied
            if applied > 0 and self._on_applied_update is not None:
                self._on_applied_update()


def _market_subscribe_payload(token_ids: Sequence[str]) -> dict:
    return {"type": "market", "assets_ids": [str(token_id) for token_id in token_ids]}


def _apply_market_message(book_manager: BookManager, message: object) -> int:
    if isinstance(message, Mapping):
        return int(book_manager.process_message(message))
    if isinstance(message, SequenceABC) and not isinstance(message, (str, bytes, bytearray)):
        applied = 0
        for item in message:
            if isinstance(item, Mapping):
                applied += int(book_manager.process_message(item))
        return applied
    return 0


def _ssl_context() -> ssl.SSLContext:
    if certifi is not None:
        return ssl.create_default_context(cafile=certifi.where())
    return ssl.create_default_context()
