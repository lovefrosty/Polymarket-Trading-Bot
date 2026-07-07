# Polymarket Counterparty Flow Signal

Last updated: 2026-05-10
Status: Research design

## Core Idea

Adverse selection is not just price movement. It can be a counterparty problem:
the bot may be filled by traders who are consistently better informed in a
specific market type.

The signal should answer:

> If this wallet or trader cluster is aggressively taking liquidity in this
> market, should we widen, cancel, or avoid quoting that side?

This is a public-data research signal. It should not identify humans or infer
private identity. It should score public wallet/proxy addresses and public
trading behavior.

## What Is Available

Polymarket has several layers of data:

- Market WebSocket: public orderbook snapshots, price-level changes, last trade
  prices, and best bid/ask updates. This is L2-style book data, not full
  individual order identity.
- User WebSocket: authenticated updates for your own orders and trades.
- CLOB trade APIs: authenticated trade records can include fields such as
  maker address, transaction hash, taker order id, side, size, price, and market.
- Data API: public profile, activity, positions, holder data, traded-market
  counts, leaderboards, and market/user trade resources.
- On-chain Polygon data: Polymarket CTF outcome tokens are ERC1155 positions;
  trades, balances, positions, and redeems can be analyzed through providers
  such as Goldsky, Dune, Allium, CryptoHouse, or direct RPC indexing.

Important correction: this is Polygon/on-chain CTF data, not Chainlink data.
Resolution uses UMA-related contracts; Chainlink is not the primary data layer
for this idea.

## Why This Matters

A normal orderbook imbalance says:

```text
more bid depth than ask depth
```

A counterparty-aware signal asks:

```text
who is causing the flow, and are they usually right in this market family?
```

That matters for market making because a fill from a strong counterparty is more
likely to have negative markout. If a wallet has a history of profitable,
early, sizeful trades in similar sports, crypto, or politics markets, their
taker flow should increase adverse-selection risk.

## Candidate Features

Per wallet or cluster:

- total markets traded
- volume by category, tag, and market type
- realized PnL or resolved win rate where computable
- average markout after their trades
- speed after news events
- size percentile in the market
- aggressor side: taker buy or taker sell
- concentration by token, event, and topic
- recency-weighted profitability
- whether flow is directional or hedging/market-making inventory transfer

Per market:

- wallet concentration in recent taker flow
- top taker share of volume
- informed-flow score by side
- new-wallet versus experienced-wallet volume
- cross-market wallet activity before price moves

## Trading Use

This should feed the adverse-selection gate, not directly choose trades.

Allowed effects:

- widen quotes
- cancel stale quotes faster
- reduce size
- block new risk on the side being taken by high-skill flow
- increase the adaptive boundary score

Not allowed:

- override kill switches
- expand exposure caps
- dox or infer real-world identity
- trade solely because a wallet traded

## First Build Slice

1. Add an offline `counterparty_flow` table.
2. Start with public Data API and on-chain exports rather than live execution.
3. Normalize wallet/proxy addresses, market id, token id, side, size, price,
   timestamp, and transaction hash.
4. Label historical trades by later price movement and final resolution.
5. Build a `counterparty_adverse_selection_score` by market, side, and wallet
   cluster.
6. Log the score beside each paper quote.
7. Only after backtesting, feed it into `evaluate_tail_adverse_selection`.

## Research Questions

- Does wallet-level taker flow predict short-horizon markout?
- Is the signal stronger in sports props, politics, or crypto markets?
- Does it matter more near `0.10`/`0.90` than near `0.50`?
- Can we distinguish informed takers from liquidity recycling or arbitrage?
- Does wallet clustering improve signal quality versus single-wallet scoring?

## Decision

This is a strong research direction. The first production-safe version is a
read-only counterparty-flow sidecar that produces a timestamped risk score. It
should be validated against markout and final resolution before it can affect
live quoting.
