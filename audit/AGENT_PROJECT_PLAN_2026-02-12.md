# Agent Delivery Plan — Large Projects to Build Next

This plan translates the audit findings into **independent workstreams** that can be owned by separate agents with minimal overlap.

## Program objective
Build a production-focused Polymarket market-making stack that:
1. Has complete dual-track data ingestion (market WS + reference spot/perp),
2. Preserves high-quality analytics data for strategy research,
3. Converts signals into safe, low-notional live actions,
4. Removes legacy/dead code that slows delivery.

## Phase-1 Live Safety Defaults
- `quote_interval_ms=2000`
- `max_orders_per_min=30`
- `max_daily_loss_usdc=50`
- `cap_gross_usd=200`
- `cap_total_gross_usd=400`
- `max_position_per_side=500`
- `book_stale_after_ms=30000`

## Promotion Runbook (Mandatory)
1. OBSERVE soak minimum: 48 hours.
2. PAPER soak minimum: 48 hours.
3. Replay certification must return `PASS` (`scripts/replay_certify.py`).
4. Unified promotion verdict must return `PROMOTE` (`scripts/promotion_report.py`).
5. Any runtime fingerprint change resets soak windows.

## Secret Handling Policy (Mandatory)
- Keys and secrets are loaded from environment variables only.
- Secrets must never be written to tapes, SQLite payloads, logs, or evidence artifacts.
- Secrets must never be committed to repository files.

---

## Project 1 — Reference Parity: Kraken Spot + Kraken Perp

### Why this project exists
You requested Kraken spot and Kraken perp parity. Current runtime has Kraken spot + Binance perp fallback.

### Scope
- Add Kraken perp adapter(s) for polling and/or WS.
- Add source configuration wiring in runtime (`--reference_source` and defaults).
- Ensure pstar/reference fusion cleanly handles Kraken spot+perp pair.
- Add telemetry fields to clearly indicate source provenance per tick.

### Deliverables
- `core/reference_adapters.py` and/or `core/reference_ws.py` support for Kraken perp.
- `core/reference_feed.py` source registration and validation.
- Runtime docs and examples for Kraken-only and mixed-source modes.
- Tests for parsing, staleness, disagreement, and fallback behavior.

### Acceptance criteria
- Operator can run `--reference_source ws_kraken_spot,ws_kraken_perp` (or equivalent final names).
- pstar remains valid with expected confidence under healthy feeds.
- Replay and runtime tests pass with deterministic behavior.

### Recommended owner
**Agent A: Market Data Integrations**

---

## Project 2 — Small-Capital Live Trading Profile ($5–$50)

### Why this project exists
Current configuration is not explicitly tuned for tiny live capital debug operation.

### Scope
- Create a strict `small_capital_debug` profile with low exposure and hard limits.
- Add profile-specific caps for:
  - per-order notional,
  - max open notional,
  - max daily notional,
  - max daily loss,
  - order/cancel rate limits,
  - emergency freeze and kill-switch thresholds.
- Provide one-command launch templates for OBSERVE/PAPER/TRADE using this profile.

### Deliverables
- Config profile files and/or profile selector in runtime.
- Guardrail tests (loss, notional, rate-limit, freeze paths).
- Operator runbook for stepping from OBSERVE -> PAPER -> TRADE.

### Acceptance criteria
- With profile enabled, system cannot exceed configured capital bounds.
- Kill-switches trip deterministically under breach tests.
- Trade mode can place minimal-size quotes while respecting caps.

### Recommended owner
**Agent B: Execution Risk & Safety**

---

## Project 3 — Research-Grade Data Export + Analysis Bundle

### Why this project exists
Data exists in tapes and SQLite, but strategy iteration needs a clean, repeatable export artifact.

### Scope
- Build a unified export job that joins:
  - market/reference events,
  - decision records,
  - orders/fills,
  - latency/quality stats,
  into analysis-ready tables.
- Produce manifest + schema version + run metadata.
- Support reproducible backtest/research ingest.

### Deliverables
- `scripts/export_*` enhancement or new single-entry export script.
- Output formats: JSONL/CSV/Parquet partitions for downstream notebooks.
- Data quality checks (row counts, key integrity, timestamp sanity).

### Acceptance criteria
- One command produces an analysis bundle per run-id.
- Bundle includes all joins needed to study signal->execution outcomes.
- Export contract documented and tested.

### Recommended owner
**Agent C: Data Platform / Quant Research Infra**

---

## Project 4 — Production Path Cleanup (Remove Fluff / Dead Surface)

### Why this project exists
Legacy and disconnected modules increase maintenance load and hide true runtime path.

### Scope
- Remove/repurpose dead objects (e.g., unused `market_trades` table).
- Decide fate of legacy `src/` tree: deprecate, archive, or keep as explicit compatibility layer.
- Evaluate disconnected modules (`core/execution_runner.py`, `core/dry_run.py`, etc.) and either wire or retire.
- Add CI guard to detect newly orphaned modules/tables.

### Deliverables
- Cleanup PR set with migration notes.
- Updated audit/static-check script with allowlist for intentional compatibility shims.
- Documentation of canonical runtime path.

### Acceptance criteria
- No ambiguous duplicate paths for production-critical logic.
- Orphan/dead-surface report trend decreases release-over-release.
- CI fails on introduction of untracked dead surfaces.

### Recommended owner
**Agent D: Code Health / Architecture**

---

## Project 5 — Promotion Gates for Live Readiness (OBSERVE -> PAPER -> TRADE)

### Why this project exists
You want to debug continuously while moving toward live market-making safely.

### Scope
- Formalize stage-gate thresholds for each mode transition:
  - feed health,
  - data completeness,
  - latency quantiles,
  - reconciliation mismatch rates,
  - execution quality bounds.
- Automate gate checks and generate promotion reports.

### Deliverables
- Configurable gate definitions.
- Gate checker integrated in scripts/CI/ops workflow.
- Promotion report artifact with pass/fail reasons.

### Acceptance criteria
- No mode promotion without measurable criteria.
- Reports are reproducible and archived by run-id/time window.
- Operators can clearly identify blockers before enabling TRADE.

### Recommended owner
**Agent E: Reliability / SRE + Trading Ops**

---

## Project 6 — Live Market-Making Strategy Baseline (Minimal Viable Strategy)

### Why this project exists
Pipeline exists, but you need a practical, constrained strategy that turns signals into quote actions.

### Scope
- Define a minimal baseline strategy policy for tiny notional trading.
- Tighten action logic around spread/depth/toxicity/latency gates.
- Add post-trade attribution and iterative tuning loop.

### Deliverables
- Baseline strategy config and decision policy thresholds.
- Controlled quote-placement behavior under low balance.
- KPI dashboard views: fill rate, adverse selection, net edge after fees.

### Acceptance criteria
- Strategy runs continuously in PAPER and then TRADE at tiny size.
- Negative-tail risk stays within configured daily loss cap.
- Execution quality metrics are visible and actionable.

### Recommended owner
**Agent F: Strategy & Microstructure**

---

## Cross-project sequence (recommended)
1. **P1 Reference Parity** and **P2 Small-Capital Profile** (parallel).
2. **P5 Promotion Gates** (early, to enforce safe rollout behavior).
3. **P3 Data Export Bundle** (for faster learning loops).
4. **P6 Strategy Baseline** (deploy once data+gates are stable).
5. **P4 Cleanup** continuously, with major prune after first stable live cycle.

---

## Agent handoff template (use per project)
- Objective:
- In-scope:
- Out-of-scope:
- Inputs/dependencies:
- Implementation files likely touched:
- Tests required:
- Rollback plan:
- Acceptance checklist:
- Evidence artifacts to attach:



## Governance note
- Canonical cross-agent contract for A–E is maintained in `audit/UPGRADES_AE.md`.
