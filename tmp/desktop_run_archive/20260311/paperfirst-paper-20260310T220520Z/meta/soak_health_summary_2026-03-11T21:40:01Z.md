# Soak Health Summary

- Generated: 2026-03-11T21:40:01Z
- Run root: /Users/padraigjudge/Desktop/Polymarket Bot/tmp/desktop_run_archive/20260311/paperfirst-paper-20260310T220520Z
- Status: not_clean (commit_blocked=True)
- Current blocker: FRESHNESS_OR_REFERENCE_BROKEN,NEW_ROLLOVER_FAILURES

## Freshness
- Book age now: 54388082 ms (last ts 1773210803700)
- Decision age now: 54053844 ms (last ts 1773211137938)
- Log age now: 54054845 ms (last ts 1773211136937)
- Spot reference age (estimated): 24293742 ms
- Perp reference age (estimated): 24293929 ms

## Count deltas
- Book delta vs checkpoint: 9108
- Decision delta vs checkpoint: 88
- Orders delta vs soak snapshot counts: 0
- Fills delta vs soak snapshot counts: 0
- Rollover INTENT delta vs soak snapshot counts: 6
- Rollover COMMIT delta vs soak snapshot counts: 0
- Rollover ABORT/ERROR delta vs soak snapshot counts: 0

## Liveness / rollover tail
- Latest liveness ts: 1773211133951
- Latest liveness reasons: E_ACTIVE_MARKET_LAG_HIGH,E_CLOCK_DRIFT_HIGH
- Latest rollover event: INTENT @ 1773211136937

## Intervention
- Required: yes
- Why: pipeline appears stalled (no recent book/decision/log updates and commit still blocked).
