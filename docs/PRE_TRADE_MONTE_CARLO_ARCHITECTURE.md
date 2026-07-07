# Pre-Trade Monte Carlo Architecture

Last updated: 2026-05-10
Status: Proposed architecture

## Core Decision

Use Monte Carlo as a pre-trade risk gate, not as the live strategy brain.

The live bot should not wait for agents to research the world before each
quote. Agents, news feeds, and research models run asynchronously and write
timestamped inputs. The trading loop consumes the latest valid inputs and runs a
bounded simulation before placing or replacing orders.

If the simulation is stale, slow, missing inputs, or inconclusive, the bot
fails closed by reducing size, widening, or skipping the trade.

## Why This Shape

The current bot is a market maker. The urgent question before each quote is:

> Given current inventory, book state, fees, volatility, and information risk,
> what is the distribution of outcomes if this quote gets filled?

That is a better use of 10,000 simulations than trying to forecast BTC from
scratch on every cycle.

## Runtime Flow

```text
News feeds / macro feeds / agents / market data
        |
        v
Research sidecars write timestamped feature state
        |
        v
Scenario engine builds weighted BTC paths
        |
        v
Pre-trade Monte Carlo gate
        |
        v
Quote / reduce size / widen / skip / unwind
```

## Live Loop Contract

For every proposed quote, the simulation receives:

- current YES/NO book
- proposed side, price, and size
- current inventory and average cost
- time to market expiry
- current BTC spot and short-horizon volatility
- recent markout/adverse-fill stats
- fee model
- stale-book state
- active macro/news/research features
- sub-agent confidence weights

It returns:

- expected PnL
- PnL percentile bands
- probability of loss
- expected shortfall / tail loss
- probability of forced unwind
- expected inventory after fill
- confidence / data freshness status
- recommended action: `ALLOW`, `REDUCE`, `WIDEN`, `SKIP`, or `UNWIND_ONLY`

## Simulation Model

Start simple.

Use a lightweight path generator for BTC over the remaining contract horizon:

- drift from calibrated short-horizon signal, defaulting to zero
- volatility from recent realized vol
- jump component from news/event risk
- regime multiplier from HMM or volatility-state sidecar
- microstructure penalty from adverse markout and flow reversal

For each path:

1. simulate BTC path until market expiry
2. map final BTC outcome to YES/NO payoff
3. simulate whether our quote fills
4. apply fee/slippage/exit assumptions
5. compute PnL under current inventory

Do not overfit the path model early. The first useful version is a stress-test
gate, not a perfect distributional forecast.

## Performance Requirement

10,000 simulations are acceptable only if they stay within a hard latency
budget.

Target:

- normal loop budget: less than 50 ms
- hard timeout: 100 ms
- if timeout occurs: fail closed

Implementation implication:

- pure Python loops are probably too slow
- use vectorized NumPy first
- consider Numba or Rust only if NumPy is insufficient
- precompute common shock grids
- cache scenario packs and update them on a schedule

## Agent Role

Sub-agents should not trade directly.

They produce evidence packets:

```json
{
  "schema_version": "agent_edge_v1",
  "topic": "BTC",
  "direction": "up",
  "probability_delta": 0.03,
  "confidence": 0.55,
  "horizon_secs": 900,
  "source_count": 4,
  "novelty": "medium",
  "as_of_ts_ms": 1778433600000,
  "expires_ts_ms": 1778434500000,
  "summary": "ETF flow headlines and dollar weakness support mild upside bias."
}
```

The trading loop treats this as one weighted variable, not as authority.

## News Infrastructure

Use a provider abstraction so feeds can be swapped without changing strategy
logic.

Recommended first categories:

- crypto-specific news and sentiment
- broad financial breaking news
- macro calendar
- exchange/ETF/regulatory headlines
- BTC spot, derivatives, and funding context

Candidate providers to evaluate:

- Benzinga for financial/newswire style feeds and market-moving news APIs.
- The Tie for crypto news, sentiment, on-chain, and token datasets.
- Finnhub for broad market data/news coverage and crypto/forex/stock endpoints.
- NewsAPI.ai or similar for broad web news and event clustering.

The first integration should support polling. Streaming can come later once the
strategy proves that news latency matters.

## Fail-Closed Rules

The Monte Carlo gate may never widen live risk limits.

Hard rules:

- stale news state means lower confidence
- stale book state means no quote
- missing simulation means no new risk
- high tail loss means reduce or skip
- high forced-unwind probability means reduce or skip
- sub-agent disagreement lowers confidence
- agent confidence cannot increase size past normal caps

## Minimum Viable Build

1. Add `core_mm/monte_carlo_gate.py`.
2. Implement vectorized BTC path simulation with deterministic seed support.
3. Add a `MonteCarloDecision` dataclass.
4. Add unit tests for allow/reduce/skip behavior.
5. Wire it into PAPER mode only.
6. Log simulation outputs into decision metadata.
7. Run paper comparison:
   - baseline bot
   - bot with MC logging only
   - bot with MC gate active
8. Promote only if MC gate reduces drawdown/adverse fills without killing all
   quoteable activity.

## Non-Goals For V1

- no real-time LLM call in the trade loop
- no sub-agent directly authorizing trades
- no model-generated risk-limit changes
- no complex neural net path generator
- no 10,000-simulation gate if it exceeds latency budget

## Promotion Standard

The Monte Carlo gate earns live use only if paper evidence shows:

- lower negative markout rate
- lower tail loss
- lower force-flat / stale-unwind reliance
- no worse after-fee expected edge
- acceptable quoteable ratio
- deterministic replay behavior

