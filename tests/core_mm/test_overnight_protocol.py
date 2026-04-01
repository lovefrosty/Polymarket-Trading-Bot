from __future__ import annotations

from core_mm.overnight_protocol import (
    OvernightProtocolConfig,
    OvernightProtocolState,
    build_protocol_observations,
    decide_actions,
    next_state,
)


def test_decide_actions_flattens_before_kill_on_drawdown() -> None:
    config = OvernightProtocolConfig(kill_drawdown_pct=0.10, flatten_before_kill_enabled=True)
    actions = decide_actions(
        snapshot={
            "mode": "PAPER",
            "stage": "running",
            "quoteable": True,
            "cluster_exposure": {
                "clusters": [
                    {"cluster_id": "BTC-HOURLY-1", "gross_exposure": 15.0},
                ]
            },
        },
        control={"trading_enabled": True, "kill_switch_enabled": False, "pending_count": 0},
        pnl_summary={"max_drawdown_pct": 0.12},
        recent_commands=[],
        fill_timeline_rows=[],
        state=OvernightProtocolState(),
        config=config,
        now_ms=1_700_000_000_000,
    )
    assert [action["command_type"] for action in actions] == ["cancel_all_quotes", "flatten_event_inventory"]
    assert actions[1]["payload"]["event_id"] == "BTC-HOURLY-1"


def test_decide_actions_kill_switch_when_drawdown_persists_after_flatten_attempt() -> None:
    config = OvernightProtocolConfig(kill_drawdown_pct=0.10, flatten_before_kill_enabled=True)
    actions = decide_actions(
        snapshot={"mode": "PAPER", "stage": "running", "quoteable": True, "cluster_exposure": {"clusters": []}},
        control={"trading_enabled": True, "kill_switch_enabled": False, "pending_count": 0},
        pnl_summary={"max_drawdown_pct": 0.12},
        recent_commands=[{"command_type": "flatten_event_inventory", "status": "applied", "requested_at_ms": 1_700_000_000_000 - 10_000}],
        fill_timeline_rows=[],
        state=OvernightProtocolState(),
        config=config,
        now_ms=1_700_000_000_000,
    )
    assert len(actions) == 1
    assert actions[0]["command_type"] == "kill_switch_on"


def test_decide_actions_logs_drawdown_in_stress_mode_without_kill() -> None:
    config = OvernightProtocolConfig(
        overnight_protocol_mode="stress_test",
        kill_drawdown_pct=0.10,
        stress_drawdown_log_only_pct=0.08,
    )
    actions = decide_actions(
        snapshot={"mode": "PAPER", "stage": "running", "quoteable": True},
        control={"trading_enabled": True, "kill_switch_enabled": False, "pending_count": 0},
        pnl_summary={"max_drawdown_pct": 0.12},
        recent_commands=[],
        fill_timeline_rows=[],
        state=OvernightProtocolState(),
        config=config,
        now_ms=1_700_000_000_000,
    )
    assert actions == []

    observations = build_protocol_observations(
        snapshot={"quoteable": True, "book_health": "healthy"},
        control={"trading_enabled": True, "kill_switch_enabled": False, "flatten_only_mode": False},
        pnl_summary={"max_drawdown_pct": 0.12},
        fill_timeline_rows=[],
        config=config,
    )
    assert observations["stress_drawdown_breach_observed"] is True
    assert observations["live_safe_intervention_would_trigger"] is True


def test_decide_actions_pause_on_persistent_non_quoteable() -> None:
    config = OvernightProtocolConfig(pause_on_non_quoteable_cycles=2)
    state = OvernightProtocolState()
    state = next_state(
        snapshot={"stage": "running", "quoteable": False},
        control={"trading_enabled": True, "kill_switch_enabled": False, "pending_count": 0},
        commands=[],
        state=state,
    )
    state = next_state(
        snapshot={"stage": "running", "quoteable": False},
        control={"trading_enabled": True, "kill_switch_enabled": False, "pending_count": 0},
        commands=[],
        state=state,
    )
    actions = decide_actions(
        snapshot={"mode": "PAPER", "stage": "running", "quoteable": False},
        control={"trading_enabled": True, "kill_switch_enabled": False, "pending_count": 0},
        pnl_summary={"max_drawdown_pct": 0.0},
        recent_commands=[],
        fill_timeline_rows=[],
        state=state,
        config=config,
        now_ms=1_700_000_000_000,
    )
    assert len(actions) == 1
    assert actions[0]["command_type"] == "pause_trading"
    assert actions[0]["owner"] == "Kant"


def test_decide_actions_pause_on_control_backlog() -> None:
    config = OvernightProtocolConfig(
        pause_on_pending_backlog_cycles=2,
        pending_command_age_secs=120.0,
    )
    state = OvernightProtocolState(
        consecutive_non_quoteable=0,
        consecutive_pending_backlog=2,
        consecutive_healthy_paused=0,
        last_action_ts_ms={},
    )
    actions = decide_actions(
        snapshot={"mode": "PAPER", "stage": "running", "quoteable": True},
        control={"trading_enabled": True, "kill_switch_enabled": False, "pending_count": 1},
        pnl_summary={"max_drawdown_pct": 0.0},
        recent_commands=[{"command_type": "apply_config_patch", "status": "pending", "requested_at_ms": 1_700_000_000_000 - 130_000}],
        fill_timeline_rows=[],
        state=state,
        config=config,
        now_ms=1_700_000_000_000,
    )
    assert len(actions) == 1
    assert actions[0]["command_type"] == "pause_trading"
    assert actions[0]["owner"] == "Ramanujan"


def test_decide_actions_restart_when_healthy_and_paused() -> None:
    config = OvernightProtocolConfig(auto_safe_restart=True, restart_cooldown_secs=300.0)
    state = OvernightProtocolState(
        consecutive_non_quoteable=0,
        consecutive_pending_backlog=0,
        consecutive_healthy_paused=3,
        last_action_ts_ms={},
    )
    actions = decide_actions(
        snapshot={"mode": "PAPER", "stage": "running", "quoteable": True},
        control={"trading_enabled": False, "kill_switch_enabled": False, "pending_count": 0},
        pnl_summary={"max_drawdown_pct": 0.0},
        recent_commands=[],
        fill_timeline_rows=[],
        state=state,
        config=config,
        now_ms=1_700_000_000_000,
    )
    assert len(actions) == 1
    assert actions[0]["command_type"] == "restart_paper_run_safe_profile"
