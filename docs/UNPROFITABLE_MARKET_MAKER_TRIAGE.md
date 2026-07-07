# Unprofitable Market Maker Triage

Last updated: 2026-05-10
Status: Active strategy triage

## Core Decision

Do not add neural-network, macro, or agentic research alpha to compensate for an
unprofitable base market maker.

First determine whether the bot is losing because of:

1. no spread edge after fees
2. adverse selection
3. stale quotes
4. bad market selection
5. bad exit logic
6. sizing too large for the available edge
7. paper/live mismatch

If the base market maker cannot at least break even in carefully selected
markets with conservative size, research alpha should remain paper-only.

## Profitability Tests

Every run should be judged with these questions in this order.

### 1. Did fills have positive after-fee edge?

Primary fields:

- `execution_quality.avg_realized_spread_bps`
- `execution_quality.avg_fee_bps`
- `execution_quality.avg_net_edge_bps`
- `execution_quality.negative_net_edge_rate`

Interpretation:

- If net edge is negative, the bot is quoting too tightly or paying too much to
  exit.
- If net edge is only slightly positive, the strategy is fragile because one
  stale fill or forced exit can erase many wins.

### 2. Were fills adverse?

Primary fields:

- `execution_quality.avg_markout_1s_bps`
- `execution_quality.avg_markout_5s_bps`
- `execution_quality.negative_markout_1s_rate`
- `execution_quality.negative_markout_5s_rate`
- `alpha_adversity` in decision metadata

Interpretation:

- Negative markout means the bot is being picked off.
- High negative markout rate means fills are not spread capture; they are
  informed flow taking stale or weak quotes.

### 3. Was profitability dependent on emergency exits?

Primary fields:

- `risk_proof.decision_risk_actions`
- `session_performance.risk_action_counts`
- `session_performance.control_state_counts`
- `max_drawdown_abs`

Interpretation:

- A run is not clean if PnL depends on frequent force-flat or stale-unwind
  behavior.
- Force-flat should be a safety tool, not the normal exit mechanism.

### 4. Did market selection create the problem?

Primary fields:

- selected market
- selected reason
- quoteable ratio
- spread and depth at selection
- top rejected candidates
- market switch reasons

Interpretation:

- If only some markets lose, fix selection before changing quote logic.
- If all markets lose, fix economics and execution first.

### 5. Did paper flatter the strategy?

Primary fields:

- fill model assumptions
- queue wait
- queue depth fraction
- stale-book refusal
- fee source
- real exchange fee vs fallback fee

Interpretation:

- Paper profitability is not launch evidence unless the fill model is
  conservative enough.
- Live no-fill runs are not evidence of profitability or unprofitability; they
  are evidence that the live quote profile may be too passive or blocked.

## Immediate Operating Rule

Until profitability is proven:

- max active markets: `1`
- trade size: minimum viable size
- no research alpha in live sizing
- no hedge credit in the safety case
- no latency-sensitive taker strategy
- no neural-network model in execution
- paper-only experiments for macro and agentic research signals

## Remediation Ladder

Apply changes in this order.

1. **Raise minimum edge**
   - widen `base_spread_multiplier`
   - require stronger depth/spread conditions
   - stop improving the BBO in thin markets

2. **Reduce adverse selection**
   - tighten stale-book gate
   - cancel faster on flow reversal
   - use macro/HMM regimes only to reduce activity, not add direction
   - treat far-tail prices as reduce-only unless a buy reduces existing reverse
     exposure

3. **Improve market selection**
   - prefer stable spread, real two-sided depth, and repeatable fills
   - reject markets where fills have negative markout despite positive quoted
     spread

4. **Constrain exits**
   - prefer maker exits while position is fresh
   - use taker/force-flat only when stale, near expiry, or loss caps require it
   - measure whether exits lose more than entries earn

5. **Shrink size**
   - lower `trade_size`
   - lower max exposure
   - let Kelly only scale down until calibration is proven

6. **Only then add alpha**
   - start with paper-only research signals
   - require out-of-sample improvement versus no-signal baseline
   - allow at most small quote skew, never risk override

## Loss Attribution Report

Use `scripts/report_price_bucket_pnl.py` to create a run-level price-bucket
loss report:

```bash
python3 scripts/report_price_bucket_pnl.py --runtime-root tmp/core_mm_runs/<run>
```

The report attributes fills by reference price bucket and includes:

- PnL by side
- fees
- gross notional
- markout and net edge when execution-quality telemetry is available
- fill count by quote mode
- adverse-selection boundary score and reason when the tail guard is active

This should be extended later with market, risk-action, volatility, stale-regime,
quoteable-ratio, and no-quote-reason attribution. The goal is not to find a more
complex model. The goal is to identify the exact reason the existing market
maker loses money.

## Adaptive Adverse-Selection Boundary

The live quote loop should not treat `0.10` and `0.90` the same as `0.50`,
but the boundary should not be a magic number.

Adverse selection means the bot is supplying liquidity to someone with better
or faster information. The bad fill is not random. The resting quote gets hit
because the taker believes the probability has already moved and our quote has
not adjusted.

In market microstructure terms, this is one reason spreads exist: liquidity
providers need compensation for trading against better-informed flow. For this
bot, the practical symptom is negative markout after fills, especially when
fills happen during stale books, one-sided flow, news, volatility, or thin
tail-price books.

On Polymarket this has a specific shape:

- prices are probabilities between `0.00` and `1.00`
- bids are buy orders, asks are sell orders, and crossing the book means buying
  at the ask or selling at the bid
- a passive maker quote waits in the book until a taker chooses to trade against
  it
- makers are not charged Polymarket taker fees, but crossing to exit or hedge
  can make the bot the taker
- a one-cent spread is much more expensive near `0.10`/`0.90` than near `0.50`
  when measured against the remaining uncertainty or remaining upside
- if the cheap side at `0.10` fills, the taker may be selling because fresh
  information says it should be closer to zero
- if the expensive side at `0.90` fills, the bot may have little remaining
  upside while still carrying large downside and exit risk
- a future counterparty-flow signal can raise adverse-selection risk when
  public wallet or trader-cluster data suggests the taker flow is unusually
  informed; see `docs/POLYMARKET_COUNTERPARTY_FLOW_SIGNAL.md`

Reference points:

- Glosten and Milgrom's market-microstructure model explains adverse selection
  as the cost of quoting against better-informed traders.
- Polymarket's orderbook docs define bids, asks, spreads, midpoints, tick size,
  and orderbook depth.
- Polymarket's fee docs state that platform fees are taker-side and vary by
  market/category, so crossing to hedge or exit can change the economics.

The current implementation treats this as an adaptive adverse-selection score.
Price tailness is only one input. A buy is blocked when the combined score says
the quote is likely to be picked off or expensive to exit.

Inputs:

- distance from `0.50`
- quoted edge versus estimated exit cost
- spread measured against the tail payoff denominator
- exit-side depth
- hedge-side depth
- adverse flow imbalance
- recent fill adversity
- realized volatility
- stale-book pressure
- time-to-expiry pressure

Current implementation:

- default mode is `adaptive`
- `static` mode is still available for explicit experiments
- block new buy risk when adaptive adverse-selection score exceeds the
  configured threshold
- continue to allow sells so inventory can exit
- continue to allow buys only when they reduce reverse-token exposure
- expose the boundary as CLI/runtime config:
  - `--boundary-guard-mode`
  - `--boundary-no-new-risk-min-price`
  - `--boundary-no-new-risk-max-price`
  - `--boundary-adverse-selection-threshold`
  - `--boundary-exit-cost-multiplier`
- log boundary state in quote metadata for later attribution

This is a risk regime, not an alpha view. A research agent or simulation should
not be allowed to override it without explicit promotion evidence.
