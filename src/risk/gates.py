from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from src.book.order_book import OrderBook
from src.data.reference_price import ReferencePriceResult
from src.execution.state_machine import BrokerState, HedgeStateMachine
from src.features.feature_vector import FeatureVector


@dataclass(frozen=True)
class GateResult:
    allowed: bool
    reason: Optional[str] = None


@dataclass(frozen=True)
class LatencyMetrics:
    signal_ts: int
    order_send_ts: int
    ack_ts: int
    fill_ts: int
    p95_ack_ms: float
    ws_lag_ms: float


@dataclass(frozen=True)
class LatencyThresholds:
    max_signal_age_ms: int
    max_p95_ack_ms: float
    max_ws_lag_ms: float


def gate_reference_price(reference_result: ReferencePriceResult) -> GateResult:
    if reference_result.price is None:
        return GateResult(False, reference_result.freeze_reason or "missing_reference_price")
    return GateResult(True, None)


def gate_event_time(feature_vector: FeatureVector, decision_ts: int) -> GateResult:
    if feature_vector.max_event_ts() >= decision_ts:
        return GateResult(False, "feature_event_ts_not_before_decision")
    return GateResult(True, None)


def gate_book(
    book: OrderBook,
    decision_ts: int,
    side: str,
    qty: float,
    max_age_ms: int,
    max_spread_bps: float,
    max_slippage_bps: float,
) -> GateResult:
    if book.is_stale(decision_ts, max_age_ms):
        return GateResult(False, "stale_book")
    if not book.has_book():
        return GateResult(False, "empty_book")

    metrics = book.depth_metrics(side, qty)
    if not metrics.depth_ok:
        return GateResult(False, "insufficient_depth")
    if metrics.effective_spread_bps > max_spread_bps:
        return GateResult(False, "spread_too_wide")
    if metrics.slippage_to_mid_bps > max_slippage_bps:
        return GateResult(False, "slippage_too_high")
    return GateResult(True, None)


def gate_latency(metrics: LatencyMetrics, thresholds: LatencyThresholds) -> GateResult:
    if not (metrics.signal_ts < metrics.order_send_ts < metrics.ack_ts < metrics.fill_ts):
        return GateResult(False, "latency_timestamp_ordering_invalid")
    signal_age = metrics.order_send_ts - metrics.signal_ts
    if signal_age > thresholds.max_signal_age_ms:
        return GateResult(False, "signal_age_exceeded")
    if metrics.p95_ack_ms > thresholds.max_p95_ack_ms:
        return GateResult(False, "ack_latency_exceeded")
    if metrics.ws_lag_ms > thresholds.max_ws_lag_ms:
        return GateResult(False, "ws_lag_exceeded")
    return GateResult(True, None)


def gate_hedge_state(machine: HedgeStateMachine, broker_state: BrokerState) -> GateResult:
    if machine.block_new_exposure(broker_state):
        return GateResult(False, "hedge_incomplete_or_unwinding")
    return GateResult(True, None)
