from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Protocol, Any

from src.book.order_book import OrderBook
from src.data.reference_price import ReferencePriceResult
from src.features.feature_vector import FeatureVector
from src.execution.state_machine import BrokerState, HedgeStateMachine
from src.risk.gates import (
    GateResult,
    LatencyMetrics,
    LatencyThresholds,
    gate_book,
    gate_event_time,
    gate_hedge_state,
    gate_latency,
    gate_reference_price,
)


@dataclass(frozen=True)
class TradeIntent:
    side: str
    qty: float
    market_price: float


@dataclass(frozen=True)
class ExecutionDecision:
    allow: bool
    reasons: List[str]


class Broker(Protocol):
    def send(self, payload: Any) -> Any:
        ...


@dataclass(frozen=True)
class OrderIntent:
    broker: Broker
    payload: Any


class ExecutionEngine:
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
    ) -> ExecutionDecision:
        reasons: List[str] = []

        ref_gate = gate_reference_price(reference_result)
        if not ref_gate.allowed:
            reasons.append(ref_gate.reason or "reference_price")

        event_gate = gate_event_time(feature_vector, decision_ts)
        if not event_gate.allowed:
            reasons.append(event_gate.reason or "event_time")

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
            reasons.append(book_gate.reason or "book")

        hedge_gate = gate_hedge_state(hedge_state_machine, broker_state)
        if not hedge_gate.allowed:
            reasons.append(hedge_gate.reason or "hedge_state")

        if latency_metrics is None:
            reasons.append("missing_latency")
        else:
            latency_gate = gate_latency(latency_metrics, self.latency_thresholds)
            if not latency_gate.allowed:
                reasons.append(latency_gate.reason or "latency")

        return ExecutionDecision(allow=not reasons, reasons=reasons)


def submit_order(decision: ExecutionDecision, order: OrderIntent) -> Any:
    if not decision.allow:
        raise ValueError(f"order_submission_blocked:{','.join(decision.reasons)}")
    return order.broker.send(order.payload)
