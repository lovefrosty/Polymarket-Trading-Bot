from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from core.entry_exit_rules import PositionState


@dataclass(frozen=True)
class HedgeParams:
    edge_min: float
    tox_max: float
    h_min: float
    h_max: float
    hedge_required_vol_pct: float
    mapping_error_scale: float = 1.0
    latency_scale_ms: float = 1000.0
    illiquidity_scale: float = 1.0
    confidence_scale: float = 1.0


def hedge_policy(
    position_state: Optional[PositionState],
    decision_snapshot: Dict[str, Any],
    params: HedgeParams,
) -> Dict[str, Any]:
    if position_state is None:
        return {
            "hedge_ratio_target": 0.0,
            "target_hedge_notional": 0.0,
            "blockers": ["NO_POSITION"],
        }

    signals = decision_snapshot.get("notes", {}).get("signals", {}) or decision_snapshot.get("signals", {}) or {}
    edge_net = signals.get("edge_net")
    edge_strength = None
    if edge_net is not None and params.edge_min > 0:
        edge_strength = abs(float(edge_net)) / params.edge_min

    vol_regime = signals.get("vol_regime")
    vol_pct = None
    if vol_regime is not None:
        vol_pct = float(vol_regime) * 100.0

    tox_10s = signals.get("tox_10s")
    tox_val = None
    if tox_10s is not None:
        tox_val = abs(float(tox_10s))

    hedge_ratio = 0.5
    if vol_pct is not None and vol_pct >= params.hedge_required_vol_pct:
        hedge_ratio = params.h_max
    elif tox_val is not None and tox_val >= params.tox_max:
        hedge_ratio = params.h_max
    elif edge_strength is not None and edge_strength >= 1.0:
        if vol_pct is None or vol_pct < params.hedge_required_vol_pct:
            if tox_val is None or tox_val < params.tox_max:
                hedge_ratio = params.h_min

    confidence = decision_snapshot.get("confidence")
    mapping_error = decision_snapshot.get("mapping_error")
    illiquidity = decision_snapshot.get("illiquidity")
    latency_ms = decision_snapshot.get("latency_ms")

    if confidence is not None:
        hedge_ratio -= min(1.0, max(0.0, float(confidence) / params.confidence_scale))
    if mapping_error is not None:
        hedge_ratio += min(1.0, max(0.0, float(mapping_error) / params.mapping_error_scale))
    if illiquidity is not None:
        hedge_ratio += min(1.0, max(0.0, float(illiquidity) / params.illiquidity_scale))
    if latency_ms is not None and params.latency_scale_ms > 0:
        hedge_ratio += min(1.0, max(0.0, float(latency_ms) / params.latency_scale_ms))

    hedge_ratio = max(params.h_min, min(params.h_max, hedge_ratio))
    blockers: List[str] = []

    if hedge_ratio < 0.0 or hedge_ratio > 1.0:
        blockers.append("HEDGE_RATIO_OUT_OF_BOUNDS")

    if position_state.notional is None:
        blockers.append("NOTIONAL_MISSING")
    target_notional = 0.0
    if position_state.notional is not None:
        target_notional = abs(float(position_state.notional)) * hedge_ratio
        if position_state.side == "buy":
            target_notional = -target_notional

    hedge_capacity = decision_snapshot.get("hedge_capacity")
    if hedge_capacity is not None and abs(target_notional) > float(hedge_capacity):
        blockers.append("HEDGE_NOT_FEASIBLE")

    if blockers:
        return {
            "hedge_ratio_target": 0.0,
            "target_hedge_notional": 0.0,
            "blockers": blockers,
        }

    return {
        "hedge_ratio_target": hedge_ratio,
        "target_hedge_notional": target_notional,
        "blockers": [],
    }
