from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import ssl
from typing import Any, Callable, Dict, List, Optional
from urllib.request import Request, urlopen

try:  # pragma: no cover - optional dependency
    import certifi
except ModuleNotFoundError:  # pragma: no cover - optional dependency
    certifi = None

EventCallback = Callable[[Dict[str, Any]], Any]


class ReferenceAdapter:
    async def connect(self) -> None:
        return None

    async def run(self, callback: EventCallback) -> None:
        raise NotImplementedError

    def stop(self) -> None:
        return None


@dataclass(frozen=True)
class PollingAdapterConfig:
    symbols: List[str]
    poll_interval_secs: float
    base_url: str
    source: str
    venue: str


class PollingAdapter(ReferenceAdapter):
    def __init__(self, config: PollingAdapterConfig) -> None:
        self.config = config
        self._stop_event = asyncio.Event()

    def stop(self) -> None:
        self._stop_event.set()

    async def run(self, callback: EventCallback) -> None:
        while not self._stop_event.is_set():
            for symbol in self.config.symbols:
                record = await asyncio.to_thread(self._poll_symbol, symbol)
                if record is not None:
                    result = callback(record)
                    if asyncio.iscoroutine(result):
                        await result
            await asyncio.sleep(self.config.poll_interval_secs)

    def _poll_symbol(self, symbol: str) -> Optional[Dict[str, Any]]:
        raise NotImplementedError

    def _event_record(
        self,
        symbol: str,
        value: float,
        t_event_ms: Optional[int],
        raw: Dict[str, Any],
        parse_warnings: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        t_recv_wall_iso = _utc_iso()
        t_recv_wall_ms = _wall_ms()
        t_recv_mono_ns = _monotonic_ns()
        warnings = parse_warnings or []
        return {
            "channel": "reference",
            "event_type": "reference_tick",
            "market": symbol,
            "asset_id": None,
            "t_event_ms": t_event_ms,
            "t_recv_wall_ms": t_recv_wall_ms,
            "raw": raw,
            "parse_warnings": warnings,
            "out_of_order": False,
            "t_recv_wall_iso": t_recv_wall_iso,
            "t_recv_mono_ns": t_recv_mono_ns,
        }


class CoinbasePollingAdapter(PollingAdapter):
    def _poll_symbol(self, symbol: str) -> Optional[Dict[str, Any]]:
        product = f"{symbol.upper()}-USD"
        url = f"{self.config.base_url.rstrip('/')}/products/{product}/ticker"
        data = _fetch_json(url)
        price = _parse_float(data.get("price"))
        bid = _parse_float(data.get("bid"))
        ask = _parse_float(data.get("ask"))
        if price is None:
            price = _mid(bid, ask)
        if price is None:
            return None
        t_event_ms = _parse_iso_ms(data.get("time"))
        parse_warnings: List[str] = []
        if t_event_ms is None:
            parse_warnings.append("MISSING_EVENT_TS")
        raw = {
            "source": self.config.source,
            "venue": self.config.venue,
            "symbol": symbol,
            "value": price,
            "bid": bid,
            "ask": ask,
            "t_event_ms": t_event_ms,
        }
        return self._event_record(symbol, price, t_event_ms, raw, parse_warnings)


class KrakenPollingAdapter(PollingAdapter):
    def _poll_symbol(self, symbol: str) -> Optional[Dict[str, Any]]:
        pair = _kraken_pair(symbol)
        url = f"{self.config.base_url.rstrip('/')}/0/public/Ticker?pair={pair}"
        data = _fetch_json(url)
        result = data.get("result", {})
        if not isinstance(result, dict) or not result:
            return None
        entry = next(iter(result.values()))
        if not isinstance(entry, dict):
            return None
        last = _parse_float((entry.get("c") or [None])[0])
        bid = _parse_float((entry.get("b") or [None])[0])
        ask = _parse_float((entry.get("a") or [None])[0])
        price = last if last is not None else _mid(bid, ask)
        if price is None:
            return None
        t_event_ms = None
        parse_warnings = ["MISSING_EVENT_TS"]
        raw = {
            "source": self.config.source,
            "venue": self.config.venue,
            "symbol": symbol,
            "value": price,
            "bid": bid,
            "ask": ask,
            "t_event_ms": t_event_ms,
        }
        return self._event_record(symbol, price, t_event_ms, raw, parse_warnings)


def _fetch_json(url: str) -> Dict[str, Any]:
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
        "Accept": "application/json,text/plain,*/*",
        "Accept-Language": "en-US,en;q=0.9",
        "Connection": "keep-alive",
    }
    req = Request(url, headers=headers, method="GET")
    context = _ssl_context()
    with urlopen(req, timeout=10, context=context) as resp:
        raw = resp.read().decode("utf-8")
    return json.loads(raw)


def _parse_float(value: object) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _mid(bid: Optional[float], ask: Optional[float]) -> Optional[float]:
    if bid is None or ask is None:
        return None
    return (bid + ask) / 2.0


def _parse_iso_ms(value: object) -> Optional[int]:
    if not isinstance(value, str):
        return None
    try:
        if value.endswith("Z"):
            value = value[:-1] + "+00:00"
        dt = datetime.fromisoformat(value)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return int(dt.timestamp() * 1000)
    except ValueError:
        return None


def _kraken_pair(symbol: str) -> str:
    upper = symbol.upper()
    if upper == "BTC":
        return "XBTUSD"
    return f"{upper}USD"


def _utc_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def _monotonic_ns() -> int:
    import time

    return time.monotonic_ns()


def _ssl_context() -> Optional[ssl.SSLContext]:
    if certifi is None:
        return None
    try:
        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return None


def _wall_ms() -> int:
    return int(datetime.now(timezone.utc).timestamp() * 1000)
