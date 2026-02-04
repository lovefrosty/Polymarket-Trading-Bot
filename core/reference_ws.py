from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import ssl
import time
from typing import Any, Dict, List, Optional, Tuple

try:
    import websockets
except ModuleNotFoundError:  # pragma: no cover - optional dependency
    websockets = None
try:  # pragma: no cover - optional dependency
    import certifi
except ModuleNotFoundError:  # pragma: no cover - optional dependency
    certifi = None

from core.event_tape import EventTape


KRAKEN_WS_URL = "wss://ws.kraken.com"



@dataclass(frozen=True)
class ReferenceWSConfig:
    venue: str
    symbols: List[str]
    ping_interval_secs: float = 20.0
    ping_timeout_secs: float = 20.0


class ReferenceWSClient:
    def __init__(
        self,
        tape: EventTape,
        config: ReferenceWSConfig,
    ) -> None:
        self.tape = tape
        self.config = config
        self._stop_event = asyncio.Event()
        self._last_recv_mono_ns: Dict[str, int] = {}

    def stop(self) -> None:
        self._stop_event.set()

    async def run(self) -> None:
        if websockets is None:
            raise RuntimeError("websockets_not_installed")
        if self.config.venue != "kraken":
            raise ValueError(f"unsupported_reference_venue:{self.config.venue}")
        ssl_context = _ssl_context()
        async with websockets.connect(
            KRAKEN_WS_URL,
            ping_interval=self.config.ping_interval_secs,
            ping_timeout=self.config.ping_timeout_secs,
            ssl=ssl_context,
        ) as ws:
            await self._subscribe(ws)
            while not self._stop_event.is_set():
                raw = await ws.recv()
                recv_mono_ns = time.monotonic_ns()
                recv_wall_ms = int(time.time() * 1000)
                recv_wall_iso = _utc_iso()
                await self._handle_message(raw, recv_mono_ns, recv_wall_ms, recv_wall_iso)

    async def _subscribe(self, ws) -> None:
        symbols = [_kraken_symbol(symbol) for symbol in self.config.symbols]
        payload = {"event": "subscribe", "pair": symbols, "subscription": {"name": "ticker"}}
        await ws.send(json.dumps(payload))

    async def _handle_message(
        self, raw: str, recv_mono_ns: int, recv_wall_ms: int, recv_wall_iso: str
    ) -> None:
        try:
            msg = json.loads(raw)
        except json.JSONDecodeError:
            return
        
        
        updates = _parse_kraken_message(msg)
        if not updates:
            return
        for update in updates:
            self._emit_event(update, recv_mono_ns, recv_wall_ms, recv_wall_iso, raw)

    def _emit_event(
        self,
        update: Dict[str, Any],
        recv_mono_ns: int,
        recv_wall_ms: int,
        recv_wall_iso: str,
        raw_payload: str,
    ) -> None:
        symbol = update.get("symbol")
        if symbol is None:
            return
        parse_warnings: List[str] = []
        out_of_order = False
        last_recv = self._last_recv_mono_ns.get(symbol)
        if last_recv is not None and recv_mono_ns < last_recv:
            parse_warnings.append("RECV_MONO_REGRESS")
            out_of_order = True
        if not out_of_order:
            self._last_recv_mono_ns[symbol] = recv_mono_ns

        t_event_ms = update.get("t_event_ms")
        if t_event_ms is None:
            parse_warnings.append("MISSING_EVENT_TS")
        else:
            try:
                t_event_ms = int(t_event_ms)
            except (TypeError, ValueError):
                t_event_ms = None
                parse_warnings.append("MISSING_EVENT_TS")
        if t_event_ms is not None and t_event_ms > recv_wall_ms:
            parse_warnings.append("EVENT_AFTER_RECV")

        bid = update.get("bid")
        ask = update.get("ask")
        mid = update.get("mid")
        raw = {
            "source": "spot",
            "venue": "kraken_spot",
            "symbol": symbol,
            "bid": bid,
            "ask": ask,
            "mid": mid,
            "value": mid,
            "t_event_ms": t_event_ms,
            "raw_payload": raw_payload,
        }

        self.tape.write(
            channel="reference",
            event_type="reference_tick",
            market=symbol,
            asset_id=None,
            t_event_ms=t_event_ms,
            raw=raw,
            parse_warnings=parse_warnings,
            out_of_order=out_of_order,
            t_recv_wall_iso=recv_wall_iso,
            t_recv_wall_ms=recv_wall_ms,
            t_recv_mono_ns=recv_mono_ns,
        )


def _parse_kraken_message(msg: Any) -> List[Dict[str, Any]]:
    updates: List[Dict[str, Any]] = []
    if isinstance(msg, dict):
        channel = msg.get("channel")
        if channel != "ticker":
            return updates
        data = msg.get("data") or []
        for entry in data:
            if not isinstance(entry, dict):
                continue
            symbol = entry.get("symbol")
            if symbol is None:
                continue
            bid = _parse_float(entry.get("bid"))
            ask = _parse_float(entry.get("ask"))
            last = _parse_float(entry.get("last"))
            mid = _mid(bid, ask) if bid is not None and ask is not None else last
            if mid is None:
                continue
            updates.append(
                {
                    "symbol": _normalize_symbol(symbol),
                    "bid": bid,
                    "ask": ask,
                    "mid": mid,
                    "t_event_ms": _parse_ts_ms(entry.get("timestamp")),
                }
            )
        return updates
    if isinstance(msg, list) and len(msg) >= 4 and msg[2] == "ticker":
        data = msg[1]
        if not isinstance(data, dict):
            return updates
        pair = msg[3]
        bid = _parse_float((data.get("b") or [None])[0])
        ask = _parse_float((data.get("a") or [None])[0])
        last = _parse_float((data.get("c") or [None])[0])
        mid = _mid(bid, ask) if bid is not None and ask is not None else last
        if mid is None:
            return updates
        updates.append(
            {
                "symbol": _normalize_symbol(pair),
                "bid": bid,
                "ask": ask,
                "mid": mid,
                "t_event_ms": None,
            }
        )
    return updates


def _kraken_symbol(symbol: str) -> str:
    upper = symbol.upper()
    if upper == "BTC":
        return "XBT/USD"
    return f"{upper}/USD"


def _normalize_symbol(pair: str) -> str:
    if not pair:
        return pair
    pair = pair.replace("/", "")
    if pair.upper().startswith("XBT"):
        return "BTC"
    if pair.upper().endswith("USD"):
        return pair[:-3]
    return pair


def _parse_float(value: object) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _ssl_context() -> Optional[ssl.SSLContext]:
    if certifi is None:
        return None
    try:
        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return None


def _mid(bid: Optional[float], ask: Optional[float]) -> Optional[float]:
    if bid is None or ask is None:
        return None
    return (bid + ask) / 2.0


def _parse_ts_ms(value: object) -> Optional[int]:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        if value < 1_000_000_000_000:
            return int(float(value) * 1000)
        return int(value)
    if isinstance(value, str):
        try:
            if value.endswith("Z"):
                value = value[:-1] + "+00:00"
            dt = datetime.fromisoformat(value)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return int(dt.timestamp() * 1000)
        except ValueError:
            return None
    return None


def _utc_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
