# Soak Health Summary

- Timestamp (UTC): 2026-03-11T18:05:06Z
- Run root inspected: /Users/padraigjudge/Desktop/Polymarket Bot/tmp/desktop_run_archive/20260311/paperfirst-paper-20260310T220520Z
- Active-root note: no direct /Users/padraigjudge/Desktop/paperfirst-paper* directory exists; this is the only matching root under Desktop.

## Freshness
- Gate status: not_clean
- Commit blocked: true
- Blocking reason: FRESHNESS_OR_REFERENCE_BROKEN,NEW_ROLLOVER_FAILURES
- Book freshness age (checkpoint snapshot): 30235300 ms
- Decision freshness age (checkpoint snapshot): 29946115 ms
- Checkpoint snapshot age now: 11269284 ms (~187 min)
- Gate snapshot age now: 11407326 ms (~190 min)
- Spot reference age now (estimated from gate sample): 11407960 ms
- Perp reference age now (estimated from gate sample): 11408147 ms
- Latest DB ts (book/decision/orders/fills): 1773210803700 / 1773211137938 / 1773210802878 / 1773180822824

## Deltas
- Orders delta (gate sample): +290
- Fills delta (gate sample): +0
- Rollover intent delta (gate sample): +5
- Rollover commit delta (gate sample): +0
- Rollover discovery-error delta (gate sample): +1
- Since last automation check: no new deltas observed (source files unchanged).

## Liveness
- Rows added across core tables since gate sample: 0
- Interpretation: no new runtime rows since the last gate sample; run remains stalled.

## Intervention
- Required: yes
- Why: freshness/reference blocker persists, commit remains blocked, and there is no forward movement in orders/fills/decisions/rollover/logs.
