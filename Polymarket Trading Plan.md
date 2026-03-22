# Polymarket V1 Implementation Plan: Automated Maker Trading, Causality-Safe, SQLite-Canonical

## Summary
- Build a single Polymarket-only V1 system in the existing `core/` runtime with live automated connection and trade placement.
- Use official Polymarket CLOB client for execution, dual-loop architecture (async ingestion + sync decision loop), maker-first quoting, and emergency taker unwind after timeout.
- Make SQLite the runtime source of truth; export JSONL tapes as derived artifacts for replay/backward compatibility.
- Enforce Failures A–E as hard policy gates before any quote/place/replace/cancel action.
- Ship a dark-theme Streamlit dashboard reading SQLite with A–E red alerts and execution realism telemetry.

## Important Public API and Interface Changes
1. New runtime abstractions.
- Add `BookSnapshot` and `BookCache` in `/Users/padraigjudge/Desktop/Polymarket Bot/core/book_cache.py`.
- Add `PStar` and `PStarBuilder` in `/Users/padraigjudge/Desktop/Polymarket Bot/core/pstar.py`.
- Add `PolicyGate` in `/Users/padraigjudge/Desktop/Polymarket Bot/core/policy_gate.py`.
- Add `ExecutionFSM` in `/Users/padraigjudge/Desktop/Polymarket Bot/core/execution_fsm.py`.
- Add `PolymarketBroker` in `/Users/padraigjudge/Desktop/Polymarket Bot/core/broker_polymarket.py`.
- Add `SQLiteStore` in `/Users/padraigjudge/Desktop/Polymarket Bot/core/sqlite_store.py`.

2. Existing interface extensions.
- Extend `/Users/padraigjudge/Desktop/Polymarket Bot/core/broker_base.py` with explicit post-only fields: `post_only`, `time_in_force`, `client_order_id`, `reduce_only`, `quote_group_id`.
- Extend `/Users/padraigjudge/Desktop/Polymarket Bot/core/execution_runner.py` to consume quote-intents and lifecycle events (`submit`, `ack`, `fill`, `cancel`, `replace`, `reject`) from live broker.
- Extend `/Users/padraigjudge/Desktop/Polymarket Bot/core/decision_tape.py` and `/Users/padraigjudge/Desktop/Polymarket Bot/core/trade_tape.py` with schema versions that include `as_of_ts_ms`, `pstar_diag`, `policy_codes`, latency fields, and FSM state.

3. New CLI/runtime entrypoints.
- Add `/Users/padraigjudge/Desktop/Polymarket Bot/scripts/run_system.py` as canonical runner with modes `OBSERVE|PAPER|TRADE`.
- Keep `/Users/padraigjudge/Desktop/Polymarket Bot/scripts/run_readonly.py` as compatibility wrapper to `OBSERVE`.
- Add `/Users/padraigjudge/Desktop/Polymarket Bot/scripts/run_dashboard.py` for Streamlit startup.

## Data Model and Canonical Storage
1. SQLite is canonical runtime storage in `runtime.db` (WAL mode).
- Required tables: `market_data_book`, `market_trades`, `pstar`, `decisions`, `orders`, `fills`, `exec_latency`, `alerts`, `logs`, `inventory`, `system_state`, `latency_stats`, `pstar_stats`.
- All rows include `ts_ms` and stable IDs (`decision_id`, `order_id`, `event_id`) for deterministic replay mapping.
- All decision rows include `decision_ts_event_ms`, `book_asof_ts_ms`, `pstar_asof_ts_ms`, `max_feature_ts_ms`.

2. JSONL export is derived, not primary.
- Keep export writers for `market_*.jsonl`, `reference_*.jsonl`, `decision_*.jsonl`, `trade_*.jsonl`.
- Export job reads committed SQLite rows and writes deterministic sorted JSONL for replay/audit compatibility.

## Implementation Steps (Decision-Complete)
1. Dependency and config foundation.
- Update `/Users/padraigjudge/Desktop/Polymarket Bot/requirements.txt` to include official Polymarket CLOB client, `websockets`, `streamlit`, and SQLite-safe helpers only.
- Update `/Users/padraigjudge/Desktop/Polymarket Bot/config/settings.py` with explicit keys for:
`TRADING_MODE`, `POLYMARKET_API_KEY`, `POLYMARKET_SECRET`, `POLYMARKET_PASSPHRASE`, `POLYMARKET_PRIVATE_KEY`, `POST_ONLY_ENABLED`, `QUOTE_INTERVAL_MS`, `REBALANCE_TIMEOUT_MS`, `EMERGENCY_TAKER_ENABLED`, `PSTAR_MAX_AGE_MS`, `PSTAR_FREEZE_DISAGREE_BPS`.
- Add `/Users/padraigjudge/Desktop/Polymarket Bot/config/constitution.yaml` as single threshold source.

2. Async ingestion loop.
- Reuse `/Users/padraigjudge/Desktop/Polymarket Bot/data/polymarket_ws.py` parsing paths but route updates through new `BookCache` atomic snapshots.
- Keep reconnect backoff and explicit stale markers.
- Add WS lag telemetry per market in `exec_latency` and `latency_stats`.

3. Reference and P* validity.
- Build `PStarBuilder` using spot/perp snapshots with deterministic scoring.
- Hard gates:
`missing source`, `age > max_age`, `extreme disagreement`, `symbol mismatch`.
- Degraded mode allowed only when configured and logged with lower confidence.
- Every decision persists complete `PStar.diagnostics`.

4. Sync decision loop.
- Cadence default: `1000ms` quoting loop, separate `5000ms` stats rollup loop.
- Read immutable snapshots from `BookCache` + `PStar`.
- Build minimal feature set and optional skew bias.
- Apply `PolicyGate` A–E before generating quote actions.
- Produce `QUOTE|SKIP|FREEZE` decision with structured reason codes.

5. Maker quoting and FSM.
- FSM states: `FROZEN`, `QUOTING_BOTH`, `ONE_SIDE_FILLED`, `REBALANCING`, `UNWINDING`.
- Quote logic:
compute fair `q`, derive bid/ask around `q`, apply inventory skew, risk padding, spread/depth gates, round to tick.
- Post-only GTC only for normal operation.
- On post-only reject, deterministically reprice one tick away and retry once.
- If one-leg exposure persists past `REBALANCE_TIMEOUT_MS`, trigger emergency unwind.
- Emergency unwind policy: maker-first attempt, then taker cross if timeout still breached and `EMERGENCY_TAKER_ENABLED=true`.

6. Live Polymarket execution.
- Implement `PolymarketBroker` for submit/cancel/replace/order-status polling fallback + WS user updates.
- Map broker events to normalized internal events and persist to SQLite `orders`/`fills`.
- Add idempotency keys for every order intent.
- Never block decision loop on broker I/O; broker operations run async with bounded queue/backpressure.

7. Split/merge inventory integration.
- Implement split/merge module behind feature flag `CTF_SPLIT_MERGE_ENABLED=false` by default.
- Add reconciliation worker comparing broker inventory vs on-chain-confirmed inventory with lag-aware status.
- Dashboard shows both views and mismatch alerts.
- TRADE rollout starts with split/merge disabled; enable after canary checks.

8. Dashboard (dark theme, SQLite-backed).
- Add `/Users/padraigjudge/Desktop/Polymarket Bot/dashboard/app.py`.
- Tabs: `Overview`, `Health(A–E)`, `Inventory & Quotes`, `Statistics`, `Logs`.
- Red banner if frozen or any critical gate threshold breached.
- Refresh target: 0.5–2s, no trading logic in UI.

9. Runtime automation.
- Add `/Users/padraigjudge/Desktop/Polymarket Bot/ops/systemd/trader.service` and `/Users/padraigjudge/Desktop/Polymarket Bot/ops/systemd/dashboard.service`.
- Add startup script `/Users/padraigjudge/Desktop/Polymarket Bot/scripts/start_vps.sh` for automated boot and health checks.
- Add health endpoint script `/Users/padraigjudge/Desktop/Polymarket Bot/scripts/health_check.py` for process and DB liveness.

## Test Cases and Scenarios
1. A–E unit tests.
- `A`: invalid/missing/stale/disagreeing P* => no quote placement.
- `B`: any feature/book/pstar timestamp at or after decision timestamp => hard reject.
- `C`: insufficient depth, high spread, high expected slippage => skip/freeze.
- `D`: one-side fill then rebalance timeout => unwind path executed within SLA.
- `E`: high signal age, high ack p95, high ws lag => reject new quotes.

2. Execution behavior tests.
- Post-only reject handling reprices correctly.
- Cancel/replace race conditions preserve single active quote per side.
- Broker reconnect restores open-order state without duplicate intents.
- Emergency taker unwind only fires under configured timeout conditions.

3. Replay determinism tests.
- Same SQLite event stream produces byte-identical derived JSONL exports.
- Replay assertions verify strict causal ordering on each decision.
- Window-edge tests around `14:59:58 -> 15:00:02` and 15m boundary resets.

4. Dashboard and observability tests.
- Red alerts appear for each A–E trigger.
- Metrics update latency <= 2s from DB commit.
- Inventory mismatch indicator fires when on-chain and broker views diverge.

## Rollout and Acceptance Criteria
1. OBSERVE.
- Live feeds + decisions only, no order placement.
- Must run continuously with zero causal violations for 7 days.

2. PAPER.
- Simulated fills through existing sim broker + live feeds.
- Must pass A–E test suite and replay parity daily.

3. TRADE (Polymarket only).
- Start with low size caps and maker-only quoting.
- Enable emergency taker unwind from day one.
- Split/merge remains disabled until post-launch canary metrics pass.

4. Canary exit criteria.
- `P* invalid` rate below configured threshold.
- Ack latency p95 under threshold.
- One-leg timeout events below kill-switch threshold.
- No unresolved inventory mismatches.

## Assumptions and Defaults (Locked)
- Venue scope: Polymarket only for V1 live execution.
- SDK: official Polymarket CLOB client for order placement/cancel/replace.
- Unwind policy: maker-first then taker emergency on timeout.
- Inventory ops: split/merge implemented but feature-flagged off by default.
- Canonical store: SQLite (`runtime.db`), JSONL export derived.
- Default loop cadences: quoting 1s, stats 5s.
- Default red lines:
`PSTAR_MAX_AGE_MS=3000`,
`PSTAR_FREEZE_DISAGREE_BPS=50`,
`SIGNAL_AGE_MAX_MS=1200`,
`ACK_P95_MAX_MS=400`,
`WS_LAG_MAX_MS=1000`,
`REBALANCE_TIMEOUT_MS=5000`.
