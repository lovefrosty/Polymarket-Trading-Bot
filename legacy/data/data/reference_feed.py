from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import time
from typing import Callable, Dict, Iterable, List, Optional
from urllib.request import urlopen

from core.event_tape import EventTape
from core.reference_price import ReferencePriceAggregator, ReferenceQuote


@dataclass(frozen=True)
class ReferenceFeedConfig:
    symbols: List[str]
    poll_interval_secs: float
    coinbase_base: str = "https://api.exchange.coinbase.com"
    bybit_base: str = "https://api.bybit.com"


class ReferenceFeed:
    def __init__(
        self,
        aggregator: ReferencePriceAggregator,
        tape: EventTape,
        config: ReferenceFeedConfig,
        on_quote: Optional[Callable[[ReferenceQuote], None]] = None,
    ) -> None:
        self.aggregator = aggregator
        self.tape = tape
        self.config = config
        self._on_quote = on_quote
        self._stop_event = asyncio.Event()

    def stop(self) -> None:
        self._stop_event.set()

    async def run(self) -> None:
        while not self._stop_event.is_set():
            for symbol in self.config.symbols:
                await self._poll_symbol(symbol)
            await asyncio.sleep(self.config.poll_interval_secs)

    async def _poll_symbol(self, symbol: str) -> None:
        loop = asyncio.get_running_loop()
        coinbase_task = loop.run_in_executor(None, _fetch_coinbase, self.config.coinbase_base, symbol)
        bybit_task = loop.run_in_executor(None, _fetch_bybit, self.config.bybit_base, symbol)
        results = await asyncio.gather(coinbase_task, bybit_task, return_exceptions=True)
        for result in results:
            if isinstance(result, Exception):
                continue
            if result is None:
                continue
            update = result
            self._ingest(update)

    def _ingest(self, update: ReferenceQuote) -> None:
        result = self.aggregator.ingest(update)
        parse_warnings: List[str] = []
        if result.recv_out_of_order:
            parse_warnings.append("recv_out_of_order")
        if result.event_time_regressed:
            parse_warnings.append("event_time_regressed")
        if result.symbol_mismatch:
            parse_warnings.append("symbol_mismatch")
        self.tape.write(
            channel="reference",
            event_type="reference_update",
            market=update.symbol,
            asset_id=None,
            t_event_ms=update.t_event_ms,
            raw={
                "source": update.source,
                "symbol": update.symbol,
                "value": update.value,
                "t_event_ms": update.t_event_ms,
            },
            parse_warnings=parse_warnings,
            out_of_order=result.recv_out_of_order,
            t_recv_wall_iso=update.t_recv_wall_iso,
            t_recv_mono_ns=update.t_recv_mono_ns,
        )
        if self._on_quote is not None:
            self._on_quote(update)


def _fetch_coinbase(base_url: str, symbol: str) -> Optional[ReferenceQuote]:
    product = f"{symbol.upper()}-USD"
    url = f"{base_url.rstrip('/')}/products/{product}/ticker"
    data = _fetch_json(url)
    price = _parse_float(data.get("price"))
    if price is None:
        return None
    t_event_ms = _parse_iso_ms(data.get("time"))
    return _build_quote("spot", symbol, price, t_event_ms)


def _fetch_bybit(base_url: str, symbol: str) -> Optional[ReferenceQuote]:
    pair = f"{symbol.upper()}USDT"
    url = f"{base_url.rstrip('/')}/v5/market/tickers?category=linear&symbol={pair}"
    data = _fetch_json(url)
    result = data.get("result", {})
    tickers = result.get("list", [])
    if not tickers:
        return None
    entry = tickers[0]
    price = _parse_float(entry.get("lastPrice"))
    if price is None:
        return None
    t_event_ms = _parse_int(entry.get("time"))
    if t_event_ms is not None and t_event_ms < 1_000_000_000_000:
        t_event_ms *= 1000
    return _build_quote("perp", symbol, price, t_event_ms)


def _build_quote(source: str, symbol: str, price: float, t_event_ms: Optional[int]) -> ReferenceQuote:
    return ReferenceQuote(
        source=source,
        symbol=symbol,
        value=price,
        t_event_ms=t_event_ms,
        t_recv_mono_ns=time.monotonic_ns(),
        t_recv_wall_iso=_utc_iso(),
        t_recv_wall_ms=_utc_ms(),
    )


def _fetch_json(url: str) -> Dict:
    with urlopen(url, timeout=10) as resp:
        raw = resp.read().decode("utf-8")
    return json.loads(raw)


def _parse_float(value: object) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _parse_int(value: object) -> Optional[int]:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


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


def _utc_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def _utc_ms() -> int:
    return int(datetime.now(timezone.utc).timestamp() * 1000)
