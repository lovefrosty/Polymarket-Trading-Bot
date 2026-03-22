# G2 - Evidence Contract + Source Guards

Date: 2026-02-11 (UTC)
Status: Complete

## Changed Files
- `core/sqlite_store.py`
- `dashboard/data_access.py`
- `dashboard/contracts.py`
- `tests/test_dashboard_source_guards.py`

## What Changed
- Added canonical `evidence_rows` table in SQLite schema.
- Added automatic evidence-row emission for `decisions`, `orders`, and `fills` insert paths.
- Added evidence-row emission for `append_log` and `append_alert`.
- Added dashboard data access layer with:
  - `query_df(...)`
  - `require_sources(...)`
  - adapters for decisions/orders/fills
  - canonical evidence row query union
- Added deterministic `DrillthroughContext` hash generation.

## Acceptance Evidence
- Source guard and context-hash determinism tests pass.
- Existing sqlite store and reconciliation tests continue to pass.

Commands executed:

```bash
.venv/bin/python -m pytest -q \
  tests/test_dashboard_source_guards.py \
  tests/test_sqlite_store_v1.py \
  tests/test_reconcile_missed_fill_updates_fsm.py \
  tests/test_reconcile_freeze_determinism_replay.py
```

Result: passing.

## Risk Notes
- `evidence_rows` growth rate should be monitored in long-running deployments.

## Rollback
- Revert `core/sqlite_store.py` and `dashboard/data_access.py`.
