# Trading Policy Goals

Last updated: 2026-03-25
Status: Active operator intent

## Core Objective

The bot is a capital-preserving liquidity provider first. It is not allowed to
drift into speculative risk-taking just because multiple related markets are
available.

Priority order:

1. Stay safe.
2. Minimize drawdown.
3. Preserve the ability to quote and unwind.
4. Only then optimize for PnL.

## Inventory Management Philosophy

- Quoting is the primary mechanism for entering and exiting.
- If the bot can exit through quotes, that is preferred because it captures
  spread and is the most profitable path.
- If inventory becomes stale, concentrated, or starts moving against the bot,
  the system should shift from passive spread capture to aggressive risk
  reduction.
- Getting neutral is safety. Paying fees to get neutral is acceptable when it
  is functioning as insurance against worse losses.
- If a position continues to move in the bot's favor, it may continue working
  the other side passively instead of rushing to flatten.

## Position Sizing Contract

Position sizing is safety-first. The sizing system may express conviction, but
it may not override hard safety limits.

The operational definition lives in [Position Sizing Contract](./POSITION_SIZING_CONTRACT.md).

Sizing precedence for every cycle:

1. Hard limits first: per-token `hard_position_cap`, configured exposure caps,
   and available inventory always outrank all other sizing inputs.
2. Risk budget next: buy-side entry sizing must obey the current per-trade risk
   budget when risk-based share sizing is enabled.
3. Kelly after safety: Kelly may only scale sizing down from the configured
   `trade_size`, never above it, and never past any hard or risk budget.
4. Baseline trade size after Kelly: `trade_size` remains the default maximum
   quoting size when no tighter control is active.
5. Minimum order gate last: if the resulting order is below `min_order_size`,
   the order should be skipped rather than rounded up.

### Buy-Side vs Sell-Side Rules

- Buy sizing is entry risk and must remain constrained by hard caps, available
  cash, and the active per-trade risk budget.
- Sell sizing is risk reduction first. A sell that reduces existing inventory
  should not be blocked by the buy-side per-trade loss budget.
- Sell aggressiveness may still be shaped by inventory skew, quote logic, and
  available inventory, but not by a rule that prevents reduction of existing
  risk.
- This asymmetry is intentional for the current rollout: opening risk is capped
  more tightly than closing risk.

### Allocated Equity Semantics

- `strategy_allocated_equity` is the preferred risk base for per-trade,
  per-market, and per-event safety budgets.
- If `use_allocated_equity_for_risk=true`, allocated strategy capital should be
  treated as the canonical bankroll for risk limits even if wallet cash is
  temporarily larger.
- Wallet balance still constrains affordability for buys. It does not replace
  the risk base unless allocated-equity mode is disabled or unavailable.
- If allocated-equity mode is disabled or no allocated equity is provided, the
  implementation may fall back to wallet/reference equity, but that fallback
  must be observable in telemetry and logs.

### Kelly Interaction Rules

- Kelly is a soft conviction scaler, not a target-position controller.
- Kelly may reduce participation when edge is weak or absent.
- Kelly may increase size only up to the configured `trade_size`; it may not
  authorize larger-than-baseline quoting by itself.
- Kelly must remain subordinate to hard caps, risk budgets, inventory
  directionality, and affordability checks.
- Kelly remains a paper-first control until calibration evidence shows it
  improves outcomes without increasing drawdown instability.

## Hedge Policy

Hedge is a rare high-quality exception first.

The workflow for validating hedge changes lives in
[Hedge Calibration Workflow](./HEDGE_CALIBRATION_WORKFLOW.md).
Market-specific live-readiness criteria for the current BTC Kalshi cluster live
in [BTC Kalshi Go-Live Criteria](./BTC_KALSHI_GO_LIVE_CRITERIA.md).
That document is the operative launch gate while Kalshi is the primary venue
and Polymarket remains unavailable.
Local-only strategy triage for the first launch lives in
[Local-Only Launch Strategy Triage](./LOCAL_ONLY_LAUNCH_STRATEGY_TRIAGE.md).
The required operator comprehension bar lives in
[Owner Understanding Checklist](./OWNER_UNDERSTANDING_CHECKLIST.md).

The first rollout should treat hedge as:

- an inventory-safety tool
- a fallback to get neutral
- a way to reduce cluster concentration

It should not yet be treated as a broad speculative expansion tool.

Target operating frequency:

- Hedge should be reachable but uncommon, roughly on the order of `1/10`
  cluster-management opportunities.
- That target is secondary to quality. A good hedge should trigger whenever it
  clearly improves safety after fees.

### Hedge Entry Rules

- Hedge only when the hedge market is strictly better quality than the current
  inventory market.
- "Better quality" means the safety benefit outweighs the contract cost and
  expected fees. After-fee economics should still look conservative, not
  optimistic.
- Hedge should reduce cluster net exposure first.
- Hedge may increase gross exposure only slightly and only under a hard cap.
- If the hedge is not clearly improving safety, do not use it.

### Hedge Success Criteria

A hedge is successful only if it does one or more of the following quickly:

- materially reduces stale inventory risk
- materially reduces cluster net exposure
- gets the portfolio closer to neutral

A hedge is not successful just because an order was emitted.

### Hedge Failure Rules

- If a hedge attempt does not improve concentration quickly enough, the cluster
  should drop into `UNWIND`-only mode for a cooldown period.
- If inventory is already stale, `UNWIND` should usually outrank `HEDGE`
  unless the hedge is clearly the safer path.
- Hedge failures should not be retried immediately. Cooldown is mandatory.

## Churn vs Inactivity

- Churn is worse than inactivity.
- Lower and lower spreads that bleed capital are not acceptable.
- Restrictive activity is acceptable if it keeps capital intact.
- Aggressive action is acceptable only when inventory is stale, deteriorating,
  or concentrated enough that safety now dominates spread capture.
- A little more activity is acceptable if it improves safety, but the system
  should remain restrictive by default.

## Adverse Inventory Rules

- If inventory is moving against the bot and mark-to-market turns negative, the
  bot should accelerate reduction.
- Negative mark-to-market should not always mean an instant panic exit on the
  first tick through zero; the preferred behavior is:
  - quote-first while inventory is healthy
  - accelerate exits quickly once the position is both negative and no longer
    behaving well
  - unwind aggressively once the position is stale or continuing to deteriorate
- If mark-to-market remains positive, the bot may keep working the other side
  passively until stale.

## Intervention Ladder

- In testing, stress runs should prefer logging and evidence generation over
  early forced shutdowns.
- Test runs should not be hard-killed solely because of an interim drawdown
  event if the purpose is stress testing. Drawdowns should be recorded and
  reviewed.
- In live operation, the intervention ladder should be:
  1. flatten first
  2. then kill if flattening does not restore safety

## Market Expansion Direction

- Broader diversification is a later phase.
- The sequence should be:
  1. clustered crypto markets first
  2. then other event families such as geopolitics
  3. then sports and broader cross-symbol/cross-event portfolios
- Cross-event and cross-symbol diversification should not be used to justify
  weak same-cluster controls.

## Observation / Reset Behavior

The bot may occasionally stop quoting briefly and observe the market before
resuming. This is allowed if it improves quote quality or reduces stale
inventory drift.

## Current Design Direction

- `SKEW` is the default cluster-management tool.
- `UNWIND` is the safety tool.
- `HEDGE` must earn its place with paper evidence.
- Stress testing should intentionally allow harder situations so the policy can
  be observed under pressure.
- Market expansion (`PAD-24`) stays blocked until hedge and cluster controls
  are proven in paper.
- Position sizing should be explainable cycle by cycle in terms of which gate
  bound the final order size.
