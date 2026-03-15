# Soak Health Summary (2026-03-11T21:36:06Z)

Run root: /Users/padraigjudge/Desktop/Polymarket Bot/tmp/desktop_run_archive/20260311/paperfirst-paper-20260310T220520Z

## Snapshot
- soak status: not_clean
- commit blocked: true
- blocker (from soak gate): FRESHNESS_OR_REFERENCE_BROKEN,NEW_ROLLOVER_FAILURES
- latest liveness reasons: E_ACTIVE_MARKET_LAG_HIGH,E_CLOCK_DRIFT_HIGH
- freeze state: 1
- active market lag ms: 330202.0

## Freshness
- book: stale (age 54163087 ms, 15.05 h; last ts 1773210803700)
- decision: stale (age 53828849 ms, 14.95 h; last ts 1773211137938)
- log: age 53829850 ms (14.95 h; last ts 1773211136937)
- estimated spot reference age: 23883885 ms (6.63 h)
- estimated perp reference age: 23884162 ms (6.63 h)

## Deltas Since Checkpoint (ts_ms=1773241036716)
- book: 0
- decisions: 0
- logs: 0
- orders: 0
- fills: 0
- rollover INTENT/COMMIT/ABORT: 0/0/0

## Deltas Since Soak Snapshot (ts_ms=1773240898674)
- book: 0
- decisions: 0
- logs: 0
- orders: 0
- fills: 0
- rollover INTENT/COMMIT/ABORT: 0/0/0

## Rollover totals
- INTENT: 148
- COMMIT: 29
- ABORT: 25
- last ABORT reason/error: DISCOVERY_ERROR / gamma_fetch_failed status=unknown url=https://gamma-api.polymarket.com/events?active=true&limit=1000&offset=0

## Assessment
- current blocker: FRESHNESS_OR_REFERENCE_BROKEN,NEW_ROLLOVER_FAILURES; corroborated by stale book/decision streams and liveness reasons (E_ACTIVE_MARKET_LAG_HIGH,E_CLOCK_DRIFT_HIGH).
- intervention: required.
