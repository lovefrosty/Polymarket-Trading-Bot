# Owner Understanding Checklist

Last updated: 2026-03-25
Status: Required pre-live operator checklist

## Standard

Before going live, you should be able to explain a full decision cycle without
guessing:

- why the market was selected
- what fair value the bot believed
- what fee-adjusted edge it believed
- why it chose its size
- why it stayed passive, skewed, unwound, force-flattened, or killed

If you cannot do that, then you are still delegating too much strategic
judgment to the code.

## What You Must Understand

### 1. Market Selection

You should be able to answer:

- Why did this market win?
- Why did the next two candidates lose?
- Which gate rejected the bad markets: one-sided book, price range, spread,
  liquidity score, family filter, or proximity?
- Which fields in the operator surface show that rationale today?

Primary code paths:

- [core_mm/kalshi/market_selector.py](../core_mm/kalshi/market_selector.py)
- [core_mm/runner.py](../core_mm/runner.py)
- [core_mm/operator_service.py](../core_mm/operator_service.py)

### 2. Fair Value Formation

You should be able to answer:

- What is `p_fair` in the current bot?
- How much of it comes from microstructure versus external spot context?
- Is the bot leaning because of real conviction or just short-horizon book
  state?

Primary code paths:

- [core_mm/main_loop.py](../core_mm/main_loop.py)
- [core_mm/alpha_overlay.py](../core_mm/alpha_overlay.py)
- [config/settings.py](../config/settings.py)

### 3. Fees And True Edge

You should be able to answer:

- What fee model is active for the selected Kalshi market?
- Is the fee coming from exchange-reported fill data or model fallback?
- What minimum edge survives after fees?
- Which fills would still have been good trades at zero fees but become bad
  after fees?

Primary code paths:

- [core_mm/kalshi/fees.py](../core_mm/kalshi/fees.py)
- [core_mm/live_broker.py](../core_mm/live_broker.py)
- [core_mm/paper_broker.py](../core_mm/paper_broker.py)

### 4. Size Formation

You should be able to answer:

- Which limiter actually bound the final buy or sell amount?
- Was size constrained by trade size, inventory skew, risk budget,
  affordability, Kelly, or hard cap?
- Why would the bot quote smaller even if the nominal edge looks attractive?

Primary code path:

- [core_mm/sizing.py](../core_mm/sizing.py)

### 5. Risk Ladder

You should be able to answer:

- What causes the transition from normal quoting to `SKEW`?
- What causes `UNWIND` instead of more passive exit?
- What causes `FORCE_FLAT` or kill?
- When is paying fees to reduce risk the correct behavior?

Primary code paths:

- [core_mm/risk_manager.py](../core_mm/risk_manager.py)
- [core_mm/main_loop.py](../core_mm/main_loop.py)
- [docs/TRADING_POLICY_GOALS.md](./TRADING_POLICY_GOALS.md)

### 6. Hedge Reality

You should be able to answer:

- Is hedge part of the live safety case right now?
- If hedge does not fire, what is the actual fallback?
- Is the current hedge machinery an alpha strategy or an inventory control
  exception?

Primary code paths:

- [core_mm/hedge_engine.py](../core_mm/hedge_engine.py)
- [docs/BTC_KALSHI_GO_LIVE_CRITERIA.md](./BTC_KALSHI_GO_LIVE_CRITERIA.md)

### 7. Local-Latency Harm

You should be able to answer:

- What strategies become uncompetitive because you are local-only?
- Which losses would indicate adverse selection from slow updates rather than a
  bad thesis?
- Why is passive MM still plausible locally while HFT-style trading is not?

Primary grounding:

- [core_mm/kalshi/market_feed.py](../core_mm/kalshi/market_feed.py)
- [scripts/run_core_mm.py](../scripts/run_core_mm.py)
- [docs/LOCAL_ONLY_LAUNCH_STRATEGY_TRIAGE.md](./LOCAL_ONLY_LAUNCH_STRATEGY_TRIAGE.md)

## Questions To Ask After Every Fill Or Bad Run

- Why was the market selected at all?
- What did the bot believe `p_fair` was at the time?
- What was the after-fee edge, not the gross edge?
- What limiter controlled size?
- Was this fill desirable spread capture or adverse selection?
- Was the loss caused by bad selection, bad fair value, bad size, bad fees, or
  bad inventory handling?
- If hedge did nothing, was the strategy still safe?
- Was the result repeatable, or did paper conditions flatter the strategy?

## Failure Modes That Mean You Do Not Yet Understand The Bot Well Enough

- You can describe the outcome, but not the reason code or state transition.
- You know the PnL, but not whether it survived fees and slippage honestly.
- You know the selected market, but not why nearby candidates were rejected.
- You see `UNWIND` or `FORCE_FLAT`, but cannot explain what specific trigger
  caused it.
- You describe hedge as important, even though the current launch standard does
  not credit hedge.
- You treat paper profitability as proof without being able to attribute where
  the edge actually came from.

## Pre-Live Self-Test

Do not go live until you can take any recent run and answer all of the
following from the repo and runtime surfaces:

1. Why was this market chosen?
2. What exact fee assumptions applied?
3. What fair value did the bot trade against?
4. Why was this the chosen size?
5. Why did the risk ladder choose this exit path?
6. What would have happened if hedge never existed?
7. Would this profit still count as robust after fees, slippage, and local
   latency constraints?

That is the minimum operator-understanding bar for the first live deployment.
