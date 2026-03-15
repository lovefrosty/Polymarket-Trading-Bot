# Soak Health Summary (2026-03-11T19:23:57Z UTC)

Run root: /Users/padraigjudge/Desktop/Polymarket Bot/tmp/desktop_run_archive/20260311/paperfirst-paper-20260310T220520Z  
Checkpoint ts_ms: 1773241036716  
Soak gate ts_ms: 1773240898674

## Current status
- Soak gate status: **not_clean**
- Commit blocked: **true**
- Current blocker: **FRESHNESS_OR_REFERENCE_BROKEN,NEW_ROLLOVER_FAILURES**
- System frozen (latest): **1**
- Freeze/block reasons (latest): 
  - system_state: C_BOOK_DOWN,C_BOOK_STALE,C_SLIPPAGE_HIGH,E_ACTIVE_MARKET_LAG_HIGH,E_CLOCK_DRIFT_HIGH,E_LIVENESS_CRITICAL,E_WS_LAG_HIGH
  - decision_ticks: C_BOOK_DOWN,C_BOOK_STALE,C_SLIPPAGE_HIGH,E_ACTIVE_MARKET_LAG_HIGH,E_CLOCK_DRIFT_HIGH,E_LIVENESS_CRITICAL,E_WS_LAG_HIGH

## Freshness
- Book freshness now: **stale** (age 45903049ms; checkpoint had 30235300ms)
- Decision freshness now: **stale** (age 45899062ms; checkpoint had 29946115ms)
- Spot reference freshness now (estimated from soak sample): **stale** (age 16138960ms)
- Perp reference freshness now (estimated from soak sample): **stale** (age 16139147ms)
- Latest decision sourceset: ["perp","spot"]

## Activity deltas
- Since soak snapshot (soak_gate_status.json):
  - Orders: 0
  - Fills: 0
  - Rollover intent: 6
  - Rollover commit: 0
  - Rollover abort discovery error: 0
- Since checkpoint (checkpoint_latest.json):
  - Book rows: 9108
  - Decision rows: 88
  - Log rows: 2
  - Rollover intent: 2
  - Rollover commit: 0

## Liveness detail
- Last book stats row age: 45903049ms
- Last decision row age: 45899062ms
- Last order row age: 46234122ms
- Last fill row age: 76214176ms
- Last rollover row age: 45900063ms

## Assessment
- No fresh book/decision activity in hours; run remains frozen and commit-blocked.
- New rollover commits are not occurring; only minor intent movement vs checkpoint.
- Intervention required to restore data freshness/reference integrity and clear rollover failure blocker.
