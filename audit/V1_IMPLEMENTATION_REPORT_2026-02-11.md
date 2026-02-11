# Polymarket V1 Implementation Report (2026-02-11)

## Scope Completed This Session
- Implemented Polymarket-only V1 runtime path with canonical SQLite storage and dual-loop execution in `scripts/run_system.py`.
- Added core validated-object modules:
  - `core/book_cache.py` (`BookSnapshot`, `BookCache`)
  - `core/pstar.py` (`PStar`, `PStarBuilder`)
  - `core/policy_gate.py` (`PolicyGate` logic via `evaluate_policy`)
  - `core/execution_fsm.py` (`FROZEN`, `QUOTING_BOTH`, `ONE_SIDE_FILLED`, `REBALANCING`, `UNWINDING`)
  - `core/sqlite_store.py` (WAL SQLite schema and helpers)
- Added Polymarket broker adapter:
  - `core/broker_polymarket.py`
- Extended order intent model:
  - `core/broker_base.py` now includes `post_only`, `time_in_force`, `reduce_only`, `quote_group_id`, `idempotency_key`.
- Added dashboard and runtime scripts:
  - `dashboard/app.py`
  - `scripts/run_system.py`
  - `scripts/run_dashboard.py`
  - `scripts/export_runtime_jsonl.py`
  - `scripts/health_check.py`
  - `scripts/start_vps.sh`
  - `ops/systemd/trader.service`
  - `ops/systemd/dashboard.service`
- Added thresholds/config:
  - `config/constitution.yaml`
  - `config/settings.py` new V1 runtime keys
- Added/updated tests:
  - `tests/test_pstar_builder_v1.py`
  - `tests/test_policy_gate_v1.py`
  - `tests/test_execution_fsm_v1.py`
  - `tests/test_sqlite_store_v1.py`

## Breakages Found and Fixed
1. `test_slug_discovery` attempted network access when `clob_candidates=[]` was explicitly provided.
- Root cause: truthy/falsy fallback used `clob_candidates = clob_candidates or ...`.
- Fix: changed to explicit `if clob_candidates is None` in `core/market_discovery.py`.
- Result: offline test determinism restored.

2. `run_system` used private TradeTape internals.
- Root cause: direct access to `trade_tape._intent_event_ids`.
- Fix: runtime now tracks parent event IDs internally (`self._trade_tape_parent_event_ids`) without private attribute coupling.
- Result: cleaner boundary and less fragile replay plumbing.

3. Quote state could become inconsistent after submit/replace reject.
- Root cause: `open_quotes` was updated even when broker rejected.
- Fix: `_apply_side` now only updates quote state on submit/ack success, clears state on reject, and handles post-only retry failure safely.
- Result: better one-source-of-truth for active resting quotes.

4. Decision loop could block on broker I/O in trade mode.
- Root cause: synchronous broker calls inside quote loop.
- Fix: broker submit/cancel/replace now run via `asyncio.to_thread` wrappers.
- Result: quote loop remains responsive under broker/API latency.

5. `run_readonly` still contained old runtime.
- Root cause: script had legacy code path instead of wrapper behavior.
- Fix: replaced with wrapper that forces `--mode OBSERVE` and delegates to `scripts/run_system.py`.
- Result: compatibility entrypoint now matches V1 plan.

6. P* diagnostics too sparse for dashboard/alerts.
- Root cause: missing explicit source prices in diagnostics.
- Fix: added `spot_px`, `perp_px`, `single_source` fields in `core/pstar.py` diagnostics.
- Result: better health tab observability and audit evidence.

7. Direct script execution failed with `ModuleNotFoundError` for repo-local packages.
- Root cause: direct `python scripts/*.py` did not inject repo root into `sys.path`.
- Fix: added path bootstrap to `scripts/run_system.py`, `scripts/run_readonly.py`, `scripts/run_dashboard.py`, and `scripts/export_runtime_jsonl.py`.
- Result: both `python -m ...` and direct script invocation now work.

## Test and Validation Evidence
Commands executed:
- `python3 -m compileall core scripts dashboard`
- `python3 -m unittest -v tests.test_pstar_builder_v1 tests.test_policy_gate_v1 tests.test_execution_fsm_v1 tests.test_sqlite_store_v1`
- `python3 -m unittest discover -s tests -p 'test_*.py'`

Current result:
- Full suite passing: `Ran 143 tests ... OK`
- Known non-fatal warning still present from legacy async test path:
  - `ResourceWarning: unclosed event loop`

## Remaining Logical / Production Gaps
1. Live Polymarket API method contract verification.
- `core/broker_polymarket.py` currently supports multiple method-name fallbacks.
- Need explicit validation against exact installed `py-clob-client` signatures in your runtime image.

2. User/order websocket integration for live fills.
- Runtime currently handles broker events from submit/cancel/replace responses.
- A dedicated authenticated user stream reconciliation path should be wired for robust fill state and cancel/replace races.

3. Emergency unwind policy tuning.
- Implemented maker-first then taker escalation.
- Needs canary tuning for `emergency_taker_after_ms`, retry cadence, and partial-fill behavior under live liquidity.

4. Split/merge inventory operations.
- Feature remains intentionally disabled by default (`CTF_SPLIT_MERGE_ENABLED=false`).
- Onchain reconciliation worker logic still needs fuller implementation before enabling in TRADE.

5. Replay parity from SQLite -> JSONL as daily check.
- Export script exists, but scheduled parity assertions and byte-identical replay CI checks should be added.

6. Latency realism in live mode.
- Instrumentation is present, but threshold tuning needs live baseline from VPS deployment.

## Recommended Next Work Block
1. Run in OBSERVE on VPS continuously and track A-E alert rates for 24-72h.
2. Validate Polymarket broker adapter against real credentials in dry-run/small canary mode.
3. Add user-stream reconciliation and open-order recovery tests.
4. Add deterministic replay parity test from SQLite exports in CI.
5. Enable PAPER mode continuously; compare expected vs realized slippage and one-leg timeout frequency.
