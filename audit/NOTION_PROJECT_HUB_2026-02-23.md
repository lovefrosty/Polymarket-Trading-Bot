# Polymarket Bot — Notion Project Hub (Constitution-Driven)

## 1) Operating Objective
Ship a production-safe **internal operator** trading product that can be promoted from OBSERVE -> PAPER -> TRADE only when constitution gates pass.

Primary success condition:
- `scripts/promotion_report.py` returns `status = PROMOTE` using **fresh** OBSERVE/PAPER artifacts.

Non-negotiables:
- Determinism and replay parity
- Strict causality (`as_of` discipline)
- Fail-closed gates
- Mode safety boundaries
- Complete audit trail

---

## 2) Current State Snapshot (as of 2026-02-23)
### Completed implementation tracks
1. Runtime determinism hardening and float quantization at decision boundaries.
2. WS determinism/freshness hardening and diagnostics stabilization.
3. Decision contract + replay certifier tooling.
4. Promotion report with soak + gate aggregation.
5. Micro-cap defaults + secret hygiene documentation.

### Verified gates
- Test suite: `312 passed, 5 subtests passed`.
- Repo audit command works: `python3 -m scripts.repo_audit --trigger "run audit"`.
- Promotion report command works and currently returns HOLD on this worktree (expected without fresh runtime evidence).

### Active blockers (operational, not architecture)
1. Missing fresh runtime evidence tables for promotion checks.
2. Replay certification artifact not yet provided for current promotion decision.
3. Soak windows not yet satisfied from fresh runs.

---

## 3) Pinned Runtime Profile (Micro-Cap)
Source of truth:
- `/Users/padraigjudge/Desktop/Polymarket Bot/config/constitution.yaml`
- `/Users/padraigjudge/Desktop/Polymarket Bot/config/profiles/micro_cap_live.yaml`

Pinned values:
- `quote_interval_ms = 2000`
- `max_orders_per_min = 30`
- `max_daily_loss_usdc = 50`
- `cap_gross_usd = 200`
- `cap_total_gross_usd = 400`
- `book_stale_after_ms = 30000`
- `max_position_per_side = 500`

---

## 4) Promotion Constitution (Operator Checklist)
Required to promote:
1. OBSERVE soak >= 48h
2. PAPER soak >= 48h
3. Replay certification PASS (Tier-1 equality)
4. Integration health PASS
5. Promotion gates PASS
6. `promotion_report` verdict = PROMOTE

Reset rule:
- Any runtime code change affecting decision/execution resets the relevant soak clock.

Mandatory commands:
```bash
.venv/bin/pytest -q
python3 -m scripts.repo_audit --trigger "run audit"
python3 -m scripts/replay_certify --left <run_a_decisions> --right <run_b_decisions> --output <replay_report.json>
python3 -m scripts/promotion_report --current-mode PAPER --replay-report <replay_report.json>
```

---

## 5) Notion Workspace Structure (Recommended)
Create one parent page: **Polymarket Bot — Program Control**

Child pages/databases:
1. **Mission & Constitution**
2. **Promotion Control Board** (database)
3. **Workstream Backlog** (database)
4. **Run Evidence Ledger** (database)
5. **Risk Register** (database)
6. **Weekly Ops Review**
7. **Decision Log**

---

## 6) Notion Database Schemas
### A) Promotion Control Board
Purpose: single source for release readiness.

Properties:
- `Environment` (Select: OBSERVE, PAPER, TRADE)
- `Status` (Select: HOLD, READY, PROMOTE)
- `Observe Soak Hours` (Number)
- `Paper Soak Hours` (Number)
- `Replay Certify` (Select: PASS, FAIL, MISSING)
- `Integration Health` (Select: PASS, FAIL)
- `Promotion Gates` (Select: PASS, FAIL)
- `Blocking Reasons` (Multi-select)
- `Promotion Report Artifact` (URL/Text)
- `Runtime Fingerprint` (Text)
- `Last Verified At` (Date)
- `Owner` (Person/Text)

Views:
- `Release View` (table; sorted by Last Verified At desc)
- `Blockers Only` (filter Status != PROMOTE)
- `By Environment` (board grouped by Environment)

### B) Workstream Backlog
Purpose: execution queue tied to constitution risk.

Properties:
- `Title` (Title)
- `Stream` (Select: Determinism, Causality, Execution Safety, Ops, Dashboard, Docs)
- `Severity` (Select: High, Medium, Low)
- `Type` (Select: Bug, Hardening, Test, Tooling, Runbook)
- `Status` (Select: Todo, In Progress, Blocked, Done)
- `Owner` (Person/Text)
- `File Targets` (Text)
- `Acceptance Criteria` (Text)
- `Regression Risk` (Select: High, Medium, Low)
- `Linked Evidence` (Relation/URL)
- `Linked PR` (URL/Text)

Views:
- `High Severity First`
- `Blocked Items`
- `By Stream`

### C) Run Evidence Ledger
Purpose: evidence index for promotion decisions.

Properties:
- `Run ID` (Title)
- `Mode` (Select: OBSERVE, PAPER, TRADE)
- `Start Time` (Date)
- `End Time` (Date)
- `Duration Hours` (Formula/Number)
- `DB Artifact Path` (Text)
- `Decision Tape Path` (Text)
- `Replay Pair Group` (Text)
- `Replay Certify Result` (Select: PASS, FAIL, NA)
- `Promotion Report` (URL/Text)
- `Notes` (Text)

Views:
- `Latest Runs`
- `Promotion-Candidate Runs`

### D) Risk Register
Purpose: ranked risk management with closure criteria.

Properties:
- `Risk` (Title)
- `Severity` (Select: High, Medium, Low)
- `Likelihood` (Select: High, Medium, Low)
- `Impact` (Text)
- `Evidence` (Text/URL)
- `Mitigation` (Text)
- `Owner` (Person/Text)
- `Status` (Select: Open, Monitoring, Mitigated)
- `Exit Criteria` (Text)

---

## 7) Program Cadence (Minimal, High-Discipline)
### Daily (15 min)
1. Update Promotion Control Board from latest `promotion_report`.
2. Review new blockers and assign single owner + due date.
3. Verify no constitution violation merged.

### Twice-weekly (30 min)
1. Replay parity status review.
2. Gate health trend review.
3. Risk register reprioritization.

### Weekly (45 min)
1. Decide: hold, promote, or rollback plan.
2. Freeze next week’s top 3 changes only.

---

## 8) First Product Ship Definition
Product = **operator-managed constrained trading system** with:
- Deterministic Tier-1 replay behavior
- Explicit gate decisions and reason codes
- Promotion ladder with objective PASS/FAIL artifacts
- Micro-cap risk envelope enforced

Not included in first ship:
- Throughput arms race
- Multi-market expansion
- Aggressive alpha expansion

---

## 9) Linear Usage Recommendation
Short answer: **Yes, use Linear now** for execution tracking; keep Notion as the control tower.

Best split:
- **Notion:** strategy, constitution, promotion board, evidence ledger, executive state.
- **Linear:** actionable implementation tickets, assignment, cycle planning, throughput.

Recommended Linear project setup:
1. Project: `Polymarket Bot - Promotion to TRADE`
2. Team labels:
- `determinism`
- `causality`
- `mode-safety`
- `promotion-gates`
- `ops-evidence`
3. Issue template fields:
- file targets
- acceptance criteria
- regression risk
- linked evidence artifact
4. Workflow:
- Todo -> In Progress -> In Review -> Done
- Separate status for `Blocked` with blocker code required.

Suggested initial Linear epics:
1. `Fresh OBSERVE evidence pack (48h)`
2. `Fresh PAPER evidence pack (48h)`
3. `Replay pair certification pack`
4. `Promotion report to PROMOTE`

---

## 10) Immediate Next Actions
1. Start fresh OBSERVE run and create corresponding row in Run Evidence Ledger.
2. Schedule PAPER run start criteria in Promotion Control Board.
3. Pre-create replay pair slots (Run A / Run B) in Evidence Ledger.
4. Run certifier + promotion report and update board from artifacts.
5. Do not enable TRADE until PROMOTE verdict is recorded.

