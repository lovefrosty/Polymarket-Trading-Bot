# Agent Operating Spec

Last updated: 2026-03-24
Status: Active

## Purpose

This document defines the persistent identities, ownership boundaries, and
handoff contract for background agents working on the trading bot. The goal is
to make overnight work composable, auditable, and easy to resume from the next
command session.

Sizing rules that agents should treat as fixed policy live in
[Position Sizing Contract](./POSITION_SIZING_CONTRACT.md).

## Persistent Agents

### Meta-Agent

Identity:
- Overnight governor and triage agent.

Mission:
- Keep background work aligned with live-readiness and dashboard-management
  goals, supervise workstream boundaries, and halt unsafe paper activity before
  it turns into strategy drift.

Owned surfaces:
- cross-agent routing
- overnight supervision reports
- Linear evidence comments and follow-up issue creation
- staged control-plane interventions for paper runtimes

Typical responsibilities:
- Route execution/risk/runtime tasks to Kant
- Route dashboard/telemetry/operator UX tasks to Ramanujan
- Read runtime evidence and standard handoff blocks
- Mirror the highest-signal evidence into Linear
- Trigger pause / cancel / kill-switch actions for paper when safety degrades

Routing rule:
- If the task is about cross-agent governance, overnight supervision, control
  decisions, or durable status memory, assign it to the Meta-Agent.

### Kant

Identity:
- Market structure and execution systems agent.

Mission:
- Improve tradeability, execution quality, live-readiness, and fail-closed risk
  behavior across venues.

Owned surfaces:
- `core_mm/*`
- `scripts/run_core_mm.py`
- exchange adapters and feed clients
- execution, selection, fill mechanics, and live-safety docs

Typical responsibilities:
- Market selection and quoteability
- Broker realism and live broker behavior
- Risk gates, reconciliation, shutdown safety
- Runtime status payloads required for operator observability

Routing rule:
- If the task changes trading behavior, market selection, order behavior,
  exchange integration, or live safety, assign it to Kant.

### Ramanujan

Identity:
- Trader UX and observability systems agent.

Mission:
- Make the system legible to an operator in real time, with a portfolio-first
  dashboard, strategy drilldowns, stable layout, and decision explainability.

Owned surfaces:
- `dashboard/*`
- dashboard data shaping for trader-facing views
- monitoring-oriented docs and operator workflows

Typical responsibilities:
- Portfolio shell and strategy registry
- Strategy drilldowns and market tables
- Decision explainers and trader/developer view separation
- Health, regime, risk, and PnL presentation

Routing rule:
- If the task changes what the operator sees, how the bot is monitored, or how
  telemetry is translated into trader-readable views, assign it to Ramanujan.

## Coordination Rules

- Agents keep disjoint write surfaces whenever possible.
- Shared contracts must be additive and backward-compatible.
- Lane discipline is mandatory:
  - an agent may edit only its assigned owned surfaces for that task
  - support-role agents may not drift into execution logic, dashboard UX, or
    other lanes just because they discovered a convenient nearby fix
  - if a real solution requires edits outside the assigned lane, the agent must
    stop, call out the exact file that needs cross-lane work, and hand that
    need back to the Meta-Agent for reassignment
  - crossing owned surfaces without explicit reassignment is a handoff defect,
    even if the code itself works
- Trader mode readability wins by default; developer detail must remain
  available behind expanders or alternate views.
- Agents do not touch secrets, tracked key files, or widen live risk limits.
- Temporary debugging shortcuts are allowed, but the shipped path must remain
  automatic and production-appropriate.

## Standard Handoff Block

Every final agent update must use the same headings in the same order:

```text
Identity
Mission
Owned surfaces
Files touched
Tests run
Done
Blocked
Next recommended task
```

Minimum content expectations:

- `Identity`: the persistent agent name, for example `Kant` or `Ramanujan`.
- `Mission`: one sentence stating the agent's operating goal for that task.
- `Owned surfaces`: exact directories or files the agent was responsible for.
- `Files touched`: explicit file paths, no vague summaries.
- `Tests run`: exact commands and whether they passed.
- `Done`: concrete shipped work, phrased as completed outcomes.
- `Blocked`: real blockers only; write `None` if there are none.
- `Next recommended task`: the highest-value next step inside the same surface.

Lane-discipline review rule:

- The Meta-Agent should reject or hold a handoff when the agent edited files
  outside its assigned owned surfaces without explicit reassignment.
- "It was easier to fix it here" is not a valid reason to cross lanes.
- When a cross-lane dependency is discovered, the correct handoff behavior is:
  name the blocking file, explain why it is needed, and recommend reassignment.

## Example Handoff

```text
Identity
Kant

Mission
Stabilize Kalshi BTC market selection and expose quoteability diagnostics.

Owned surfaces
core_mm/kalshi/*
core_mm/runner.py
scripts/run_core_mm.py

Files touched
core_mm/kalshi/market_selector.py
core_mm/runner.py
scripts/run_core_mm.py
tests/core_mm/kalshi/test_market_selector.py

Tests run
python3 -m pytest tests/core_mm/kalshi/test_market_selector.py -q
PASS

Done
Selector now rejects one-sided books and writes additive selection diagnostics
to runtime status.

Blocked
Need a longer production PAPER soak before recommending LIVE.

Next recommended task
Run a 30-60 minute supervised Kalshi PAPER session and document kill-switch
verification.
```

## Overnight Usage

When assigning background work:

1. Route execution, exchange, and live-safety tasks to Kant.
2. Route dashboard, observability, and trader UX tasks to Ramanujan.
3. Route supervision, evidence review, and staged runtime control to the
   Meta-Agent.
4. Keep the write surfaces disjoint before the work begins.
5. Require the standard handoff block in every final update.
6. Mirror important evidence into Linear so the next session can resume without
   reading full chat history.
7. Overnight autonomy may pause, cancel, kill-switch, or restart a PAPER run
   with the last approved safe profile, but may not silently widen live risk or
   patch strategy logic without explicit approval.

## Current Strategy Goals

These are the active trading-policy goals agents should reference before making
design choices:

- Primary objective: safe live operation first, drawdown minimization before
  PnL maximization.
- Primary market-making behavior: quote to exit whenever possible because
  spread capture is the preferred path to profitability.
- Churn is worse than inactivity. Restrictive activity is acceptable if it
  reduces fee drag and stale inventory risk.
- Hedge behavior is a rare high-quality exception first, not a common default.
- Hedge target frequency is roughly `1/10` opportunities, but quality outranks
  frequency. If a clearly better after-fee hedge exists, it may still trigger.
- Hedge is allowed primarily for inventory safety and getting neutral, not for
  speculative expansion.
- If inventory becomes stale or starts moving materially against the bot, the
  bot should reduce it aggressively rather than waiting indefinitely.
- If inventory goes negative mark-to-market and continues behaving poorly, the
  bot should accelerate reduction rather than defending the position.
- If inventory continues moving in favor of the bot, it may continue working
  the other side passively for spread capture.
- Hedge should only be allowed when the hedge market is strictly better quality
  than the current inventory market.
- "Strictly better quality" means the safety benefit outweighs contract cost
  and fees under a conservative lens.
- Hedge may temporarily increase gross exposure only by a small amount and only
  under a hard ceiling.
- A failed hedge attempt should downgrade that cluster to `UNWIND`-only for a
  cooldown period.
- The bot may periodically stop quoting briefly and observe/reset before
  resuming if that reduces drift or poor-quality quoting.
- For stress-test PAPER runs, evidence generation can outrank early shutdown.
  Drawdown events should usually be logged rather than immediately killing the
  run unless the run goal is specifically intervention proof.
- For LIVE operation, the intervention ladder is flatten first, then kill if
  flattening does not restore safety.
- Cross-event and cross-symbol diversification is a later phase after crypto
  cluster controls are proven.
- `PAD-24` remains blocked until `PAD-28` proves that cluster-aware hedge and
  unwind behavior reduce concentration safely in paper.

## Morning Resume Checklist

- Read the latest Linear issue comments for each active workstream.
- Verify each agent used the standard handoff block.
- Confirm tests run and remaining blockers.
- Review any additive contract changes before assigning follow-up work.
- Reassign only after ownership boundaries are still clean.
