# Operator Playbook

## Single source of truth
- Codebase: `/Users/padraigjudge/Desktop/Polymarket Bot` on `main`
- Branch/run map: `/Users/padraigjudge/Desktop/Polymarket Bot/tmp/branch_run_map.md`
- Active validation root: `/Users/padraigjudge/Desktop/paperfirst-observe-livenessfix-20260311T185029Z`
- PAPER gate: `/Users/padraigjudge/Desktop/paperfirst-observe-livenessfix-20260311T185029Z/meta/paper_gate_status.md`

## Current blocker
The current `OBSERVE` run is stale and the gate to bounded PAPER is closed.

Precise blocker from `/Users/padraigjudge/Desktop/paperfirst-observe-livenessfix-20260311T185029Z/runtime.db`:
- repeated `rollover_abort_switch`
- repeated `rollover_health_freeze`
- latest switch failures are `CONFIRM_TIMEOUT`
- `confirm_diag.failure_class = NO_PENDING_MESSAGES`
- one earlier discovery timeout exists:
  - `rollover_abort_discovery_error`
  - `error_code = TIMEOUTERROR`

This means the current problem is not quoting aggressiveness. It is rollover continuity on the Polymarket side after earlier successful commits.

## What not to do
- Do not widen to `TRADE`
- Do not increase trading aggressiveness yet
- Do not resume old Desktop worktrees
- Do not treat stale monitor output as truth; use DB truth and `paper_gate_status.*`

## What to check first
1. `paper_gate_status.md`
- If `paper_gate = OPEN`, bounded PAPER is allowed
- If `paper_gate = CLOSED`, use the blocking reason there

2. Latest rollover payloads in the active run DB
- inspect `rollover_abort_switch`
- inspect `rollover_health_freeze`
- inspect the latest `rollover_commit`

3. Freshness
- `market_data_book`
- `decisions`
- `logs`
- `spot` and `perp` reference ages from the gate monitor

## When bounded PAPER is allowed
Bounded PAPER is open only when all are true:
- fresh `book`, `decisions`, and `logs`
- fresh `spot` and `perp`
- at least 2 clean rollover commits since the last failure
- no new `rollover_abort_discovery_error`
- no new `rollover_health_freeze`
- no candidate-liveness regression

## What to run next
If the gate stays closed:
1. Inspect the latest `rollover_abort_switch` payloads
2. Determine whether the failure is:
   - no pending messages again
   - discovery timeout
   - readiness/policy block
3. Fix the new failure class narrowly on `main`
4. Relaunch one fresh isolated `OBSERVE`
5. Recheck `paper_gate_status.md`

If the gate opens:
1. Start one fresh bounded `PAPER` run from `main`
2. Use the same reference pair:
   - `poll_coinbase`
   - `ws_kraken_futures_perp`
3. Keep the existing gate monitor model and do not tune aggressiveness until PAPER proves stable
