# G UX Signoff - Trader/Developer UX Smoke

## Run Metadata
- UTC timestamp: 2026-02-11
- Runtime DB: `/Users/padraigjudge/Desktop/Polymarket Bot/tmp/soak30_20260211_150115/runtime.db`
- Dashboard command used:
  - `.venv/bin/python /Users/padraigjudge/Desktop/Polymarket Bot/scripts/run_dashboard.py --db-path /Users/padraigjudge/Desktop/Polymarket Bot/tmp/soak30_20260211_150115/runtime.db --port 8502 --host 127.0.0.1`
- HTTP reachability check:
  - `curl http://127.0.0.1:8502` returned `200`
- AppTest evidence bundle:
  - `/Users/padraigjudge/Desktop/Polymarket Bot/tmp/ux_signoff_apptest.json`

## Regression Test Results
- Command:
  - `.venv/bin/python -m pytest -q tests/test_trader_view_hides_token_ids.py tests/test_developer_view_shows_ids.py tests/test_no_meta_refresh.py tests/test_label_mapper_stability.py tests/test_dashboard_*`
- Result:
  - `22 passed in 0.65s`

## Trader View Checklist (Hard Checks)
- No `token_id` / `decision_id` in visible tables: **PASS**
  - AppTest `trader.id_leaks = []` in `/Users/padraigjudge/Desktop/Polymarket Bot/tmp/ux_signoff_apptest.json`
- Market/side shown as human labels: **PASS**
  - Trader dataframes include `Market` / `Outcome` columns, no raw id columns.
- Health reasons readable English first: **PASS**
  - Humanizer wired in topbar/log health paths via `/Users/padraigjudge/Desktop/Polymarket Bot/dashboard/app.py:407` and `/Users/padraigjudge/Desktop/Polymarket Bot/dashboard/app.py:992`.
- No large white panel backgrounds: **PASS (structural)**
  - Terminal CSS forces dark/transparent containers and dataframe shells in `/Users/padraigjudge/Desktop/Polymarket Bot/dashboard/app.py:40`.
- Refresh updates in place (no full-layout churn): **PASS (structural + runtime)**
  - Stable placeholder slots + fragment loop in `/Users/padraigjudge/Desktop/Polymarket Bot/dashboard/app.py:1460`.
  - Fragment widget boundary issues were fixed by non-interactive auto-refresh rendering in:
    - `/Users/padraigjudge/Desktop/Polymarket Bot/dashboard/panels/reliability.py:67`
    - `/Users/padraigjudge/Desktop/Polymarket Bot/dashboard/panels/staleness.py:38`
    - `/Users/padraigjudge/Desktop/Polymarket Bot/dashboard/panels/signals.py:28`

## Developer Mode Checklist
- Raw `token_id` / `decision_id` visible: **PASS**
  - AppTest developer mode reports id-bearing tables (`developer.id_leaks` populated) in `/Users/padraigjudge/Desktop/Polymarket Bot/tmp/ux_signoff_apptest.json`.
- Raw technical reason fields remain accessible: **PASS**
  - Developer mode retains raw `code` / `reason_codes` columns in health/log tables.
- Read-only ops control remains disabled: **PASS**
  - `Cancel all quotes` stays disabled in `/Users/padraigjudge/Desktop/Polymarket Bot/dashboard/app.py:1338`.

## Screenshots
- Trader screenshot: **BLOCKED in this headless run environment**
- Developer screenshot: **BLOCKED in this headless run environment**
- Reason:
  - No browser automation/screenshot binary available in this environment without adding tooling.
- Manual capture command (local GUI):
  - Start dashboard with the run command above, open `http://127.0.0.1:8502`, capture Trader and Developer mode screenshots.

## Defects Found During Smoke (and fixed)
1. Fragment widget boundary error in auto-refresh paths.
   - Severity: High
   - Symptom: `StreamlitFragmentWidgetsNotAllowedOutsideError`
   - Fix locations:
     - `/Users/padraigjudge/Desktop/Polymarket Bot/dashboard/app.py:1497`
     - `/Users/padraigjudge/Desktop/Polymarket Bot/dashboard/panels/reliability.py:67`
     - `/Users/padraigjudge/Desktop/Polymarket Bot/dashboard/panels/staleness.py:38`
     - `/Users/padraigjudge/Desktop/Polymarket Bot/dashboard/panels/signals.py:28`
2. Trader mode empty-table header leakage (`token_id` still visible for empty frames).
   - Severity: Medium
   - Fix locations:
     - `/Users/padraigjudge/Desktop/Polymarket Bot/dashboard/panels/reliability.py:46`
     - `/Users/padraigjudge/Desktop/Polymarket Bot/dashboard/panels/staleness.py:80`

## Branch Decision
- Outcome: **PASS for functional/operator checks in this environment**
- Residual: manual screenshot capture still needed for visual record.
- Next step: resume reliability loop (A vs E vs BOOK bottleneck) using this same runtime DB evidence workflow.
