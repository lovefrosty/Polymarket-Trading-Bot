from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import ssl
import time
from typing import Any, Callable, Dict, List, Optional, Tuple
from urllib.request import Request, urlopen

try:
    import websockets
except ModuleNotFoundError:  # pragma: no cover - optional dependency
    websockets = None
try:  # pragma: no cover - optional dependency
    import certifi
except ModuleNotFoundError:  # pragma: no cover - optional dependency
    certifi = None

from core.event_tape import EventTape
from core.reference_price import ReferenceQuote, parse_reference_event
from core.reference_store import ReferenceStore


KRAKEN_WS_URL = "wss://ws.kraken.com"
KRAKEN_FUTURES_WS_URL = "wss://futures.kraken.com/ws/v1"
KRAKEN_FUTURES_INSTRUMENTS_URL = "https://futures.kraken.com/derivatives/api/v3/instruments"



@dataclass(frozen=True)
class ReferenceWSConfig:
    venue: str
    symbols: List[str]
    ping_interval_secs: float = 20.0
    ping_timeout_secs: float = 20.0
    reconnect_base_secs: float = 1.0
    reconnect_max_secs: float = 15.0
    inactivity_timeout_secs: float = 10.0


class ReferenceWSClient:
    def __init__(
        self,
        tape: EventTape,
        config: ReferenceWSConfig,
        on_quote: Optional[Callable[[ReferenceQuote], None]] = None,
        reference_store: Optional[ReferenceStore] = None,
    ) -> None:
        self.tape = tape
        self.config = config
        self._on_quote = on_quote
        self._reference_store = reference_store
        self._stop_event = asyncio.Event()
        self._last_recv_mono_ns: Dict[str, int] = {}
        self._kraken_futures_product_ids: Optional[List[str]] = None

    def stop(self) -> None:
        self._stop_event.set()

    async def run(self) -> None:
        if websockets is None:
            raise RuntimeError("websockets_not_installed")
        if self.config.venue not in {"kraken", "kraken_futures"}:
            raise ValueError(f"unsupported_reference_venue:{self.config.venue}")
        if self.config.venue == "kraken_futures":
            await self._run_kraken_futures()
            return
        await self._run_single_session()

    async def _run_single_session(self) -> None:
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

    async def _run_kraken_futures(self) -> None:
        ssl_context = _ssl_context()
        attempt = 0
        backoff_secs = max(float(self.config.reconnect_base_secs), 0.1)
        while not self._stop_event.is_set():
            attempt += 1
            try:
                async with websockets.connect(
                    KRAKEN_FUTURES_WS_URL,
                    ping_interval=self.config.ping_interval_secs,
                    ping_timeout=self.config.ping_timeout_secs,
                    ssl=ssl_context,
                ) as ws:
                    await self._subscribe(ws)
                    if attempt > 1:
                        self._emit_status("reconnected", attempt)
                    while not self._stop_event.is_set():
                        try:
                            raw = await asyncio.wait_for(
                                ws.recv(), timeout=self.config.inactivity_timeout_secs
                            )
                        except asyncio.TimeoutError as exc:
                            self._emit_status("error", attempt, exc)
                            self._emit_status("disconnected", attempt, exc)
                            break
                        recv_mono_ns = time.monotonic_ns()
                        recv_wall_ms = int(time.time() * 1000)
                        recv_wall_iso = _utc_iso()
                        await self._handle_message(raw, recv_mono_ns, recv_wall_ms, recv_wall_iso)
                if self._stop_event.is_set():
                    break
                self._emit_status("reconnecting", attempt)
            except Exception as exc:
                self._emit_status("error", attempt, exc)
                self._emit_status("disconnected", attempt, exc)
                if self._stop_event.is_set():
                    break
                self._emit_status("reconnecting", attempt, exc)
            if self._stop_event.is_set():
                break
            await asyncio.sleep(backoff_secs)
            backoff_secs = min(backoff_secs * 2.0, float(self.config.reconnect_max_secs))

    async def _subscribe(self, ws) -> None:
        if self.config.venue == "kraken_futures":
            product_ids = await self._get_kraken_futures_product_ids()
            payload = {"event": "subscribe", "feed": "ticker", "product_ids": product_ids}
        else:
            symbols = [_kraken_symbol(symbol) for symbol in self.config.symbols]
            payload = {"event": "subscribe", "pair": symbols, "subscription": {"name": "ticker"}}
        try:
            await ws.send(json.dumps(payload))
        except Exception:
            if self.config.venue == "kraken_futures":
                product_ids = await self._get_kraken_futures_product_ids(force_refresh=True)
                payload = {"event": "subscribe", "feed": "ticker", "product_ids": product_ids}
                await ws.send(json.dumps(payload))
            else:
                raise

    async def _get_kraken_futures_product_ids(self, force_refresh: bool = False) -> List[str]:
        if force_refresh or self._kraken_futures_product_ids is None:
            self._kraken_futures_product_ids = await asyncio.to_thread(
                _resolve_kraken_futures_product_ids, self.config.symbols
            )
        return list(self._kraken_futures_product_ids)

    async def _handle_message(
        self, raw: str, recv_mono_ns: int, recv_wall_ms: int, recv_wall_iso: str
    ) -> None:
        try:
            msg = json.loads(raw)
        except json.JSONDecodeError:
            return
        
        
        updates = (
            _parse_kraken_futures_message(msg)
            if self.config.venue == "kraken_futures"
            else _parse_kraken_message(msg)
        )
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
            "source": str(update.get("source") or "spot"),
            "venue": str(update.get("venue") or "kraken_spot"),
            "symbol": symbol,
            "bid": bid,
            "ask": ask,
            "mid": mid,
            "value": mid,
            "t_event_ms": t_event_ms,
            "raw_payload": raw_payload,
        }

        record = {
            "channel": "reference",
            "event_type": "reference_tick",
            "market": symbol,
            "asset_id": None,
            "t_event_ms": t_event_ms,
            "t_recv_wall_ms": recv_wall_ms,
            "t_recv_wall_iso": recv_wall_iso,
            "t_recv_mono_ns": recv_mono_ns,
            "raw": raw,
            "parse_warnings": parse_warnings,
            "out_of_order": out_of_order,
        }
        self.tape.write(
            channel=record["channel"],
            event_type=record["event_type"],
            market=record["market"],
            asset_id=record["asset_id"],
            t_event_ms=record["t_event_ms"],
            raw=record["raw"],
            parse_warnings=record["parse_warnings"],
            out_of_order=record["out_of_order"],
            t_recv_wall_iso=record["t_recv_wall_iso"],
            t_recv_wall_ms=record["t_recv_wall_ms"],
            t_recv_mono_ns=record["t_recv_mono_ns"],
        )

        if self._reference_store is not None:
            self._reference_store.ingest_record(record)

        if self._on_quote is not None:
            quote = parse_reference_event(
                record.get("raw"),
                record.get("t_recv_mono_ns"),
                record.get("t_recv_wall_iso"),
                record.get("t_recv_wall_ms"),
            )
            if quote is not None:
                self._on_quote(quote)

    def _emit_status(self, status: str, attempt: int, exc: Optional[BaseException] = None) -> None:
        venue = "kraken_futures_perp" if self.config.venue == "kraken_futures" else "kraken_spot"
        detail = ""
        if isinstance(exc, asyncio.TimeoutError):
            detail = "inactivity_timeout"
        elif exc is not None:
            detail = str(exc)
        self.tape.write(
            channel="reference",
            event_type="reference_ws_status",
            market=self.config.symbols[0] if self.config.symbols else None,
            asset_id=None,
            t_event_ms=int(time.time() * 1000),
            raw={
                "venue": venue,
                "status": status,
                "attempt": attempt,
                "error_class": exc.__class__.__name__ if exc is not None else None,
                "error_detail": detail or None,
            },
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


def _parse_kraken_futures_message(msg: Any) -> List[Dict[str, Any]]:
    if not isinstance(msg, dict):
        return []
    if msg.get("feed") != "ticker":
        return []
    product_id = msg.get("product_id")
    if not isinstance(product_id, str) or not product_id:
        return []
    bid = _parse_float(msg.get("bid"))
    ask = _parse_float(msg.get("ask"))
    mark = _parse_float(msg.get("markPrice"))
    last = _parse_float(msg.get("last"))
    mid = _mid(bid, ask)
    value = mark if mark is not None else mid if mid is not None else last
    if value is None:
        return []
    return [
        {
            "symbol": _normalize_kraken_futures_symbol(msg.get("pair"), product_id),
            "bid": bid,
            "ask": ask,
            "mid": value,
            "t_event_ms": _parse_ts_ms(msg.get("time")),
            "source": "perp",
            "venue": "kraken_futures_perp",
        }
    ]


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


def _normalize_kraken_futures_symbol(pair: object, product_id: str) -> str:
    if isinstance(pair, str) and pair:
        normalized = pair.replace(":", "").replace("/", "")
        if normalized.upper().startswith("XBT"):
            return "BTC"
        if normalized.upper().endswith("USD"):
            return normalized[:-3]
    token = str(product_id).upper()
    if "XBT" in token or "BTC" in token:
        return "BTC"
    if "ETH" in token:
        return "ETH"
    if "SOL" in token:
        return "SOL"
    if "XRP" in token:
        return "XRP"
    return token


def _resolve_kraken_futures_product_ids(symbols: List[str]) -> List[str]:
    data = _fetch_json(KRAKEN_FUTURES_INSTRUMENTS_URL)
    instruments = data.get("instruments")
    if not isinstance(instruments, list):
        raise RuntimeError("kraken_futures_instruments_missing")
    resolved: List[str] = []
    for symbol in symbols:
        product_id = _select_kraken_futures_product_id(symbol, instruments)
        if product_id is None:
            raise RuntimeError(f"kraken_futures_no_active_perpetual:{symbol}")
        resolved.append(product_id)
    return resolved


def _select_kraken_futures_product_id(symbol: str, instruments: List[Dict[str, Any]]) -> Optional[str]:
    candidates = []
    for entry in instruments:
        if not isinstance(entry, dict):
            continue
        product_id = entry.get("symbol")
        if not isinstance(product_id, str) or not product_id:
            continue
        if not bool(entry.get("tradeable", False)):
            continue
        if entry.get("lastTradingTime") not in (None, 0, ""):
            continue
        candidates.append(product_id)
    preferred = _kraken_futures_symbol_candidates(symbol)
    for product_id in preferred:
        if product_id in candidates:
            return product_id
    return None


def _kraken_futures_symbol_candidates(symbol: str) -> List[str]:
    upper = symbol.upper()
    bases = ["XBT", upper] if upper == "BTC" else [upper]
    preferred: List[str] = []
    for base in bases:
        preferred.extend([f"PI_{base}USD", f"PF_{base}USD"])
    return preferred


def _parse_float(value: object) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _fetch_json(url: str) -> Dict[str, Any]:
    headers = {
        "User-Agent": "Codex Kraken Futures Adapter/1.0",
        "Accept": "application/json,text/plain,*/*",
        "Connection": "keep-alive",
    }
    req = Request(url, headers=headers, method="GET")
    with urlopen(req, timeout=10, context=_ssl_context()) as resp:
        raw = resp.read().decode("utf-8")
    return json.loads(raw)


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
