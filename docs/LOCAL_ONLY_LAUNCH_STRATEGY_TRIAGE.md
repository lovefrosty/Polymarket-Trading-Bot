# Local-Only Launch Strategy Triage

Last updated: 2026-03-25
Status: Active launch-triage memo

## Executive Recommendation

Treat the current machine, network path, and lack of VPS/colocation as a hard
constraint.

Under that constraint, the correct primary launch lane is:

- venue: `Kalshi`
- strategy: `single-market market making`
- operating mode: `quote / skew / unwind / force-flat / kill`
- launch posture: after-fee, passive, inventory-managed, and explicitly
  non-HFT

Defer or reject for the first launch:

- latency sniping
- HFT-style order-flow strategies
- multi-market hedge-first expansion
- classical factor investing across listed equities

Treat the agentic research loop as a later overlay, not as the current live
engine.

## Why This Is The Right Primary Lane

The active codebase is built around market making, not around a general
multi-strategy platform.

The live path today is centered on:

- market selection in [core_mm/kalshi/market_selector.py](../core_mm/kalshi/market_selector.py)
- runner orchestration in [core_mm/runner.py](../core_mm/runner.py)
- quote/risk loop in [core_mm/main_loop.py](../core_mm/main_loop.py)
- sizing in [core_mm/sizing.py](../core_mm/sizing.py)
- desktop operator monitoring in [core_mm/operator_service.py](../core_mm/operator_service.py) and [operator_app/src/App.tsx](../operator_app/src/App.tsx)

The active alpha stack is still microstructure-first:

- book imbalance
- fill asymmetry
- volatility regime
- complement arbitrage
- depth-change signal
- spot momentum

That means the realistic question is not "which of six research strategies
should launch first?" It is "which current strategy can make money safely on
your actual hardware and connectivity?"

The answer is the current single-market Kalshi market maker.

## Platform And Latency Viability

### Kalshi

Why Kalshi fits the current setup better:

- The current Kalshi feed implementation in [core_mm/kalshi/market_feed.py](../core_mm/kalshi/market_feed.py)
  is a REST poller with a default `poll_interval_secs = 1.0`.
- The main loop in [scripts/run_core_mm.py](../scripts/run_core_mm.py) also
  defaults to `cycle_secs = 1.0`.
- The first-launch scope is already locked to `max_active_markets = 1` in
  [core_mm/runner.py](../core_mm/runner.py) and the Kalshi go-live docs.
- The repo now has exchange-aware Kalshi fee handling in
  [core_mm/kalshi/fees.py](../core_mm/kalshi/fees.py),
  [core_mm/live_broker.py](../core_mm/live_broker.py), and
  [core_mm/paper_broker.py](../core_mm/paper_broker.py).

What this means operationally:

- Local-only latency is acceptable for passive quoting if your edge comes from
  spread capture, market selection, and disciplined inventory exits.
- Local-only latency is not acceptable for a strategy that depends on racing
  other traders to the touch, repeatedly crossing the spread first, or reacting
  inside sub-second information windows.

Venue-specific constraints that matter:

- Kalshi states its API is currently REST-based:
  [Kalshi API](https://help.kalshi.com/account-and-login/kalshi-api)
- Kalshi fees vary by market, and some markets can charge maker fees:
  [Kalshi Fees](https://help.kalshi.com/trading/fees)
- Kalshi currently runs both liquidity and volume incentive programs, but they
  are eligibility-constrained and can change:
  [Liquidity Incentive Program](https://help.kalshi.com/incentive-programs/liquidity-incentive-program)
  and
  [Volume Incentive Program](https://help.kalshi.com/en/articles/13823850-what-is-the-kalshi-volume-incentive-program)
- Kalshi also pays interest on eligible balances and open positions, which can
  slightly soften carry for longer holds:
  [APY on Kalshi](https://help.kalshi.com/navigating-the-exchange/your-portfolio/apy-on-kalshi)

MM-specific call:

- `local-only Kalshi passive MM`: viable enough to justify launch work
- `local-only Kalshi speed-sensitive taker trading`: not viable enough to
  justify launch work

### Polymarket

Why Polymarket is less aligned with the current launch path:

- The repo docs already describe the Polymarket competitive landscape as much
  more latency-sensitive for taker and sniping strategies in
  [docs/STRATEGY_RESEARCH.md](./STRATEGY_RESEARCH.md).
- Polymarket offers a real-time WebSocket feed and documents it as sufficient
  for standard MM operations:
  [Polymarket Data Feeds](https://docs.polymarket.com/developers/market-makers/data-feeds)
  and
  [Orderbook](https://docs.polymarket.com/trading/orderbook)
- Polymarket's fee/rebate regime is more fragmented by market type and is
  currently changing:
  [Fees](https://docs.polymarket.com/polymarket-learn/trading/fees),
  [Get Fee Rate](https://docs.polymarket.com/api-reference/market-data/get-fee-rate),
  [Maker Rebates](https://docs.polymarket.com/market-makers/maker-rebates),
  and
  [Liquidity Rewards](https://docs.polymarket.com/market-makers/liquidity-rewards)

What this means operationally:

- Local-only passive Polymarket MM may still be viable on the right markets.
- Local-only Polymarket taker, sniping, or latency-arb is the wrong first
  target unless you deliberately upgrade infrastructure.
- Since Polymarket is not the current live venue and the repo launch standard
  is already Kalshi-first, Polymarket should remain a later venue-expansion
  project rather than competing with the Kalshi launch lane now.

MM-specific call:

- `local-only Polymarket passive MM`: possible later, but not the current
  priority
- `local-only Polymarket speed-dependent trading`: reject for now

## Strategy-Fit Audit

| Strategy | Repo State | Venue Fit | Local-Only Fit | Decision |
| --- | --- | --- | --- | --- |
| Current market making | Implemented and active | Kalshi and Polymarket | Yes, if passive and tightly risk-controlled | `Now` |
| Mean reversion | Not implemented as a standalone rolling z-score strategy | Possible on both, but not current code | Yes in theory | `Later`, only after the base MM bot is live and legible |
| Cross-sectional momentum | Absent as a portfolio strategy; only spot momentum overlay exists | Weak fit for current binary prediction-market engine | Not the blocker | `No-fit for current launch` |
| Statistical arbitrage / cointegration | Partially implemented through hedge covariance and pair scoring, but not as a dedicated stat-arb engine | Limited current fit; hedge is still unproven | Local-only is not the main blocker | `Later`, after launch, and only if hedge becomes useful |
| Factor investing | Absent | Not a natural extension of prediction-market MM | Requires a different data and execution stack | `Separate system` |
| Agentic research loop | Research-stage only; planned in [docs/PLAN_research_sidecar.md](./PLAN_research_sidecar.md) | Better fit for Polymarket or slower Kalshi event markets than for current BTC MM | Yes, as an asynchronous sidecar | `Later overlay` |
| Copy trading | Absent | Mostly Polymarket-native because of public wallet visibility | Yes for slower markets, but not relevant to Kalshi-first launch | `No-fit for current launch` |
| HFT / OFI / latency trading | Absent as a dedicated strategy | Strong venue mismatch without low-latency infra | No | `Reject for now` |

### Important clarifications

- The current bot is not a general stat-arb engine. Hedge covariance in
  [core_mm/hedge_engine.py](../core_mm/hedge_engine.py) is an inventory control
  subsystem, not a production-ready cointegration strategy.
- The current bot is not a factor engine. The repo does not implement a
  sector-neutral min-variance optimizer or listed-assets portfolio builder.
- The current bot is not an agentic research trader today. The research
  sidecar exists as a saved plan, not as an active production path.
- The current bot is not an HFT system. Its loop cadence, feed model, Python
  runtime, and local-machine deployment are all inconsistent with that claim.

## Launch-First Roadmap

Sequence the work in this order:

1. Confirm that local-only Kalshi passive MM is the live target and stop
   diverting launch work into HFT or cross-venue strategy exploration.
2. Prove after-fee paper profitability on one exact Kalshi launch market using
   the current Kalshi fee model.
3. Tighten desktop operator visibility so you can explain market selection,
   sizing, and risk-state transitions from the local app.
4. Launch small with `max_active_markets = 1`.
5. Only after live stability, revisit research overlays and broader strategy
   incubation.

Explicitly defer:

- multi-market inventory calibration
- hedge promotion
- latency-sensitive Polymarket strategies
- factor portfolio construction
- broad alternative-strategy incubation

## Operator-Surface Implications

The desktop operator stack should be treated as primary:

- [core_mm/operator_service.py](../core_mm/operator_service.py)
- [operator_app/src/App.tsx](../operator_app/src/App.tsx)
- [operator_app/src/api.ts](../operator_app/src/api.ts)

Current gap:

- the runner and selector already hold richer market-selection context
- the operator snapshot still exposes only a thin market block:
  - `selected_market`
  - `quoteable`
  - `book_health`
  - `selected_reason`

That is good enough for first-launch triage, but not yet good enough for
multi-market operation or deep postmortems. For the current launch lane, that
means the next operator work should focus on one market and one rationale path,
not on a full portfolio console.

## Final Recommendation

Make the following commitments explicit:

- primary strategy: `single-market Kalshi market making`
- primary venue: `Kalshi`
- primary operating mode: `local-only passive MM with strong inventory exits`

Everything else should be treated as follows:

- `agentic research`: later overlay
- `stat-arb / hedge-first`: later research
- `factor investing`: separate system
- `copy trading`: separate venue-specific project
- `HFT / sniping / OFI`: rejected until infrastructure changes

That is the shortest path from the current repo state to a real-money launch.
