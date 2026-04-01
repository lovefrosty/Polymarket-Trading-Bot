# Rotating Single-Market Reporting Spec

Last updated: 2026-03-25
Status: Active

## Summary

The first Kalshi launch proof is evaluated as:

- `single active market`
- selector may rotate between BTC buckets
- run-level economics and risk behavior matter more than fixed-ticker persistence

This spec defines the required reporting for every launch-candidate run.

## Session Semantics

- `single active market` means `max_active_markets = 1`
- `episode` means a contiguous time interval where the selected market stays the same
- `market_change_count` is the number of times the selected market changes during a run
- `episode_count = market_change_count + 1` when at least one market was selected
- a market switch is not a failure by itself
- a run fails only if rotation is not explainable or if economics/risk degrade materially

## Required Scorecard Fields

Every report must include:

- realized net PnL
- unrealized PnL
- total PnL
- cumulative fees
- turnover
- fill count
- distinct order count
- max drawdown in dollars
- max drawdown as peak-relative percentage
- episode count
- market change count
- previous market
- latest switch reason
- top markets by decision count
- top switch reasons
- control-state distribution
- risk-action distribution
- hedge-action distribution
- latest realized fee source
- launch-scope label

## Verdict Labels

Allowed report verdicts:

- `clean_quote_first`
- `profitable_but_rotation_heavy`
- `profit_depended_on_emergency_exits`
- `not_launchable`

Interpretation:

- `clean_quote_first`
  - after-fee PnL is positive
  - emergency behavior is not dominant
  - selector behavior is limited and legible
- `profitable_but_rotation_heavy`
  - after-fee PnL is positive
  - rotation is materially frequent
  - still acceptable for the current launch lane if selection reasons are understandable
- `profit_depended_on_emergency_exits`
  - run made money but leaned too much on force-flat, stale unwind, or persistent unwind-only behavior
- `not_launchable`
  - after-fee PnL is not positive or the run is otherwise clearly unsafe

## Pass / Fail Guidance

For a small live canary, prefer runs that are:

- `clean_quote_first`
- or `profitable_but_rotation_heavy` with understandable switch reasons

Do not promote based on runs that are:

- `profit_depended_on_emergency_exits`
- `not_launchable`

## Operator Understanding Requirement

Before launch, the operator must be able to explain:

- why the current market was selected
- why the selector switched away from the prior market
- whether the run stayed quote-first
- whether fees materially changed the edge
- whether PnL came from normal quoting rather than emergency exits
