# Alert & Bottleneck Stabilization (Mode-Aware) - Milestone Report

Date: 2026-02-11 UTC

## Scope Completed
- P* validity state machine integrated (`UNAVAILABLE/WARMING/VALID/STALE/DIVERGED`).
- Mode-aware freeze policy:
  - `OBSERVE`: downgrade non-hard freeze paths to `DEGRADED`.
  - `PAPER/TRADE`: fail-closed behavior retained.
- Startup feed guard hardened with readiness states:
  - `BOOTING -> PARTIAL -> READY`.
- Liveness startup grace path added (degrade-first before freeze).
- Unknown WS alerting hardened:
  - minimum sample gate, sustained breach windows, recovery hysteresis, richer payload.
- Causality diagnostics expanded:
  - clamped as-of timestamps, offending timestamp evidence in alerts/payloads.
- Dashboard top bar contract extended to show `OK/DEGRADED/FROZEN` and readiness state.
- Health panel reason drilldown wired to evidence rows.

## Changed Files
- `core/pstar.py`
- `core/metrics.py`
- `data/polymarket_ws.py`
- `scripts/run_system.py`
- `config/constitution.yaml`
- `dashboard/contracts.py`
- `dashboard/app.py`
- `dashboard/panels/reliability.py`
- `tests/test_unknown_ws_alert_guard.py`
- `tests/test_unknown_never_affects_health.py`
- `tests/test_startup_guard_v1.py`
- `tests/test_dashboard_topbar_contract.py`
- `tests/test_mode_aware_freeze_v1.py`
- `tests/test_pstar_state_machine_v1.py`
- `tests/test_liveness_startup_grace_v1.py`

## Test Evidence
- Targeted stabilization suite: `22 passed`
- Dashboard suite: `12 passed`
- Additional runtime/policy suite: `12 passed`

## Residual Risks
- Full end-to-end smoke run against live discovery/providers is network-dependent and was not executable in sandbox (DNS/network blocked).
- Alert thresholds may still require environment-specific tuning under live traffic.

## Rollback Switches
- Unknown alert behavior can be tuned via `trading.unknown_alert_policy`.
- Startup liveness grace controlled by `trading.startup_liveness_grace_ms`.
- OBSERVE causality freeze escalation controlled by `trading.observe_causality_freeze_after`.
