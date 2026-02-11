from __future__ import annotations

import asyncio
from dataclasses import dataclass
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

    def stop(self) -> None:
        if self._adapter is not None:
            self._adapter.stop()

    async def run(self) -> None:
        if self._adapter is None:
            return
        await self._adapter.run(self._handle_event)

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
