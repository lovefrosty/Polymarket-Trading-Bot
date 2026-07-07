from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

from core.event_tape import EventTape
from core.reference_adapters import (
    BinancePerpPollingAdapter,
    CoinbasePollingAdapter,
    KrakenPollingAdapter,
    PollingAdapterConfig,
    ReferenceAdapter,
)
from core.reference_price import ReferencePriceAggregator, parse_reference_event
from core.reference_store import ReferenceStore


EventCallback = Callable[[Dict[str, Any]], Any]


@dataclass(frozen=True)
class ReferenceFeedConfig:
    symbols: List[str]
    poll_interval_secs: float
    source: str
    coinbase_base: str = "https://api.exchange.coinbase.com"
    kraken_base: str = "https://api.kraken.com"
    binance_futures_base: str = "https://fapi.binance.com"


class ReferenceFeed:
    def __init__(
        self,
        aggregator: Optional[ReferencePriceAggregator],
        tape: EventTape,
        config: ReferenceFeedConfig,
        on_quote: Optional[Callable[[object], None]] = None,
        reference_store: Optional[ReferenceStore] = None,
    ) -> None:
        self.aggregator = aggregator
        self.tape = tape
        self.config = config
        self._adapter = _build_adapter(config)
        self._on_quote = on_quote
        self._reference_store = reference_store
        self._poll_failures_by_symbol: Dict[str, int] = {str(symbol): 0 for symbol in config.symbols}

    def stop(self) -> None:
        if self._adapter is not None:
            self._adapter.stop()

    async def run(self) -> None:
        if self._adapter is None:
            return
        if self.config.source.startswith("poll_") and hasattr(self._adapter, "_poll_symbol") and hasattr(self._adapter, "_stop_event"):
            await self._run_polling_adapter()
            return
        await self._adapter.run(self._handle_event)

    async def _run_polling_adapter(self) -> None:
        adapter = self._adapter
        if adapter is None:
            return
        while not adapter._stop_event.is_set():  # type: ignore[attr-defined]
            for symbol in self.config.symbols:
                try:
                    record = await asyncio.to_thread(adapter._poll_symbol, symbol)  # type: ignore[attr-defined]
                except Exception as exc:
                    failures = int(self._poll_failures_by_symbol.get(symbol, 0)) + 1
                    self._poll_failures_by_symbol[str(symbol)] = failures
                    self._emit_feed_status(
                        status="error",
                        symbol=str(symbol),
                        consecutive_failures=failures,
                        exc=exc,
                    )
                    continue
                failures = int(self._poll_failures_by_symbol.get(symbol, 0))
                if failures > 0:
                    self._emit_feed_status(
                        status="recovered",
                        symbol=str(symbol),
                        consecutive_failures=failures,
                    )
                    self._poll_failures_by_symbol[str(symbol)] = 0
                if record is not None:
                    self._handle_event(record)
            await asyncio.sleep(self.config.poll_interval_secs)

    def _handle_event(self, record: Dict[str, Any]) -> None:
        self.tape.write(
            channel=record.get("channel", "reference"),
            event_type=record.get("event_type", "reference_tick"),
            market=record.get("market"),
            asset_id=record.get("asset_id"),
            t_event_ms=record.get("t_event_ms"),
            raw=record.get("raw"),
            parse_warnings=record.get("parse_warnings"),
            out_of_order=record.get("out_of_order", False),
            t_recv_wall_iso=record.get("t_recv_wall_iso"),
            t_recv_wall_ms=record.get("t_recv_wall_ms"),
            t_recv_mono_ns=record.get("t_recv_mono_ns"),
        )

        if self._reference_store is not None:
            self._reference_store.ingest_record(record)

        quote = parse_reference_event(
            record.get("raw"),
            record.get("t_recv_mono_ns"),
            record.get("t_recv_wall_iso"),
            record.get("t_recv_wall_ms"),
        )
        if quote is None:
            return
        if self.aggregator is not None:
            self.aggregator.ingest(quote)
        if self._on_quote is not None:
            self._on_quote(quote)

    def _emit_feed_status(
        self,
        *,
        status: str,
        symbol: str,
        consecutive_failures: int,
        exc: Optional[BaseException] = None,
    ) -> None:
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        self.tape.write(
            channel="reference",
            event_type="reference_feed_status",
            market=symbol,
            asset_id=None,
            t_event_ms=now_ms,
            raw={
                "source": _source_kind(self.config.source),
                "venue": _source_venue(self.config),
                "status": status,
                "symbol": symbol,
                "consecutive_failures": int(consecutive_failures),
                "error_class": exc.__class__.__name__ if exc is not None else None,
                "error_detail": str(exc) if exc is not None else None,
            },
        )


def _build_adapter(config: ReferenceFeedConfig) -> Optional[ReferenceAdapter]:
    source = (config.source or "none").lower()
    if source == "none":
        return None
    if source == "poll_coinbase":
        return CoinbasePollingAdapter(
            PollingAdapterConfig(
                symbols=config.symbols,
                poll_interval_secs=config.poll_interval_secs,
                base_url=config.coinbase_base,
                source="spot",
                venue="coinbase_spot",
            )
        )
    if source == "poll_kraken":
        return KrakenPollingAdapter(
            PollingAdapterConfig(
                symbols=config.symbols,
                poll_interval_secs=config.poll_interval_secs,
                base_url=config.kraken_base,
                source="spot",
                venue="kraken_spot",
            )
        )
    if source == "poll_binance_perp":
        return BinancePerpPollingAdapter(
            PollingAdapterConfig(
                symbols=config.symbols,
                poll_interval_secs=config.poll_interval_secs,
                base_url=config.binance_futures_base,
                source="perp",
                venue="binance_perp",
            )
        )
    raise ValueError(f"unsupported_reference_source:{config.source}")


def _source_kind(source: str) -> str:
    lower = str(source or "none").lower()
    if lower == "poll_binance_perp":
        return "perp"
    return "spot"


def _source_venue(config: ReferenceFeedConfig) -> str:
    lower = str(config.source or "none").lower()
    if lower == "poll_coinbase":
        return "coinbase_spot"
    if lower == "poll_kraken":
        return "kraken_spot"
    if lower == "poll_binance_perp":
        return "binance_perp"
    return lower
