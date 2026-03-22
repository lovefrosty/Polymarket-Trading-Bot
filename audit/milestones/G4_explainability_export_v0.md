# G4 - Explainability + Incident Export v0

Date: 2026-02-11 (UTC)
Status: Complete

## Changed Files
- `dashboard/panels/staleness.py`
- `dashboard/panels/export.py`
- `scripts/export_runtime_jsonl.py`
- `tests/test_dashboard_replay_diff_v0.py`
- `tests/test_export_incident_bundle_v0.py`

## What Changed
- Added metric-inspector + evidence timeline panel and reconstructible `DrillthroughContext` (`context_id`, `context_hash`).
- Added strict incident bundle mode to exporter:
  - `manifest.json`
  - `context.json`
  - `logs.jsonl`
  - `decisions.jsonl`
  - `book_events.jsonl`
  - `system_state.json`
  - `config_fingerprint.json`
- Added replay/live mismatch v0 rule implementation for dashboard diagnostics.

## Acceptance Evidence
Commands executed:

```bash
.venv/bin/python -m pytest -q \
  tests/test_dashboard_replay_diff_v0.py \
  tests/test_export_incident_bundle_v0.py
```

Result: passing.

## Risk Notes
- Replay mismatch v0 depends on replay fields being populated in `policy_json`; otherwise panel reports degraded mode.

## Rollback
- Revert `scripts/export_runtime_jsonl.py` and dashboard explainability/export panel modules.
