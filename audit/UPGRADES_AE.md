# UPGRADES_AE — Determinism & Causality Engineering Constitution

This document is the implementation contract for upgrades A–E and is the single coordination source for multi-agent delivery.

## Product intent
Build a scientific, auditable market-making/trading system where:
- Every decision is explainable **as-of** a timestamp.
- Replay from identical inputs reproduces identical decisions.
- Trade gating is explicit binary (`ALLOW` or `BLOCK`) with stable reason codes.

## Sacred laws (non-negotiable)
1. **Determinism first**: no hidden randomness, no nondeterministic ordering.
2. **Strict causality**: no computation may consume data newer than `as_of_ts_ms`.

---

## Global rules for every agent
1. No refactors unless required by the upgrade scope.
2. Strict as-of enforcement at computation boundaries.
3. Single time authority for `as_of_ts_ms` (event clock / time mapper).
4. Every derived field must log provenance (raw inputs or computation version).
5. No sampling unless seeded and logged (prefer none).
6. Every change requires replay parity validation.
7. Leakage fails closed: block trading + emit explicit reason code.
8. Schema evolution is additive only.

---

## Shared architecture contracts

## 1) As-of contract
- Every decision tick includes `as_of_ts_ms`.
- Every signal calculator accepts `as_of_ts_ms`.
- Every emitted analytics/gating row includes `as_of_ts_ms`.

## 2) Trade gating contract
Single gating function returns:
- `trade_allowed: bool`
- `block_reasons: list[str]`
- `gate_version: str`

Hard rule: confidence below threshold => `BLOCK` (never “size down” as fallback).

## 3) Versioning contract
Every upgraded component logs explicit version:
- `pstar_version` (e.g. `pstar_v2`)
- `gate_version` (e.g. `gate_v1`)
- `depth_version` (e.g. `depth_v1`)
- `half_life_version` (e.g. `half_life_v2`)

---

## Reason code registry (stable)
- `C_ASOF_VIOLATION`
- `C_CONF_LOW`
- `C_BOOK_STALE`
- `C_SPREAD_WIDE`
- `C_SLIPPAGE_HIGH`
- `C_PSTAR_DISAGREE_HARD`
- `C_HALF_LIFE_SAMPLES_LOW`

Agents may propose additions, but they must be recorded here first.

---

## Multi-agent ownership (A–E + Integrator)

## Agent 0 — Integrator / Traffic Cop
**Owns**
- Branch/merge strategy and interface governance.
- Shared additive schema updates.
- End-to-end replay parity harness.
- DoD enforcement for A–E.

**Mandatory outputs**
- Keep this file (`UPGRADES_AE.md`) current.
- Integration PR wiring A–E behind stable interfaces.
- Replay parity report artifact.

---

## Agent A — As-of Timestamp + Anti-Leakage Guardrails
**Goal**: make time-honesty impossible to bypass.

**Implementation**
- Add `as_of_ts_ms` in all relevant rows/tapes.
- Enforce `ts <= as_of_ts_ms` in buffers/window accessors.
- Leakage detection fails closed with `C_ASOF_VIOLATION`.
- Log evidence payload (`offending_ts_ms`, `as_of_ts_ms`, function).

**Tests**
- Future sample exclusion test.
- Replay parity test for unchanged decisions given identical tape.

---

## Agent B — P* Rule v2 (confidence-scaled spot/perp fusion)
**Goal**: reduce brittle freeze behavior.

**Implementation**
- Compute `disagree_bps = abs(spot - perp)/mid*10000`.
- Derive `p_star_conf` as deterministic monotone function of disagreement + quality.
- Hard freeze only at extreme disagreement/quality failure.
- Log: `p_star`, `p_star_conf`, `spot_px`, `perp_px`, `disagree_bps`, `pstar_version`.

**Tests**
- Table-driven thresholds: low/medium/high disagreement behavior.
- Deterministic rounding consistency tests.

---

## Agent D — Depth-Derived Imbalance Telemetry
**Goal**: depth metrics that reflect execution reality.

**Implementation**
- Depth within N ticks (bid/ask).
- Depth-to-fill notional bands (bid/ask).
- Imbalance for tick band and notional band.
- Strictly compute from as-of snapshot.
- Log `depth_version`.

**Tests**
- Synthetic ladder unit tests.
- Replay determinism tests for identical snapshots.

---

## Agent E — Arb Half-Life Shock via Quantiles
**Goal**: deterministic shock detection with sample guarantees.

**Implementation**
- Replace static threshold with rolling as-of quantile shock definition.
- Fixed lookback window ending at `as_of_ts_ms`.
- Deterministic quantile method and tie handling.
- Insufficient samples => explicit conservative status/reason.
- Log: `half_life_value`, `half_life_quantile`, `half_life_window_n`, `shock_flag`, `half_life_version`.

**Tests**
- Hand-computable quantile unit tests.
- Insufficient sample behavior tests.

---

## Agent C — Centralized Confidence + Trade Gating
**Goal**: one binary gate; no scattered allow/deny logic.

**Implementation**
- Build a single gate function using A/B/D/E outputs.
- Outputs only `trade_allowed` + `block_reasons` (+ summary metrics).
- Missing critical data should default to `BLOCK`.
- Log: `trade_allowed`, `block_reasons`, `confidence_value`, `confidence_threshold`, `gate_version`.

**Tests**
- Reason-code trigger tests.
- Integration test proving low confidence emits no orders and explicit block reason.

---

## Merge order (conflict-minimizing)
1. Agent A (time/as-of base contract)
2. Agent B (pstar v2 on as-of foundation)
3. Agent D (depth telemetry)
4. Agent E (half-life quantile shock)
5. Agent C (central gate consumes all prior outputs)
6. Agent 0 integration + replay parity certification

---

## Definition of Done (PR acceptance checklist)
- [ ] All new derived fields include `as_of_ts_ms`.
- [ ] No function reads newer-than-as-of data (with tests).
- [ ] Decision outputs include: `p_star`, `p_star_conf`, `trade_allowed`, `block_reasons`, depth telemetry, half-life shock fields.
- [ ] Trade gating is centralized and binary.
- [ ] Replay parity passes with deterministic output.
- [ ] Reason codes are stable and documented in this file.

---

## Evidence bundle recommendation (next layer)
After A–E, add per-decision Evidence Bundle artifact:
- raw inputs snapshot
- derived computations + versions
- gate verdict and reason codes
- output actions

This enables scientist-style run-to-run diffing and forensic replay.
