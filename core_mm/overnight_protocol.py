from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence


@dataclass(frozen=True)
class OvernightProtocolConfig:
    overnight_protocol_mode: str = "live_safe"
    kill_drawdown_pct: float = 0.10
    stress_drawdown_log_only_pct: float = 0.10
    stress_flatten_alert_only: bool = True
    stress_allow_drawdown_continuation: bool = True
    flatten_before_kill_enabled: bool = True
    pause_on_non_quoteable_cycles: int = 2
    pause_on_pending_backlog_cycles: int = 2
    pause_on_stale_unwind_count: int = 25
    pending_command_age_secs: float = 120.0
    action_cooldown_secs: float = 300.0
    restart_cooldown_secs: float = 900.0
    auto_safe_restart: bool = True


@dataclass
class OvernightProtocolState:
    consecutive_non_quoteable: int = 0
    consecutive_pending_backlog: int = 0
    consecutive_healthy_paused: int = 0
    last_action_ts_ms: Dict[str, int] = field(default_factory=dict)


def next_state(
    *,
    snapshot: Dict[str, Any],
    control: Dict[str, Any],
    commands: Sequence[Dict[str, Any]],
    state: OvernightProtocolState,
) -> OvernightProtocolState:
    next_value = OvernightProtocolState(
        consecutive_non_quoteable=state.consecutive_non_quoteable,
        consecutive_pending_backlog=state.consecutive_pending_backlog,
        consecutive_healthy_paused=state.consecutive_healthy_paused,
        last_action_ts_ms=dict(state.last_action_ts_ms),
    )
    quoteable = bool(snapshot.get("quoteable"))
    trading_enabled = bool(control.get("trading_enabled", True))
    pending_count = int(control.get("pending_count") or 0)

    next_value.consecutive_non_quoteable = (
        next_value.consecutive_non_quoteable + 1
        if str(snapshot.get("stage") or "") == "running" and not quoteable
        else 0
    )
    next_value.consecutive_pending_backlog = (
        next_value.consecutive_pending_backlog + 1
        if pending_count > 0
        else 0
    )
    next_value.consecutive_healthy_paused = (
        next_value.consecutive_healthy_paused + 1
        if (quoteable and not trading_enabled and not bool(control.get("kill_switch_enabled")))
        else 0
    )
    return next_value


def decide_actions(
    *,
    snapshot: Dict[str, Any],
    control: Dict[str, Any],
    pnl_summary: Dict[str, Any],
    recent_commands: Sequence[Dict[str, Any]],
    fill_timeline_rows: Sequence[Dict[str, Any]],
    state: OvernightProtocolState,
    config: OvernightProtocolConfig,
    now_ms: int,
) -> List[Dict[str, Any]]:
    actions: List[Dict[str, Any]] = []
    mode = str(snapshot.get("mode") or "").upper()
    if mode != "PAPER":
        return actions
    if bool(control.get("kill_switch_enabled")):
        return actions

    max_drawdown_pct = _float_or_none(pnl_summary.get("max_drawdown_pct")) or 0.0
    trading_enabled = bool(control.get("trading_enabled", True))
    protocol_mode = str(config.overnight_protocol_mode or "live_safe").lower()

    rejected_recent = [
        cmd for cmd in recent_commands
        if str(cmd.get("status") or "") == "rejected"
    ]
    pending_recent = [
        cmd for cmd in recent_commands
        if str(cmd.get("status") or "") == "pending"
    ]
    oldest_pending_age_s = None
    if pending_recent:
        oldest_ts = min(int(cmd.get("requested_at_ms") or now_ms) for cmd in pending_recent)
        oldest_pending_age_s = max(0.0, (float(now_ms) - float(oldest_ts)) / 1000.0)

    stale_unwind_count = sum(
        1
        for row in fill_timeline_rows
        if str(row.get("risk_action") or "") == "STALE_UNWIND"
    )

    if protocol_mode == "stress_test":
        return actions

    if max_drawdown_pct >= float(config.kill_drawdown_pct):
        if bool(config.flatten_before_kill_enabled):
            flatten_actions = _drawdown_flatten_actions(
                snapshot=snapshot,
                recent_commands=recent_commands,
                state=state,
                cooldown_secs=config.action_cooldown_secs,
                now_ms=now_ms,
                reason=f"drawdown {max_drawdown_pct:.3f} exceeded limit",
            )
            if flatten_actions:
                return flatten_actions
        if _command_allowed("kill_switch_on", recent_commands=recent_commands, state=state, cooldown_secs=config.action_cooldown_secs, now_ms=now_ms):
            actions.append(_action("kill_switch_on", owner="Kant", severity="critical", reason=f"drawdown {max_drawdown_pct:.3f} exceeded limit"))
        return actions

    if rejected_recent and trading_enabled:
        if _command_allowed("pause_trading", recent_commands=recent_commands, state=state, cooldown_secs=config.action_cooldown_secs, now_ms=now_ms):
            actions.append(_action("pause_trading", owner="Ramanujan", severity="critical", reason="control command rejected"))

    if state.consecutive_non_quoteable >= int(config.pause_on_non_quoteable_cycles) and trading_enabled:
        if _command_allowed("pause_trading", recent_commands=recent_commands, state=state, cooldown_secs=config.action_cooldown_secs, now_ms=now_ms):
            actions.append(_action("pause_trading", owner="Kant", severity="warn", reason="runtime stayed non-quoteable"))

    if (
        state.consecutive_pending_backlog >= int(config.pause_on_pending_backlog_cycles)
        and oldest_pending_age_s is not None
        and oldest_pending_age_s >= float(config.pending_command_age_secs)
        and trading_enabled
    ):
        if _command_allowed("pause_trading", recent_commands=recent_commands, state=state, cooldown_secs=config.action_cooldown_secs, now_ms=now_ms):
            actions.append(_action("pause_trading", owner="Ramanujan", severity="warn", reason="pending command backlog stayed unresolved"))

    if stale_unwind_count >= int(config.pause_on_stale_unwind_count) and trading_enabled:
        if _command_allowed("pause_trading", recent_commands=recent_commands, state=state, cooldown_secs=config.action_cooldown_secs, now_ms=now_ms):
            actions.append(_action("pause_trading", owner="Kant", severity="warn", reason=f"stale unwind activity high ({stale_unwind_count})"))

    healthy_for_restart = (
        bool(snapshot.get("quoteable"))
        and not rejected_recent
        and stale_unwind_count < int(config.pause_on_stale_unwind_count)
        and (oldest_pending_age_s is None or oldest_pending_age_s < float(config.pending_command_age_secs))
    )
    if (
        bool(config.auto_safe_restart)
        and not trading_enabled
        and healthy_for_restart
        and state.consecutive_healthy_paused >= 2
    ):
        if _command_allowed(
            "restart_paper_run_safe_profile",
            recent_commands=recent_commands,
            state=state,
            cooldown_secs=config.restart_cooldown_secs,
            now_ms=now_ms,
        ):
            actions.append(_action("restart_paper_run_safe_profile", owner="Meta-Agent", severity="info", reason="runtime healthy again while paused"))

    return actions


def record_actions(state: OvernightProtocolState, actions: Sequence[Dict[str, Any]], now_ms: int) -> OvernightProtocolState:
    next_value = OvernightProtocolState(
        consecutive_non_quoteable=state.consecutive_non_quoteable,
        consecutive_pending_backlog=state.consecutive_pending_backlog,
        consecutive_healthy_paused=state.consecutive_healthy_paused,
        last_action_ts_ms=dict(state.last_action_ts_ms),
    )
    for action in actions:
        command_type = str(action.get("command_type") or "")
        if command_type:
            next_value.last_action_ts_ms[command_type] = int(now_ms)
    return next_value


def build_linear_comment_payload(
    *,
    runtime_root: str,
    snapshot: Dict[str, Any],
    actions: Sequence[Dict[str, Any]],
    alerts: Sequence[Dict[str, Any]],
) -> Dict[str, str]:
    action_text = ", ".join(str(item.get("command_type")) for item in actions) if actions else "No control action taken"
    top_alert = alerts[0]["summary"] if alerts else "No active alerts"
    owner = actions[0].get("owner") if actions else "Meta-Agent"
    return {
        "What changed": f"Overnight protocol checked runtime {snapshot.get('strategy_name') or snapshot.get('market') or runtime_root} and decided: {action_text}.",
        "Evidence": f"Runtime root: {runtime_root} | Market: {snapshot.get('market')} | Alert: {top_alert}",
        "Risk impact": "Reduced short-term paper risk by preferring safe halt / pause / restart actions." if actions else "No new control action; runtime remained within current safety protocol.",
        "Next task": f"Route follow-up to {owner} if the condition persists.",
    }


def build_protocol_observations(
    *,
    snapshot: Dict[str, Any],
    control: Dict[str, Any],
    pnl_summary: Dict[str, Any],
    fill_timeline_rows: Sequence[Dict[str, Any]],
    config: OvernightProtocolConfig,
) -> Dict[str, Any]:
    max_drawdown_pct = _float_or_none(pnl_summary.get("max_drawdown_pct")) or 0.0
    protocol_mode = str(config.overnight_protocol_mode or "live_safe").lower()
    stress_drawdown_breach = bool(
        protocol_mode == "stress_test"
        and max_drawdown_pct >= float(config.stress_drawdown_log_only_pct)
    )
    live_safe_drawdown_breach = bool(
        max_drawdown_pct >= float(config.kill_drawdown_pct)
    )
    stale_unwind_count = sum(
        1
        for row in fill_timeline_rows
        if str(row.get("risk_action") or "") == "STALE_UNWIND"
    )
    control_state = dict(control or {})
    return {
        "protocol_mode": protocol_mode,
        "max_drawdown_pct": float(max_drawdown_pct),
        "stress_drawdown_log_only_pct": float(config.stress_drawdown_log_only_pct),
        "live_safe_kill_drawdown_pct": float(config.kill_drawdown_pct),
        "stress_drawdown_breach_observed": stress_drawdown_breach,
        "live_safe_intervention_would_trigger": bool(live_safe_drawdown_breach),
        "stress_flatten_alert_only": bool(config.stress_flatten_alert_only),
        "stress_allow_drawdown_continuation": bool(config.stress_allow_drawdown_continuation),
        "flatten_before_kill_enabled": bool(config.flatten_before_kill_enabled),
        "flatten_only_observed": bool(control_state.get("flatten_only_mode")),
        "halt_after_flatten_observed": bool(control_state.get("halt_after_flatten")),
        "kill_switch_available": "kill_switch_enabled" in control_state,
        "kill_switch_enabled": bool(control_state.get("kill_switch_enabled")),
        "stale_unwind_count": int(stale_unwind_count),
        "quoteable": bool(snapshot.get("quoteable")),
        "book_health": snapshot.get("book_health"),
    }


def _command_allowed(
    command_type: str,
    *,
    recent_commands: Sequence[Dict[str, Any]],
    state: OvernightProtocolState,
    cooldown_secs: float,
    now_ms: int,
) -> bool:
    for item in recent_commands:
        if str(item.get("command_type") or "") != str(command_type):
            continue
        if str(item.get("status") or "") in {"pending", "acknowledged"}:
            return False
        ts_ms = int(item.get("requested_at_ms") or 0)
        if ts_ms > 0 and (float(now_ms) - float(ts_ms)) < float(cooldown_secs) * 1000.0:
            return False
    last_ts = int(state.last_action_ts_ms.get(str(command_type), 0) or 0)
    if last_ts > 0 and (float(now_ms) - float(last_ts)) < float(cooldown_secs) * 1000.0:
        return False
    return True


def _action(command_type: str, *, owner: str, severity: str, reason: str) -> Dict[str, Any]:
    return {
        "command_type": str(command_type),
        "owner": str(owner),
        "severity": str(severity),
        "reason": str(reason),
        "scope": "global",
        "payload": {"reason": str(reason)},
    }


def _drawdown_flatten_actions(
    *,
    snapshot: Dict[str, Any],
    recent_commands: Sequence[Dict[str, Any]],
    state: OvernightProtocolState,
    cooldown_secs: float,
    now_ms: int,
    reason: str,
) -> List[Dict[str, Any]]:
    actions: List[Dict[str, Any]] = []
    cluster_exposure = snapshot.get("cluster_exposure") if isinstance(snapshot.get("cluster_exposure"), dict) else {}
    clusters = list(cluster_exposure.get("clusters") or [])
    active_event_ids = [
        str(cluster.get("cluster_id") or cluster.get("event_id") or "")
        for cluster in clusters
        if float(cluster.get("gross_exposure") or 0.0) > 0.0
    ]
    active_event_ids = [event_id for event_id in active_event_ids if event_id]
    if not active_event_ids:
        return actions

    if _command_allowed(
        "cancel_all_quotes",
        recent_commands=recent_commands,
        state=state,
        cooldown_secs=cooldown_secs,
        now_ms=now_ms,
    ):
        actions.append(_action("cancel_all_quotes", owner="Kant", severity="critical", reason=f"{reason}; cancel quotes before flatten"))

    for event_id in active_event_ids:
        if _command_allowed(
            "flatten_event_inventory",
            recent_commands=recent_commands,
            state=state,
            cooldown_secs=cooldown_secs,
            now_ms=now_ms,
        ):
            action = _action("flatten_event_inventory", owner="Kant", severity="critical", reason=f"{reason}; flatten event inventory")
            action["payload"] = {"reason": f"{reason}; flatten event inventory", "event_id": event_id}
            actions.append(action)

    return actions


def _float_or_none(value: Any) -> Optional[float]:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None
