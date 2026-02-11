# G0 Baseline - Live Readiness Program

Date: 2026-02-11 (UTC)
Program profile: Conservative, reliability-first, milestone check-ins

## Scope
This baseline captures current system state before G1-G7 implementation.

## Baseline Test Snapshot
Command:

```bash
.venv/bin/python -m pytest -q \
  tests/test_onchain_ingest.py \
  tests/test_slug_discovery.py \
  tests/test_market_discovery.py \
  tests/test_dashboard_missing_tables.py \
  tests/test_dashboard_topbar_contract.py \
  tests/test_dashboard_health_tabs.py \
  tests/test_dashboard_refresh_cadence.py \
  tests/test_dashboard_contract_adapters.py \
  tests/test_check_promotion_missing_tables_diagnostics.py \
  tests/test_promotion_gate_checker_v1.py
```

Result: `19 passed in 1.25s`

## Runtime DB Inventory (runtime.db)
Total tables discovered: 18

- `alerts` (2 rows)
- `book_health_stats` (42 rows)
- `decision_ticks` (1 row)
- `decisions` (1 row)
- `exec_latency` (0 rows)
- `fills` (0 rows)
- `inventory` (1 row)
- `latency_stats` (21 rows)
- `logs` (7 rows)
- `market_data_book` (138 rows)
- `market_trades` (0 rows)
- `microstructure_stats` (1 row)
- `orders` (0 rows)
- `pstar` (2 rows)
- `pstar_stats` (1 row)
- `reconciliation_stats` (21 rows)
- `recovery_events` (1 row)
- `system_state` (0 rows)

## Dashboard Runtime Baseline
- Refresh model: Streamlit fragment polling.
- Default interval: 1000ms.
- Heavy refresh cadence: every 5 ticks.
- Known risk: dashboard logic concentrated in `dashboard/app.py` monolith.

## Top Risks (Pre-G1)
1. Discovery fragility under slug query misses can hard-fail startup paths.
2. On-chain reconcile errors require clearer diagnostics for rapid remediation.
3. Panel dependency/fallback behavior is not standardized across tabs.
4. Drillthrough context is not yet reconstructible/export-canonical.

## Locked Promotion Thresholds (from config/constitution.yaml)
- `pstar_max_age_ms=3000`
- `pstar_freeze_disagree_bps=50.0`
- `book_stale_after_ms=30000`
- `book_down_after_ms=120000`
- `signal_age_max_ms=1200`
- `ack_p95_max_ms=400.0`
- `ws_lag_max_ms=1000.0`

## Exit
G0 complete when this baseline is committed and linked from `.github/PROJECT.md`.
