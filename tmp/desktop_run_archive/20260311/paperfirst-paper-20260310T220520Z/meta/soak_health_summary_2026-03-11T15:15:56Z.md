# Soak Health Summary

- Generated at (local): 2026-03-11 11:16:14 EDT
- Generated at (UTC): 2026-03-11T15:16:14Z
- Analyzed run root: /Users/padraigjudge/Desktop/Polymarket Bot/tmp/desktop_run_archive/20260311/paperfirst-paper-20260310T220520Z
- Note: `soak_gate_status.json` still points at `/Users/padraigjudge/Desktop/paperfirst-paper-20260310T220520Z`, which is not currently present.

## Fresh book/decision status

- Gate status: `not_clean` (`commit_blocked=true`)
- Latest stream ages from DB (at read time):
  - `book_age_ms`: 31,304,300
  - `decision_age_ms`: 30,970,062
  - `log_age_ms`: 30,971,063
- Interpretation: book/decision/log streams are stale by ~8.6 hours.

## Spot/perp freshness

- Last gate sample reference ages (`soak_gate_status.json`):
  - `spot`: 634 ms
  - `perp`: 821 ms
- Current interpretation: references were fresh at the last gate sample, but no new samples are arriving now (pipeline stale).

## Order/fill deltas

From `soak_gate_status.json -> deltas_since_last_sample`:
- `orders`: +290
- `fills`: +0
- `quote`: +145
- `skip`: +117
- `freeze`: +246

Current totals (`runtime.db`):
- `orders`: 50,609
- `fills`: 152

## Rollover intent/commit/error deltas

From `soak_gate_status.json -> deltas_since_last_sample`:
- `rollover_intent`: +5
- `rollover_commit`: +0
- `rollover_abort_discovery_error`: +1
- `rollover_health_freeze`: +0

Current totals (`soak_gate_status.json` counts):
- `rollover_intent`: 142
- `rollover_commit`: 29
- `rollover_abort_discovery_error`: 25
- `rollover_health_freeze`: 8

## Current blocker

- `blocking_reason`: `FRESHNESS_OR_REFERENCE_BROKEN,NEW_ROLLOVER_FAILURES`
- Clean window target: 6h (`21600000 ms`)
- `clean_window_elapsed_ms`: `null`

## Intervention required?

- **Yes**. Commit gate is blocked and telemetry is stale; soak cannot clear while data is not advancing and rollover failures are still being introduced.
