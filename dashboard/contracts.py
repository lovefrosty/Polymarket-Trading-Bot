from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence


@dataclass(frozen=True)
class DashboardFilters:
    lookback_rows: int
    window_minutes: int
    selected_market: str
    selected_token: str
    severity_filter: str
    positive_ev_only: bool
    allow_only: bool
    strategy_filter: str


@dataclass(frozen=True)
class RefreshPolicy:
    auto_refresh: bool
    topbar_refresh_ms: int
    heavy_every_ticks: int


@dataclass(frozen=True)
class TopBarMetrics:
    mode: str
    is_frozen: bool
    freeze_reasons: List[str]
    market_slug: str
    token_ids: List[str]
    time_to_window_end: str
    pstar_age_current_ms: Optional[float]
    pstar_age_p95_5m_ms: Optional[float]
    ws_lag_current_ms: Optional[float]
    ws_lag_p95_5m_ms: Optional[float]
    ack_p50_5m_ms: Optional[float]
    ack_p95_5m_ms: Optional[float]
    signal_age_p95_5m_ms: Optional[float]
    decisions_1h: int
    signals_1h: int
    cancels_1h: int
    replaces_1h: int
    fills_1h: int
    rejects_1h: int
    net_yes: float
    net_no: float
    net_usd_exposure: float
    hedge_completeness: float


@dataclass(frozen=True)
class HealthGateStatus:
    gate: str
    status: str
    summary: str
    details: Dict[str, Any]


@dataclass(frozen=True)
class PanelDependency:
    panel_id: str
    required_sources: Sequence[str] = field(default_factory=list)
    optional_sources: Sequence[str] = field(default_factory=list)


@dataclass(frozen=True)
class DrillthroughContext:
    context_id: str
    context_hash: str
    metric_key: str
    start_ts_ms: int
    end_ts_ms: int
    market: str
    token_id: str
    reason_codes: List[str]
    evidence_refs: List[str]
    payload: Dict[str, Any]


@dataclass(frozen=True)
class ReplayMismatchRow:
    decision_id: str
    action_live: str
    action_replay: str
    reasons_live: str
    reasons_replay: str
    p_exec_delta_bps: float
    evidence_refs: List[str]
