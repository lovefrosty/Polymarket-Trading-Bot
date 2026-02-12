# Complete Repo Audit — Pipeline, Latency Readiness, and Unused Code (2026-02-12)

## 0) Executive Summary

### Current project state (short answer)
- **You are not blocked by missing core architecture.** The main pipeline is present end-to-end: Polymarket WS -> reference fusion -> decision/policy -> broker actions -> Polymarket broker adapter.
- **Your biggest gap vs your stated goal is source parity:** Kraken **spot** is integrated; Kraken **perp** is **not** integrated as a reference source today.
- **Your data retention path is strong:** event/decision/trade JSONL tapes + runtime SQLite metrics tables are already wired for analysis and strategy research.
- **There is some real fluff/dead surface** (legacy/compat modules + at least one dead DB table) that should be trimmed so effort stays focused.

### Go-live readiness for your "$5 to <$50 live market-making" target
- Technically feasible with current architecture, **but not ready to flip on immediately** without a dedicated micro-cap profile and explicit live-risk bounds.
- This should be done in phases: OBSERVE -> PAPER -> TRADE with tiny size and hard kill-switches.

---

## 1) What was audited

- Runtime orchestration: `scripts/run_system.py`, `scripts/run_readonly.py`.
- Ingestion/feeds: `data/polymarket_ws.py`, `core/reference_feed.py`, `core/reference_ws.py`, `core/reference_adapters.py`.
- Strategy/decision chain: `core/pstar.py`, `core/reference_price.py`, policy + execution paths in `scripts/run_system.py`.
- Persistence: `core/event_tape.py`, `core/decision_tape.py`, `core/trade_tape.py`, `core/sqlite_store.py`.
- Legacy/unused surface: repo-wide static scan for disconnected modules and schema objects.

---

## 2) Dual-track pipeline audit (your requested flow)

## A) WebSocket market track (Polymarket)
**Status: ✅ Wired and active.**
- Market WS client is instantiated and started in runtime task loop.
- Incoming market messages update order books and write to `EventTape` channelized JSONL.
- Runtime health/liveness checks are connected (starvation, sequence/order checks, freshness logic).

**Result:** The "Polymarket WS -> local state" side of the dual-track is present.

## B) Reference track (spot + perp)
**Status: 🟡 Partially aligned with your exact requirement.**
- Runtime supports multi-source reference ingestion.
- Available sources in live runtime path:
  - `poll_coinbase` (spot)
  - `poll_kraken` (spot)
  - `ws_kraken` (spot)
  - `poll_binance_perp` (perp)
- `PStarBuilder` is configured to combine **spot + perp** and handles source staleness/disagreement logic.

**Gap relative to your specific ask:**
- You asked for **Kraken spot + Kraken perp**; today perp comes from **Binance perp polling**, not Kraken perp.
- Kraken WS implementation in repo is ticker/spot path; no Kraken perp adapter currently registered in runtime source map.

## C) Persistence and analytics availability
**Status: ✅ Good.**
- Event-level data is persisted as JSONL by channel (`market_*.jsonl`, `reference_*.jsonl`, `onchain_*.jsonl`).
- Decision and trade lifecycles are persisted as `decision_*.jsonl` and `trade_*.jsonl`.
- Runtime SQLite stores high-value analysis tables (`decisions`, `orders`, `fills`, `execution_quality`, `latency_stats`, `pstar_stats`, `decision_ticks`, etc.).

**Result:** You can already extract data for statistics, research, and strategy iteration from both tapes and DB.

## D) Strategy/signal -> trade action -> Polymarket
**Status: ✅ Implemented, with mode gates.**
- Reference quotes flow into runtime and pstar.
- Quote cycle computes policy verdicts and writes decision records.
- In `PAPER`/`TRADE`, broker submit/cancel/fill handling is live in orchestration.
- In `OBSERVE`, order actions are intentionally blocked.

**Result:** The signal-to-order-to-broker chain exists; live execution depends on mode + credentials + safety gates.

---

## 3) Unused / low-purpose code findings

## High-confidence unused or low-value
1. **Dead SQLite table: `market_trades`**
   - Exists in schema but no active write/read path in runtime modules.
   - This is concrete "fluff" that can be removed or repurposed.

2. **Legacy `src/` tree not used by production runtime**
   - Static scan found no imports from `src.*` in non-test runtime modules.
   - This surface appears compatibility/legacy, mostly useful for tests or historical interface continuity.

3. **`core/execution_runner.py` appears disconnected**
   - Defines `ExecutionRunner` and gate helpers but is not imported by runtime entrypoints.

4. **`core/dry_run.py` appears disconnected**
   - Dry-run classes exist without active wiring in current run-system path.

## Medium-confidence cleanup candidates (verify before removal)
- `core/calibration.py` functions (`fit_platt`, `apply_platt`, `calibration_report`) appear superseded by other model-path logic.
- Parts of backtest/scientific-method helpers may be experimental leftovers; keep if currently used in research workflows.

---

## 4) Latency + operational readiness assessment

## What is already good for lower-latency operation
- WS-first market ingestion path.
- Continuous freshness/staleness gating.
- Timing and causality fields captured in tapes/DB.
- Runtime latency telemetry tables (`latency_stats`, execution attribution and queue quality).

## Current bottlenecks / risks for your real-time debugging goal
1. **Reference perp path via polling** can become stale relative to WS market data under volatility.
2. **No Kraken perp parity** means your intended venue-consistent reference is incomplete.
3. **No dedicated micro-capital live profile** (explicitly tuned for $5-$50 operation).
4. **Codebase contains legacy branches**, increasing cognitive load and making true bottlenecks harder to spot.

---

## 5) Direct answer: can this system run tiny live market-making now?

**Answer: almost, but not safely "as-is" for your target process.**
- Infrastructure exists to do it.
- You still need a **tight live profile** and source alignment before turning on continuous real-time market making.

### Must-have before enabling sustained TRADE mode
1. Add/enable exact reference stack you want (Kraken spot + Kraken perp, or consciously accept mixed-venue spot/perp).
2. Create `small_capital_debug` config profile with hard caps:
   - bankroll cap and per-order notional cap,
   - max daily notional and max daily loss,
   - conservative quote size and spread settings,
   - strict rate limits and emergency taker constraints.
3. Run staged validation:
   - 24h OBSERVE soak,
   - PAPER validation with promotion gates,
   - then TRADE with smallest allowable order size.
4. Remove/label dead modules so team focus remains on production path only.

---

## 6) Process recommendation for project management (what to change)

To avoid "adding more but not what matters," switch planning to a **production-lane-only Kanban**:

1. **Lane A: Data correctness**
   - WS completeness, reference completeness, persistence integrity checks.
2. **Lane B: Decision quality**
   - calibration, policy gates, false-positive reduction.
3. **Lane C: Execution safety**
   - throttles, reconciliation, risk and failure handling.
4. **Lane D: Cleanup debt**
   - remove/retire modules not on live path.

Use a strict rule: **no feature merges unless mapped to one live-path KPI**
(latency, fill quality, edge retention, risk breach rate, or uptime).

---

## 7) Recommended implementation order (highest ROI)

1. **Reference parity decision:** implement Kraken perp adapter OR codify mixed-venue policy.
2. **Micro-cap live profile:** explicit "$5-$50" risk and sizing config.
3. **Automated export bundle for run analytics:** one command to join market/reference/decision/trade artifacts for analysis.
4. **Dead-surface cleanup pass:** remove `market_trades`; quarantine or remove disconnected legacy modules.
5. **Promotion checklist wiring:** automated OBSERVE->PAPER->TRADE gating with objective thresholds.

---

## 8) Evidence commands executed during audit

- `pytest -q tests/test_reference_price.py tests/test_execution_submit.py tests/test_run_system_observe_live_mode.py`
- `rg -n "kraken|perp|ws_kraken|poll_binance_perp|reference_source" core scripts README.md config`
- `rg -n "market_trades" core scripts dashboard`
- static Python scan confirming no non-test imports from `src.*` in production runtime files

