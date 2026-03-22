from __future__ import annotations

import asyncio
import json
import ssl
from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional, Sequence

try:
    import websockets
except ModuleNotFoundError:  # pragma: no cover
    websockets = None

try:  # pragma: no cover
    import certifi
except ModuleNotFoundError:  # pragma: no cover
    certifi = None


USER_WS_URL = "wss://ws-subscriptions-clob.polymarket.com/ws/user"


@dataclass(frozen=True)
class UserFeedStatus:
    connected: bool
    received_messages: int
    trade_events: int
    order_events: int


class PolymarketUserFeed:
    """WebSocket client for Polymarket user channel (fills, order updates).

    Follows the same async pattern as PolymarketMarketFeed.
    """

    def __init__(
        self,
        *,
        api_key: str,
        api_secret: str,
        api_passphrase: str,
        on_message: Optional[Callable[[Dict[str, Any]], None]] = None,
        reconnect_base_secs: float = 1.0,
        reconnect_max_secs: float = 30.0,
    ) -> None:
        self._api_key = str(api_key)
        self._api_secret = str(api_secret)
        self._api_passphrase = str(api_passphrase)
        self._on_message = on_message
        self._reconnect_base_secs = float(reconnect_base_secs)
        self._reconnect_max_secs = float(reconnect_max_secs)
        self._stop_event = asyncio.Event()
        self._connected = False
        self._received_messages = 0
        self._trade_events = 0
        self._order_events = 0

    def stop(self) -> None:
        self._stop_event.set()

    def status(self) -> UserFeedStatus:
        return UserFeedStatus(
            connected=bool(self._connected),
            received_messages=int(self._received_messages),
            trade_events=int(self._trade_events),
            order_events=int(self._order_events),
        )

    async def run(self) -> None:
        if websockets is None:
            raise RuntimeError("websockets_not_installed")
        backoff = self._reconnect_base_secs
        while not self._stop_event.is_set():
            try:
                ssl_context = _ssl_context()
                async with websockets.connect(
                    USER_WS_URL,
                    ping_interval=20,
                    ping_timeout=20,
                    ssl=ssl_context,
                ) as ws:
                    self._connected = True
                    # Subscribe to user channel
                    await ws.send(json.dumps({
                        "auth": {
                            "apiKey": self._api_key,
                            "secret": self._api_secret,
                            "passphrase": self._api_passphrase,
                        },
                        "type": "user",
                    }))
                    backoff = self._reconnect_base_secs
                    await self._receive_loop(ws)
            except Exception:
                if self._stop_event.is_set():
                    break
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2.0, self._reconnect_max_secs)
            finally:
                self._connected = False

    async def _receive_loop(self, ws) -> None:
        while not self._stop_event.is_set():
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=30.0)
            except asyncio.TimeoutError:
                continue
            except Exception:
                return
            self._received_messages += 1
            try:
                message = json.loads(raw)
            except json.JSONDecodeError:
                continue
            event_type = _classify_event(message)
            if event_type == "trade":
                self._trade_events += 1
            elif event_type == "order":
                self._order_events += 1
            if self._on_message is not None:
                self._on_message(message)


def _classify_event(message: Dict[str, Any]) -> str:
    """Classify a user WS message as trade, order, or unknown."""
    data = message.get("data", message)
    if isinstance(data, list):
        data = data[0] if data else {}
    if not isinstance(data, dict):
        return "unknown"
    et = str(data.get("event_type") or data.get("type") or data.get("event") or "").lower()
    if et in {"trade", "fill", "matched"}:
        return "trade"
    if et in {"order", "placement", "update", "cancellation", "cancelled", "canceled"}:
        return "order"
    return "unknown"


def _ssl_context() -> ssl.SSLContext:
    if certifi is not None:
        return ssl.create_default_context(cafile=certifi.where())
    return ssl.create_default_context()
