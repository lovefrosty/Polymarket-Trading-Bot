# Soak Health Summary (2026-03-11T18:27:00Z)

- Run root: `/Users/padraigjudge/Desktop/Polymarket Bot/tmp/desktop_run_archive/20260311/paperfirst-paper-20260310T220520Z`
- Active paper root discovery under `/Users/padraigjudge/Desktop`: single match found (this root).
- Gate status: `not_clean`
- Commit blocked: `True`
- Current blocker: `FRESHNESS_OR_REFERENCE_BROKEN,NEW_ROLLOVER_FAILURES`

## Freshness
- Book freshness now (from DB max ts): `42816350 ms` stale
- Decision freshness now (from DB max ts): `42482112 ms` stale
- Spot reference freshness now (inferred from gate sample + elapsed): `12722010 ms` stale
- Perp reference freshness now (inferred from gate sample + elapsed): `12722197 ms` stale
- Last gate sample ts: `1773240898674` (elapsed `12721376 ms`)

## Order/Fill Deltas
- Orders delta since gate sample: `0`
- Fills delta since gate sample: `0`
- Current totals: orders `50609`, fills `152`

## Rollover Deltas
- Intent delta since gate sample: `0`
- Commit delta since gate sample: `0`
- Abort delta since gate sample: `0`
- Current totals: intent `148`, commit `29`, abort `25`

## Checkpoint/Gate Snapshot Notes
- `checkpoint_latest.json` was found at `meta/checkpoint_latest.json` (no root-level `checkpoint_latest.json` present).
- Checkpoint snapshot ts: `1773241036716`
- Gate `deltas_since_last_sample`: `{"fills": 0, "freeze": 246, "orders": 290, "quote": 145, "rollover_abort_discovery_error": 1, "rollover_commit": 0, "rollover_health_freeze": 0, "rollover_intent": 5, "skip": 117}`

## Assessment
- Soak health: **unhealthy/stalled**.
- Evidence: no new orders/fills/rollover events since gate sample, with stale book/decision/reference ages and gate still `not_clean` + `commit_blocked`.
- Intervention required: **yes**.
