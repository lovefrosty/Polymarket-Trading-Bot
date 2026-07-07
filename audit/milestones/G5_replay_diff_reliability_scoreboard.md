# G5 - Replay/Live Diff v0 + Reliability Scoreboard

Date: 2026-02-11 (UTC)
Status: Complete

## Changed Files
- `dashboard/panels/replay_diff.py`
- `dashboard/panels/reliability.py`
- `core/metrics.py`
- `scripts/walkforward_report.py`
- `tests/test_dashboard_reliability_scoreboard.py`
- `tests/test_metrics_reliability_rows.py`

## What Changed
- Replay/live diff v0 now includes reason-attribution timestamps extracted from replay payload fields (`reason_timestamps_ms` and replay `*_ts_ms`).
- Added reliability scoreboard in Health tab with ranked degradation sources:
  - `reference_pipeline`
  - `market_data_ws`
  - `execution_path`
  - `reconciliation`
  - `signal_pipeline`
- Added freeze trend aggregation (last 24h) from `alerts` + `system_state`.
- Added shared reliability scoring utility in `core/metrics.py` for deterministic status classification.
- Extended walk-forward report with reliability diagnostics and sensitivity deltas:
  - `fee_plus_5bps`
  - `slippage_plus_5bps`
  - `latency_plus_250ms`
  - `top_degradation_source`

## Acceptance Evidence
Commands executed:

```bash
.venv/bin/python -m pytest -q \
  tests/test_metrics_reliability_rows.py \
  tests/test_dashboard_reliability_scoreboard.py \
  tests/test_dashboard_replay_diff_v0.py
```

Result: passing.

Additional regression runs:

```bash
.venv/bin/python -m pytest -q \
  tests/test_dashboard_topbar_contract.py \
  tests/test_dashboard_health_tabs.py \
  tests/test_dashboard_refresh_cadence.py \
  tests/test_dashboard_contract_adapters.py \
  tests/test_dashboard_missing_tables.py \
  tests/test_dashboard_source_guards.py \
  tests/test_export_incident_bundle_v0.py \
  tests/test_latency.py \
  tests/test_onchain_ingest.py
```

Result: passing.

## Risk Notes
- Replay mismatch still depends on replay fields existing in `policy_json`; panel will degrade gracefully if absent.
- Reliability score thresholds are heuristic and should be calibrated against longer production traces.

## Rollback
- Revert `dashboard/panels/replay_diff.py`, `dashboard/panels/reliability.py`, `core/metrics.py`, and `scripts/walkforward_report.py`.
