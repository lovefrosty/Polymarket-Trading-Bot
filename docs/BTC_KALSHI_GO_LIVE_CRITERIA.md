# BTC Kalshi Go-Live Criteria

Last updated: 2026-03-25
Status: Active market-specific readiness criteria

## Summary

For the current BTC Kalshi launch path, go-live readiness is a three-state
decision:

- `ready now`
- `ready with no-hedge constraints`
- `not ready`

Current classification for the first live deployment:

- full strategy: `not ready now`
- constrained quote-first strategy: `ready with no-hedge constraints` at best

This means the bot is close to live-ready only if the first deployment is kept
to one active Kalshi market at a time, with selector rotation allowed, and
live promotion does not depend on hedge as an operational safety mechanism.

These criteria should therefore be treated as the operative first-launch
standard while Polymarket access remains unavailable.

Launch-lane triage under local-machine constraints lives in
[Local-Only Launch Strategy Triage](./LOCAL_ONLY_LAUNCH_STRATEGY_TRIAGE.md).
The operator understanding bar for first deployment lives in
[Owner Understanding Checklist](./OWNER_UNDERSTANDING_CHECKLIST.md).
Rotation-aware scorecard requirements live in
[Rotating Single-Market Reporting Spec](./ROTATING_SINGLE_MARKET_REPORTING_SPEC.md).

## Locked Launch Scope

The first live rollout is intentionally narrow:

- `max_active_markets = 1`
- Kalshi only
- selector may rotate the active market between BTC buckets
- hedge left enabled in code but non-creditable for promotion
- quote / skew / unwind / force-flat / kill are the real safety controls
- multi-market inventory calibration is deferred until after one-market
  profitability is proven
- Kalshi fee correctness is a launch blocker because after-fee edge must be
  real, not assumed

## Why This Classification Exists

The current paper evidence supports:

- quote / skew / unwind behavior functioning as the primary inventory controls
- conservative risk reduction without obvious dependence on force-flat as the
  normal path

The current paper evidence does not yet support:

- hedge as a proven realized-risk reducer
- hedge as a reliable live safety valve
- market expansion justified by hedgeability claims

Accepted hedge candidates are not enough. Go-live credit is earned only by
realized hedge improvement.

## State 1: `Ready Now`

This state means the full intended strategy is live-ready, including hedge as
an operational safety tool.

Promotion criteria:

- repeated paper runs in these markets produce real `HEDGE` activations, not
  just accepted candidates
- accepted hedges show positive realized outcome:
  `hedge_realized_improvement_state = improved` meaningfully more often than
  `no_improvement`
- hedge-enabled runs show equal or better balanced score than no-hedge-style
  runs
- hedge-enabled runs show equal or lower:
  - hold-tail risk
  - force-flat reliance
  - stranded inventory
  - churn without offsetting benefit
- no recurring pair class where covariance and execution pass but realized
  hedge outcome remains bad
- no dependence on one-off path-dependent windows to justify hedge usefulness

Current distance:

- far from this state

Interpretation:

- do not claim hedge-ready live deployment yet
- do not credit hedge in the live safety case for these markets

## State 2: `Ready With No-Hedge Constraints`

This state means the bot is promoted only as a conservative quote-first,
inventory-managed strategy. Hedge may exist in code, but it is not part of the
promotion argument.

Promotion criteria:

- quote-first paper runs are stable across repeated windows in these same
  markets
- net paper PnL is positive or otherwise acceptable under conservative size and
  risk settings
- no dangerous force-flat dependence
- hold-tail is controlled and not exploding under normal adverse conditions
- stale inventory is resolved adequately through `SKEW` and `UNWIND`
- the live safety case is still defensible if hedge never fires
- hedge absence does not make the strategy operationally fragile

Required live constraints in this state:

- hedge is treated as non-creditable for promotion
- keep `max_active_markets = 1`
- keep conservative `trade_size`
- keep tight per-market and per-event caps
- keep conservative stale / unwind policy
- do not justify market expansion using hedgeability claims
- require correct Kalshi fee accounting in both paper and live telemetry

Current distance:

- close

Interpretation:

- this is the correct “we are close” framing
- the bot may be promotable as a constrained market maker in these markets
- it is not yet promotable as a hedge-capable clustered strategy
- this is the state that can justify a Kalshi-first launch while waiting on
  Polymarket

## State 3: `Not Ready`

This state means even the no-hedge version is not yet safe enough.

To leave this state, paper evidence must show:

- repeated runs without unstable hold-tail behavior
- no persistent force-flat reliance
- stale inventory behavior that is consistently acceptable
- quote quality that is not masking unresolved tail-risk problems
- repeatability, not one lucky path

You should treat the strategy as `not ready` again if new longer paper runs
show:

- unstable drawdown or hold-tail behavior
- persistent force-flat reliance
- unacceptable unwind behavior
- poor repeatability once hedge is ignored

Current distance:

- likely past this state already, assuming conservative live sizing

## Exact Promotion Checklist

### To promote from current state to `ready with no-hedge constraints`

Require all of the following:

- multiple paper runs under the exact intended rotating single-market launch profile
- stable positive or otherwise acceptable after-fee paper PnL
- low force-flat reliance
- controlled hold-tail
- no operational dependence on hedge to explain safety
- unwind / force-flat / kill behavior proven under adverse conditions
- operator can explain why the current market was selected and why switches occurred
- Kalshi fee accounting is verified against exchange-reported fees when present

### To promote from `ready with no-hedge constraints` to `ready now`

Require all of the following:

- repeated real hedge activations in paper
- realized hedge improvement proof
- hedge-enabled runs outperform or at least do not degrade the no-hedge
  baseline
- pair-level evidence that accepted hedges are operationally useful, not just
  statistically plausible

## Current Operating Rule

For these BTC Kalshi markets:

- treat hedge as unproven for live promotion
- treat quote / skew / unwind as the real safety case
- treat the first launch as single active market, selector can rotate
- treat hedge evidence by realized portfolio improvement, not candidate count
  or model richness
- treat these criteria as the primary live launch gate until Polymarket is
  available and independently validated
- if live promotion happens before hedge proof exists, the live claim must be:
  - one active market at a time
  - selector can rotate
  - quote-first
  - skew/unwind managed
  - not hedge-dependent
