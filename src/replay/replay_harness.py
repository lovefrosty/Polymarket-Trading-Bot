"""
⚠️ COMPATIBILITY SHIM — NOT PART OF RUNTIME SYSTEM ⚠️

This module exists solely to satisfy legacy test imports.
It must never be used in live trading, discovery, or replay logic.

If this module is invoked in production paths, that is a BUG.
"""

from __future__ import annotations

import os

if os.getenv("RUNTIME_MODE") == "live":
    raise RuntimeError("Legacy compatibility module imported in live mode")

from dataclasses import dataclass
from typing import List, Optional

from src.book.order_book import OrderBook
from src.data.reference_price import ReferencePriceResult
from src.execution.execution_engine import TradeIntent
from src.execution.state_machine import BrokerState, HedgeStateMachine
from src.features.feature_vector import FeatureVector
from src.risk.gates import (
    LatencyMetrics,
    LatencyThresholds,
    gate_book,
    gate_event_time,
    gate_hedge_state,
    gate_latency,
    gate_reference_price,
)


@dataclass(frozen=True)
class ReplayResult:
    allowed: bool
    violations: List[str]


class ReplayHarness:
    def __init__(
        self,
        book: OrderBook,
        book_max_age_ms: int,
        max_spread_bps: float,
        max_slippage_bps: float,
        latency_thresholds: LatencyThresholds,
    ) -> None:
        self.book = book
        self.book_max_age_ms = book_max_age_ms
        self.max_spread_bps = max_spread_bps
        self.max_slippage_bps = max_slippage_bps
        self.latency_thresholds = latency_thresholds

    def evaluate(
        self,
        decision_ts: int,
        intent: TradeIntent,
        reference_result: ReferencePriceResult,
        feature_vector: FeatureVector,
        latency_metrics: Optional[LatencyMetrics],
        hedge_state_machine: HedgeStateMachine,
        broker_state: BrokerState,
    ) -> ReplayResult:
        violations: List[str] = []

        ref_gate = gate_reference_price(reference_result)
        if not ref_gate.allowed:
            violations.append(ref_gate.reason or "reference_price")

        event_gate = gate_event_time(feature_vector, decision_ts)
        if not event_gate.allowed:
            violations.append(event_gate.reason or "event_time")

        book_gate = gate_book(
            self.book,
            decision_ts,
            intent.side,
            intent.qty,
            self.book_max_age_ms,
            self.max_spread_bps,
            self.max_slippage_bps,
        )
        if not book_gate.allowed:
            violations.append(book_gate.reason or "book")

        hedge_gate = gate_hedge_state(hedge_state_machine, broker_state)
        if not hedge_gate.allowed:
            violations.append(hedge_gate.reason or "hedge_state")

        if latency_metrics is None:
            violations.append("missing_latency")
        else:
            latency_gate = gate_latency(latency_metrics, self.latency_thresholds)
            if not latency_gate.allowed:
                violations.append(latency_gate.reason or "latency")

        return ReplayResult(allowed=not violations, violations=violations)
