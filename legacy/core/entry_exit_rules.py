from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple


@dataclass(frozen=True)
class EntryExitParams:
    edge_min: float
    edge_exit: float
    edge_stop: float
    z_mom_min: float
    t_min_secs: float
    hold_max_secs: float
    vol_pct_hi: float
    edge_min_mult_hivol: float


@dataclass(frozen=True)
class PositionState:
    token_id: str
    outcome: Optional[str]
    side: str
    entry_mono_ns: int
    entry_edge: Optional[float]
    size: float
    notional: Optional[float]


def choose_best_action(
    outcome: Optional[str],
    token_id: Optional[str],
    p_fair: Optional[float],
    p_exec_buy: Optional[float],
    p_exec_sell: Optional[float],
) -> Dict[str, Any]:
    edge_buy = None
    edge_sell = None
    if p_fair is not None and p_exec_buy is not None:
        edge_buy = p_fair - p_exec_buy
    if p_fair is not None and p_exec_sell is not None:
        edge_sell = p_exec_sell - p_fair

    choice = {
        "side": None,
        "buy_or_sell": None,
        "outcome": outcome,
        "token_id": token_id,
        "edge_net": None,
        "p_exec": None,
    }

    if edge_buy is None and edge_sell is None:
        return choice

    if edge_sell is None:
        choice.update({"side": "buy", "buy_or_sell": "buy", "edge_net": edge_buy, "p_exec": p_exec_buy})
        return choice
    if edge_buy is None:
        choice.update({"side": "sell", "buy_or_sell": "sell", "edge_net": edge_sell, "p_exec": p_exec_sell})
        return choice

    if edge_buy >= edge_sell:
        choice.update({"side": "buy", "buy_or_sell": "buy", "edge_net": edge_buy, "p_exec": p_exec_buy})
    else:
        choice.update({"side": "sell", "buy_or_sell": "sell", "edge_net": edge_sell, "p_exec": p_exec_sell})
    return choice


def entry_gate(decision_snapshot: Dict[str, Any], params: EntryExitParams) -> Dict[str, Any]:
    reasons: List[str] = []

    gates = decision_snapshot.get("gates") or {}
    if not gates.get("allow", True):
        gate_reasons = gates.get("reasons") or []
        reasons.extend(str(reason) for reason in gate_reasons)
        reasons.append("HARD_GATES_FAIL")

    p_star = decision_snapshot.get("p_star") or {}
    if p_star.get("freeze_reason"):
        reasons.append("REF_FROZEN")

    signals = decision_snapshot.get("notes", {}).get("signals", {}) or decision_snapshot.get("signals", {}) or {}
    z_mom = signals.get("z_mom")
    if z_mom is None:
        reasons.append("Z_MOM_MISSING")
    elif abs(float(z_mom)) < params.z_mom_min:
        reasons.append("Z_TOO_WEAK")

    time_remaining = signals.get("time_remaining_sec")
    if time_remaining is None:
        reasons.append("TIME_UNKNOWN")
    elif float(time_remaining) < params.t_min_secs:
        reasons.append("TIME_TOO_SHORT")

    vol_regime = signals.get("vol_regime")
    edge_min_required = params.edge_min
    if vol_regime is not None:
        vol_pct = float(vol_regime) * 100.0
        if vol_pct >= params.vol_pct_hi:
            edge_min_required = params.edge_min * params.edge_min_mult_hivol

    p_fair = decision_snapshot.get("p_fair", signals.get("p_fair"))
    chosen_action = choose_best_action(
        outcome=decision_snapshot.get("outcome"),
        token_id=decision_snapshot.get("token_id"),
        p_fair=p_fair,
        p_exec_buy=decision_snapshot.get("p_market_exec_buy"),
        p_exec_sell=decision_snapshot.get("p_market_exec_sell"),
    )
    edge_override = decision_snapshot.get("edge_net_override") or {}
    if isinstance(edge_override, dict):
        side = chosen_action.get("side")
        if side == "buy" and edge_override.get("buy") is not None:
            chosen_action["edge_net"] = edge_override.get("buy")
        elif side == "sell" and edge_override.get("sell") is not None:
            chosen_action["edge_net"] = edge_override.get("sell")
    edge_net = chosen_action.get("edge_net")
    if edge_net is None:
        reasons.append("EDGE_MISSING")
    elif abs(float(edge_net)) < edge_min_required:
        reasons.append("EDGE_TOO_SMALL")

    alignment_reason = _alignment_check(
        decision_snapshot.get("outcome"), chosen_action.get("side"), z_mom
    )
    if alignment_reason is not None:
        reasons.append(alignment_reason)

    reasons = _dedupe(reasons)
    allow = not reasons
    return {
        "allow": allow,
        "reasons": reasons,
        "chosen_action": chosen_action,
        "edge_min_required": edge_min_required,
    }


def exit_gate(position_state: PositionState, decision_snapshot: Dict[str, Any], params: EntryExitParams) -> Dict[str, Any]:
    edge_now = _position_edge(position_state, decision_snapshot)
    t_mono_ns = int(decision_snapshot.get("t_decision_mono_ns", 0))
    held_secs = (t_mono_ns - position_state.entry_mono_ns) / 1_000_000_000.0

    if held_secs >= params.hold_max_secs:
        return {"should_exit": True, "reason": "TIME_STOP", "edge_now": edge_now}

    if edge_now is None:
        return {"should_exit": False, "reason": "EDGE_UNKNOWN", "edge_now": None}

    edge_mag = abs(float(edge_now))
    if edge_mag <= params.edge_exit:
        return {"should_exit": True, "reason": "EDGE_COLLAPSE", "edge_now": edge_now}

    if position_state.entry_edge is not None:
        if _sign(edge_now) != 0 and _sign(position_state.entry_edge) != 0:
            if _sign(edge_now) != _sign(position_state.entry_edge) and edge_mag >= params.edge_stop:
                return {"should_exit": True, "reason": "EDGE_STOP", "edge_now": edge_now}

    return {"should_exit": False, "reason": None, "edge_now": edge_now}


def _position_edge(position_state: PositionState, decision_snapshot: Dict[str, Any]) -> Optional[float]:
    p_fair = decision_snapshot.get("p_fair") or decision_snapshot.get("notes", {}).get("signals", {}).get("p_fair")
    if p_fair is None:
        return None
    if position_state.side == "buy":
        p_exec = decision_snapshot.get("p_market_exec_sell")
        if p_exec is None:
            return None
        return float(p_fair) - float(p_exec)
    if position_state.side == "sell":
        p_exec = decision_snapshot.get("p_market_exec_buy")
        if p_exec is None:
            return None
        return float(p_fair) - float(p_exec)
    return None


def _alignment_check(outcome: Optional[str], side: Optional[str], z_mom: Optional[float]) -> Optional[str]:
    if z_mom is None:
        return None
    direction = _outcome_direction(outcome)
    if direction is None:
        return "ALIGNMENT_UNKNOWN"
    expected_sign = direction * _sign(float(z_mom))
    if expected_sign == 0:
        return "ALIGNMENT_UNKNOWN"
    if side not in {"buy", "sell"}:
        return "ALIGNMENT_UNKNOWN"
    if expected_sign > 0 and side != "buy":
        return "ALIGNMENT_MISMATCH"
    if expected_sign < 0 and side != "sell":
        return "ALIGNMENT_MISMATCH"
    return None


def _outcome_direction(outcome: Optional[str]) -> Optional[int]:
    if outcome is None:
        return None
    label = outcome.strip().lower()
    if "up" in label or label in {"yes", "true"}:
        return 1
    if "down" in label or label in {"no", "false"}:
        return -1
    return None


def _sign(value: float) -> int:
    if value > 0:
        return 1
    if value < 0:
        return -1
    return 0


def _dedupe(reasons: List[str]) -> List[str]:
    seen = set()
    output = []
    for reason in reasons:
        if reason in seen:
            continue
        seen.add(reason)
        output.append(reason)
    return output
