# Strategy Spec Template

Status: template

Use this before implementing any new strategy. Keep each filled-out spec short
enough to review in one sitting.

## One-Sentence Edge

What repeatable market behavior should pay us, after costs?

## Market And Instruments

- Asset class:
- Universe:
- Holding period:
- Rebalance cadence:
- Broker/venue:

## Structural Rationale

Why should this edge exist?

Who is likely on the other side?

Why might the edge persist after it is known?

## Trade Rules

Entry:

Exit:

Position sizing:

Cash/reserve rule:

Risk caps:

## Data Contract

Required inputs:

- prices:
- corporate actions:
- fundamentals/events:
- benchmark/risk model:
- transaction cost assumptions:

Point-in-time rules:

- Use only data available before the decision timestamp.
- Lag indicators by at least one bar unless the data source proves earlier availability.
- Do not use centered windows, whole-sample z-scores, or survivor-only universes.

## Backtest Plan

Baseline:

Walk-forward split:

Cost model:

Slippage/spread model:

Number of variants expected:

Multiple-testing control:

## Metrics

Required:

- CAGR / annualized return
- volatility
- Sharpe and Deflated Sharpe inputs
- max drawdown
- turnover
- hit rate
- average win/loss
- exposure by asset/sector
- cost sensitivity

Optional:

- beta to benchmark
- VaR/CVaR
- regime attribution
- capacity estimate

## Failure Modes

What could make the backtest look better than reality?

What regime should hurt this strategy?

What would make us stop trading it?

## Promotion Gate

Research:

Paper:

Small live:

Full live:

## Learning Notes

Financial concept learned:

Code concept learned:

One test that prevents a real trading failure:
