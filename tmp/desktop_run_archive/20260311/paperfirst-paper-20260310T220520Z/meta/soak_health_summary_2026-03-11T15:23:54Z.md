# Soak Health Summary

- Timestamp (UTC): 2026-03-11T15:23:54Z
- Run root inspected: /Users/padraigjudge/Desktop/Polymarket Bot/tmp/desktop_run_archive/20260311/paperfirst-paper-20260310T220520Z
- Active-root note: no direct /Users/padraigjudge/Desktop/paperfirst-paper* directory exists; this is the only matching root under Desktop.

## Freshness
- Gate status: not_clean
- Commit blocked: true
- Blocking reason: FRESHNESS_OR_REFERENCE_BROKEN,NEW_ROLLOVER_FAILURES
- Book freshness age (checkpoint): 30235300 ms
- Decision freshness age (checkpoint): 29946115 ms
- Spot reference age (gate snapshot): 634 ms
- Perp reference age (gate snapshot): 821 ms
- Checkpoint snapshot age now: 26 min old
- Gate snapshot age now: 28 min old
- Latest DB ts (book/decision/orders/fills): 1773210803700 / 1773211137938 / 1773210802878 / 1773180822824

## Deltas (from soak_gate_status.json)
- Orders delta: 290
- Fills delta: 0
- Rollover intent delta: 5
- Rollover commit delta: 0
- Rollover discovery-error delta: 1

## Liveness check
- Rows added across core tables since gate sample: 0
- Interpretation: no new runtime rows since the last gate sample; run appears stalled.

## Intervention
- Required: yes
- Why: blocker includes freshness/reference break plus new rollover failures, with commit still blocked and no fresh DB activity.
