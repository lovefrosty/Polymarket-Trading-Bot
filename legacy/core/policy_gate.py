from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional

from core.book_cache import BookHealthState, BookSnapshot
from core.pstar import PStar


@dataclass(frozen=True)
class PolicyThresholds:
    max_book_age_ms: int = 2000
    book_stale_after_ms: int = 30_000
    book_down_after_ms: int = 120_000
    max_spread_bps: float = 150.0
    max_slippage_bps: float = 120.0
    min_depth_at_qty: float = 1.0
    max_signal_age_ms: int = 1200
    max_ack_p95_ms: float = 400.0
    max_ws_lag_ms: float = 1000.0
    hedge_timeout_ms: int = 5000


@dataclass(frozen=True)
class PolicyContext:
    market: str
    token_id: str
    now_ms: int
    decision_ts_event_ms: int
    feature_max_ts_ms: int
    book: Optional[BookSnapshot]
    pstar: Optional[PStar]
    quote_side: str
    quote_qty: float
    signal_age_ms: int
    ack_p95_ms: Optional[float]
    ws_lag_ms: Optional[float]
    one_leg_age_ms: Optional[int]
    fsm_state: str
    expected_slippage_bps: Optional[float]
    depth_at_qty: Optional[float]
    book_health_state: Optional[str] = None
    reconciliation_mismatch_critical: bool = False
    risk_throttle_critical: bool = False
    liveness_critical: bool = False
    unknown_order_quarantine: bool = False
    daily_loss_critical: bool = False


@dataclass(frozen=True)
class PolicyVerdict:
    allow: bool
    action: str
    reason_codes: List[str]
    diagnostics: Dict[str, object]


def evaluate_policy(ctx: PolicyContext, thresholds: PolicyThresholds) -> PolicyVerdict:
    reasons: List[str] = []
    diagnostics: Dict[str, object] = {
        "token_id": ctx.token_id,
        "market": ctx.market,
        "fsm_state": ctx.fsm_state,
    }

    # A) P* validity.
    if ctx.pstar is None or not ctx.pstar.valid or ctx.pstar.value is None:
        reasons.append("A_PSTAR_INVALID")
    else:
        diagnostics["pstar_confidence"] = ctx.pstar.confidence
        diagnostics["pstar_diag"] = ctx.pstar.diagnostics

    # B) Event-time causality.
    if ctx.feature_max_ts_ms >= ctx.decision_ts_event_ms:
        reasons.append("B_FEATURE_TIME_LEAK")
    if ctx.book is not None and ctx.book.ts_event_ms is not None and ctx.book.ts_event_ms >= ctx.decision_ts_event_ms:
        reasons.append("B_BOOK_TIME_LEAK")
    if ctx.pstar is not None and ctx.pstar.ts_event_ms is not None and ctx.pstar.ts_event_ms >= ctx.decision_ts_event_ms:
        reasons.append("B_PSTAR_TIME_LEAK")

    # C) Book depth/spread/slippage.
    if ctx.book is None:
        reasons.append("C_BOOK_MISSING")
        diagnostics["book_health_state"] = BookHealthState.DOWN.value
    else:
        health_state = (
            str(ctx.book_health_state)
            if ctx.book_health_state is not None
            else ctx.book.health_state(
                now_wall_ms=ctx.now_ms,
                stale_after_ms=thresholds.book_stale_after_ms,
                down_after_ms=thresholds.book_down_after_ms,
            ).value
        )
        diagnostics["book_health_state"] = health_state
        if health_state == BookHealthState.DOWN.value:
            reasons.append("C_BOOK_DOWN")
        elif health_state == BookHealthState.STALE.value:
            reasons.append("C_BOOK_STALE_STATE")
        if not ctx.book.is_fresh(ctx.now_ms, thresholds.max_book_age_ms):
            reasons.append("C_BOOK_STALE")
        spread_bps = ctx.book.spread_bps()
        diagnostics["spread_bps"] = spread_bps
        if spread_bps is None:
            reasons.append("C_BOOK_EMPTY")
        elif spread_bps > thresholds.max_spread_bps:
            reasons.append("C_SPREAD_TOO_WIDE")

    depth_at_qty = ctx.depth_at_qty if ctx.depth_at_qty is not None else 0.0
    diagnostics["depth_at_qty"] = depth_at_qty
    if depth_at_qty < thresholds.min_depth_at_qty:
        reasons.append("C_DEPTH_THIN")

    if ctx.expected_slippage_bps is None:
        reasons.append("C_SLIPPAGE_UNKNOWN")
    elif ctx.expected_slippage_bps > thresholds.max_slippage_bps:
        reasons.append("C_SLIPPAGE_HIGH")
    diagnostics["expected_slippage_bps"] = ctx.expected_slippage_bps

    # D) One-leg risk state.
    if ctx.fsm_state in {"ONE_SIDE_FILLED", "REBALANCING", "UNWINDING"}:
        reasons.append("D_ONE_LEG_RISK_ACTIVE")
        if ctx.one_leg_age_ms is not None and ctx.one_leg_age_ms > thresholds.hedge_timeout_ms:
            reasons.append("D_HEDGE_TIMEOUT")
    diagnostics["one_leg_age_ms"] = ctx.one_leg_age_ms

    # E) Latency.
    if ctx.signal_age_ms > thresholds.max_signal_age_ms:
        reasons.append("E_SIGNAL_AGE_HIGH")
    if ctx.ack_p95_ms is not None and ctx.ack_p95_ms > thresholds.max_ack_p95_ms:
        reasons.append("E_ACK_P95_HIGH")
    if ctx.ws_lag_ms is not None and ctx.ws_lag_ms > thresholds.max_ws_lag_ms:
        reasons.append("E_WS_LAG_HIGH")
    diagnostics["signal_age_ms"] = ctx.signal_age_ms
    diagnostics["ack_p95_ms"] = ctx.ack_p95_ms
    diagnostics["ws_lag_ms"] = ctx.ws_lag_ms

    if bool(ctx.reconciliation_mismatch_critical):
        reasons.append("RECONCILIATION_MISMATCH_CRITICAL")
        diagnostics["reconciliation_mismatch_critical"] = True
    if bool(ctx.risk_throttle_critical):
        reasons.append("RISK_THROTTLE_CRITICAL")
        diagnostics["risk_throttle_critical"] = True
    if bool(ctx.daily_loss_critical):
        reasons.append("RISK_DAILY_LOSS_KILLSWITCH")
        diagnostics["daily_loss_critical"] = True
    if bool(ctx.liveness_critical):
        reasons.append("E_LIVENESS_CRITICAL")
        diagnostics["liveness_critical"] = True
    if bool(ctx.unknown_order_quarantine):
        reasons.append("RECON_UNKNOWN_ORDER_QUARANTINE")
        diagnostics["unknown_order_quarantine"] = True

    if reasons:
        critical = any(
            code.startswith("A_")
            or code.startswith("B_")
            or code in {"D_HEDGE_TIMEOUT", "C_BOOK_DOWN"}
            or code
            in {
                "RECONCILIATION_MISMATCH_CRITICAL",
                "RISK_THROTTLE_CRITICAL",
                "RISK_DAILY_LOSS_KILLSWITCH",
                "E_LIVENESS_CRITICAL",
                "RECON_UNKNOWN_ORDER_QUARANTINE",
            }
            for code in reasons
        )
        return PolicyVerdict(
            allow=False,
            action="FREEZE" if critical else "SKIP",
            reason_codes=sorted(set(reasons)),
            diagnostics=diagnostics,
        )
    return PolicyVerdict(
        allow=True,
        action="QUOTE",
        reason_codes=[],
        diagnostics=diagnostics,
    )
