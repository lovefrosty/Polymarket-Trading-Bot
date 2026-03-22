# G3 - Dashboard Architecture Refactor

Date: 2026-02-11 (UTC)
Status: Complete (initial split)

## Changed Files
- `dashboard/app.py`
- `dashboard/contracts.py`
- `dashboard/data_access.py`
- `dashboard/panels/__init__.py`
- `dashboard/panels/market_context.py`
- `dashboard/panels/staleness.py`
- `dashboard/panels/reliability.py`
- `dashboard/panels/signals.py`
- `dashboard/panels/replay_diff.py`
- `dashboard/panels/export.py`
- `tests/test_dashboard_missing_tables.py`

## What Changed
- Refactored dashboard to module structure with panel-specific renderers.
- `app.py` now primarily handles:
  - layout and tabs
  - fragment refresh orchestration
  - top-bar computation and tab wiring
- Added panel-level safety behavior:
  - explicit `DEGRADED` fallbacks on exceptions
  - panel render budget warnings when over threshold
- Preserved retro terminal style and fragment-based live update behavior.

## Acceptance Evidence
- Dashboard contract tests and adapter tests pass.
- Missing table behavior test updated to verify data-access fallback path.

Command executed:

```bash
.venv/bin/python -m pytest -q \
  tests/test_dashboard_topbar_contract.py \
  tests/test_dashboard_health_tabs.py \
  tests/test_dashboard_refresh_cadence.py \
  tests/test_dashboard_contract_adapters.py \
  tests/test_dashboard_missing_tables.py
```

Result: passing.

## Risk Notes
- Additional extraction of overview/inventory/micro code paths can be done in next cleanup pass.

## Rollback
- Revert dashboard modules and restore prior `dashboard/app.py`.
