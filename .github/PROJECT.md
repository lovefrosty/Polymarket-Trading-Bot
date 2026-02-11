# Polymarket Terminal - Live Readiness Tracker

Last updated: 2026-02-11

## Program Objective
Reach safe TRADE readiness with zero silent failures, deterministic observability, and operator-grade explainability.

Build order is locked:
1. Runtime reliability and ingestion hardening
2. Dashboard modularization + explainability
3. Data/replay/export diagnostics
4. Conservative promotion pipeline (OBSERVE -> PAPER -> TRADE)

## Milestone Status (G0-G7)

| Milestone | Status | Scope | Evidence |
|---|---|---|---|
| G0 | Done | Baseline + program control | `audit/milestones/G0_baseline.md` |
| G1 | Done | On-chain + discovery hardening | `audit/milestones/G1_onchain_discovery_hardening.md` |
| G2 | Done | Canonical evidence contract + source guards | `audit/milestones/G2_evidence_contract_source_guards.md` |
| G3 | Done | Dashboard monolith split into panel modules | `audit/milestones/G3_dashboard_modularization.md` |
| G4 | Done | Operator explainability + incident export v0 | `audit/milestones/G4_explainability_export_v0.md` |
| G5 | Done | Replay/live diff v0 + reliability scoreboard | `audit/milestones/G5_replay_diff_reliability_scoreboard.md` |
| G6 | Pending | Data pipeline completion + expanded walk-forward | pending |
| G7 | Pending | Promotion pipeline + soak gates | pending |

## Locked Safety Thresholds (Constitution)
Source: `config/constitution.yaml`

- `policy.pstar_max_age_ms = 3000`
- `policy.pstar_freeze_disagree_bps = 50.0`
- `policy.book_stale_after_ms = 30000`
- `policy.book_down_after_ms = 120000`
- `policy.signal_age_max_ms = 1200`
- `policy.ack_p95_max_ms = 400.0`
- `policy.ws_lag_max_ms = 1000.0`

## Promotion Policy (Conservative)
- OBSERVE gate: 7d stability, no unresolved A/B causality violations.
- PAPER gate: 10-14d stability, replay diagnostics green, reconciliation stable.
- TRADE gate: low-cap canary, maker-first, emergency unwind enabled, kill-switch armed.

## Current Workstream (G6-G7)
- Complete dataset/export/label/walk-forward expanded metrics path.
- Execute conservative promotion pipeline and soak gates.

## Blockers That Halt Autonomous Progress
- Missing or invalid exchange credentials.
- Requested changes to locked promotion thresholds.
- Unexplained nondeterminism that invalidates replay diagnostics.
