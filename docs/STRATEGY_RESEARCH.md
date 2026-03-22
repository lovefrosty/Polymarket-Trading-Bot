# Beyond market making: every automated strategy on Polymarket

**The Polymarket CLOB supports at least seven distinct automated strategies beyond market making, each with documented profitability and varying infrastructure requirements.** Academic research covering 86 million bids confirms that **$40 million in arbitrage profits** were extracted from April 2024 to April 2025, with 14 of the 20 most profitable wallets running bots. The competitive landscape has intensified dramatically — average arbitrage opportunity windows compressed from 12.3 seconds in 2024 to **2.7 seconds** in early 2026, with 73% of profits captured by sub-100ms execution systems. Despite this, structural inefficiencies persist in multi-outcome markets, cross-platform pricing, and information processing speed, creating real opportunities for a developer with existing infrastructure.

---

## 1. Sniping and speed-based strategies exploit information lag

Sniping on Polymarket falls into three distinct categories: **market-open sniping** (buying mispriced initial odds when new markets launch), **order sniping** (exploiting fat-finger errors or temporary orderbook mispricing), and **resolution sniping** (trading right before a contract resolves when the outcome is already determinable). All three exploit the same core dynamic — the CLOB price lags behind reality.

**The flagship example is crypto 15-minute market latency arbitrage.** BTC/ETH/SOL 5-minute and 15-minute Up/Down contracts resolve based on Chainlink Data Streams, but real-time prices are visible on Binance and Coinbase seconds earlier. One well-documented bot reportedly turned **$313 into $414,000 in a single month** exploiting this lag with a 98% win rate, placing $4,000–$5,000 per trade. The "Gabagool" strategy, analyzed in detail on CoinsBench, bought both YES and NO asymmetrically within 15-minute windows — spending $0.966 per guaranteed $1.00 payout for **$58.52 profit per window**.

**News-driven sniping** requires a five-layer architecture: data ingestion (Reuters, AP, Bloomberg APIs, Twitter/X), NLP analysis (ensemble LLM probability estimation), execution (EIP-712 signed CLOB orders), risk management, and monitoring. The most sophisticated implementations run **speech-to-text locally** on live event streams to detect keywords during presidential speeches, firing transactions before the orderbook updates. One profiled AI ensemble bot reportedly generated $2.2 million in two months by processing news faster than the market could reprice.

**Critical infrastructure requirements:**

- Polymarket's servers run on **AWS eu-west-2 (London)**. European traders see 5–15ms latency; US-based traders face 70–160ms. Co-located VPS achieves 1–5ms
- The **500ms taker quote delay was removed on February 18, 2026**, fundamentally changing the competitive landscape. Taker orders now fill immediately, and dynamic taker fees were introduced (peaking at ~1.56% near 50% probability)
- Cancel/replace loops must complete in **under 200ms** to avoid adverse selection. Professional operations target sub-10ms for components they control
- WebSocket endpoints: `wss://ws-subscriptions-clob.polymarket.com/ws/` for orderbook, Polymarket RTDS for crypto prices (no auth required), plus external Binance WebSocket feeds

**The honest assessment on profitability:** The headline-grabbing returns come from the pre-competitive early period. A developer who documented 176 trades on dev.to earned **$2 total over 8 days** — a more representative new-entrant experience. The competitive advantage has shifted heavily toward makers (zero fees + USDC rebates) and away from takers (new dynamic fees). Realistic returns for a competitive sniping operation: **3–8% monthly** for AI probability arbitrage, 8–15% for high-frequency momentum (with correspondingly higher drawdown risk).

**Sniping is the primary adversary of market making.** When breaking news hits or external crypto prices move, informed snipers fill stale market maker quotes at now-wrong prices. The removed 500ms speed bump was specifically designed to protect market makers from this. Running both strategies creates a natural hedge: when news events cause adverse selection losses on your MM positions, your sniping positions generate outsized returns.

---

## 2. Whale tracking and copy trading rely on public blockchain data

Every Polymarket trade executes on Polygon and is fully public. The platform's leaderboard ranks traders by P&L, with the top accounts showing extraordinary returns: **Theo4 at +$22M, Fredi9999 at +$16.6M, and kch123 at +$11M**. Chainalysis research revealed that Theo (the famous French ex-Wall Street trader) controlled up to 11 accounts with a combined profit of **$85 million**, primarily from the 2024 US presidential election. His edge was proprietary — he commissioned custom "neighbor effect" polls in swing states for under $100,000, generating information no amount of copy trading could replicate.

**The ecosystem of tracking tools is mature.** Bravado offers a vertically integrated terminal with automatic position mirroring. Stand.trade provides live trade feeds with PnL filtering — one user reportedly earns **$10,000/month** by watching multiple whale wallets converge on the same market. PolyWatch runs a free Telegram bot alerting on trades >= $25,000. Polymarket's own Data API (`https://data-api.polymarket.com`) provides fully public, unauthenticated access to any wallet's positions, activity, and trade history via `GET /positions?user={address}` and `GET /activity?user={address}`.

**The emerging best practice is the "wallet basket" approach.** After analyzing ~1.3 million Polymarket wallets, researchers found single-whale copying is fragile. The newer method groups 5–10 wallets specializing in the same topic, filters out bots, and **only trades when 80%+ of the basket agrees** on the same outcome. This captures real-time consensus rather than individual personality bets. A surprisingly popular variant is **counter-trading** — filtering by negative P&L and doing the opposite, which Stand.trade added dedicated filtering for due to high demand.

**Latency and edge erosion are the central challenges.** Realistic detection-to-execution latency is 3–10 seconds via API polling, reducible to 1–3 seconds via WebSocket. For slow-moving political markets that develop over days, this is negligible. For crypto 15-minute markets, it's far too slow. Top traders now use **secondary and tertiary accounts** specifically because they know main accounts are being copied immediately. Some run decoy trades. In low-liquidity markets, copy traders collectively become **exit liquidity** for the whale.

**Realistic returns:** 2–5% monthly if following quality wallets in liquid, medium-term political/macro markets with proper risk management. Only **0.51% of Polymarket wallets** have profits exceeding $1,000, and only 7.6–16.8% show any net gain at all. No rigorous published backtest of systematic copy trading returns exists — anecdotal evidence ranges from $847 overnight profits to net losses from slippage in live execution.

**Implementation:** QuickNode maintains a well-documented open-source TypeScript copy trading bot at `github.com/quiknode-labs/qn-guide-examples`. Python developers should use `py-clob-client` for execution and the Data API for wallet monitoring. Critical security note: in December 2025, researchers found **malicious code in a popular GitHub copy trading bot** stealing private keys. Always audit third-party code and use dedicated wallets.

---

## 3. Arbitrage: $40 million extracted, but windows are closing fast

The academic paper "Unravelling the Probabilistic Forest" (Saguillo et al., AFT 2025, arXiv:2508.03474) provides the definitive analysis of Polymarket arbitrage, covering 86 million bids across 17,218 conditions from April 2024 to April 2025. It identified **$40 million in realized arbitrage profit**, broken into three categories with dramatically different characteristics.

**NegRisk multi-outcome arbitrage dominated at $29 million (73% of total).** In Polymarket's neg_risk markets (e.g., "Who will win the election?" with 5+ candidates), each outcome has its own YES/NO orderbook, and all YES prices should sum to $1.00. When they sum to less, buying all YES tokens guarantees a profit. The NegRisk Adapter contract (`github.com/Polymarket/neg-risk-ctf-adapter`) enables conversions between outcome positions. Despite representing only 8.6% of opportunities by count, NegRisk arb showed **29x capital efficiency** over binary arbitrage. Political markets — especially the 2024 election — dominated because retail flow concentrates on 1–2 favorites, leaving complementary probability space thin and mispriced.

**Detection and execution for neg_risk arb:**
1. Pull all active `negRisk: true` events from the Gamma API
2. For each event, fetch current best asks for all YES outcomes
3. If sum(YES best asks) < $1.00, a long arb exists
4. Max position size = minimum depth across all legs
5. Execute as limit orders (maker = zero fees) — critical distinction: use the standard market interface to buy YES shares, not the NegRisk conversion (which costs exactly $1.00)

**Single-condition sum-to-one arbitrage accounted for $10.58 million (27%).** When a binary market's YES + NO prices sum to less than $1.00, buying both guarantees profit at resolution. This occurs regularly during high-volatility events — breaking news creates 30–60 second windows where emotional retail flow spikes YES without NO adjusting simultaneously. Spreads must exceed **2.5–3%** to be profitable after Polymarket's 2% winner fee and gas costs.

**Cross-platform arbitrage between Polymarket and Kalshi** presents consistent 2–5% price spreads but carries severe **resolution risk** — the single biggest danger. During the 2024 government shutdown, Polymarket resolved YES while Kalshi resolved NO on what appeared to be the same event, because resolution criteria differed. The Bitcoin Reserve market showed a 14c gap (Polymarket YES=51c vs Kalshi YES=37c), but Polymarket requires "government holds any amount of bitcoin" while Kalshi requires "designated National Bitcoin Reserve comparable to Strategic Petroleum Reserve." Same event, potentially opposite outcomes. An arXiv paper (2601.01706) analyzing 100,000+ events across 10 platforms confirmed that cross-platform divergence persists due to structural fragmentation and heterogeneous resolution semantics.

**Cross-market correlation arbitrage** exploits logically related markets that should move together. If "Trump wins nomination" = 80% but "Republican wins presidency" = 30%, there's a pricing violation. The IMDEA paper used sentence-transformer embeddings (e5-large-v2) with ChromaDB vector search to identify related markets, then LLMs to extract combinatorial dependencies. However, executed correlation arb was found in only **5 of 13 identified dependent election market pairs** — the opportunity exists but is hard to exploit reliably due to thin liquidity on at least one leg.

**Automation infrastructure:** The open-source `pmxt` library provides a CCXT-style unified wrapper for cross-platform trading. `CarlosIbCu/polymarket-kalshi-btc-arbitrage-bot` on GitHub offers a working Python/Node.js implementation that normalizes prices across platforms and highlights opportunities. EventArb.com and GetArbitrageBets.com provide real-time cross-platform arbitrage calculators.

---

## 4. Directional trading with edge targets structural market biases

The most rigorous analysis of prediction market mispricing comes from Becker's study of **72.1 million trades and $18.26 billion in volume** on Kalshi, which found that takers show -1.12% average excess return while makers show +1.12% — but the gap varies enormously by category. **Entertainment markets show a 4.79 percentage point taker-maker gap, media/world events show 7.28–7.32pp, while finance markets show only 0.17pp.** This directly identifies where directional edge exists: the less data-driven and more subjective the market, the greater the inefficiency.

**Three structural biases create systematic edge:**

- **Favorite-longshot bias (FLB):** Contracts priced at 5c win only 4.18% of the time (should be 5%). All contracts below 20c underperform their implied probability; all above 80c outperform. This is exploitable by systematically selling extreme longshots
- **YES bias:** Takers disproportionately buy YES at longshot prices. NO contracts outperform YES at **69 of 99 price levels**. At 1c, YES has -41% expected value while NO has +23%
- **Recency bias:** Markets overweight recent information, creating 72-hour overcorrection patterns after major developments

**Frontier LLMs achieve Brier scores of ~0.135** (o3 model), outperforming human crowd baselines (0.149) but underperforming expert forecasters (0.023). The critical finding from academic research: model profitability was driven "almost entirely by questions on which the market was most unsure (probability 40–60%)." For these uncertain markets, model bets were successful **11.8 percentage points more often** than market-implied probability. The open-source `Polymarket/agents` framework (2.5K GitHub stars) provides a ready-made LLM-based trading agent using LangChain and ChromaDB.

**Kelly criterion position sizing is essential.** For a market priced at 45c where your model estimates 60% true probability, full Kelly suggests 27% of bankroll — but practitioners universally recommend **quarter-Kelly to half-Kelly** to account for model uncertainty. The formula for binary prediction markets: `kelly = (p * b - q) / b` where `b = (1/market_price) - 1`, `p` = your estimated probability, `q = 1 - p`.

**The "informed market maker" approach** is the most natural extension for someone already running an MM bot. Academic work by Cartea and Wang (2020, IJTAF/Oxford) provides the framework: when your alpha signal is positive (upward price move expected), stop posting sell limit orders and send aggressive buy market orders. When neutral, maintain standard two-sided quotes. When signal is weak (<5% divergence), maintain two-sided quotes with slight asymmetry. This captures spread income during quiet periods AND directional alpha during information events — the best of both worlds.

---

## 5. Event-driven strategies exploit predictable volatility windows

**FOMC markets are the single most liquid recurring event-driven opportunity** on Polymarket, with individual meeting markets reaching $58M–$393M in volume. The strategy is straightforward: compare Polymarket implied probabilities against your macro model, the CME FedWatch tool, and cross-reference with Kalshi. Pre-meeting, markets are efficient for consensus outcomes but often misprice tail scenarios (50bps vs 25bps moves). Each CPI release, jobs report, and GDP figure shifts FOMC probabilities, creating catalytic trading windows. As of March 2026, the June 2026 meeting shows 63% no-change vs 30% for a 25bps decrease — the uncertainty creates a rich opportunity set.

**Pre-event volatility harvesting** is the prediction market equivalent of options selling. The FLB data from 72.1 million trades confirms that contracts at 5c win only 4.18% — systematically selling these overpriced longshots before events (when uncertainty premium inflates them further) is structurally profitable. Implementation: identify tail-risk outcomes priced above 5c where your model assigns <3% probability, sell NO shares, and collect the premium when the longshot fails to materialize.

**Post-event momentum** exploits the 30-second to 5-minute window after breaking news where Polymarket hasn't fully repriced. Markets show a documented "reverse favorite-longshot bias" after surprise events — the initial price move underreacts, creating a momentum window. The playbook: monitor news APIs, run ensemble AI assessment within seconds, enter if divergence exceeds 10%, and exit within 5–15 minutes. Correlated markets adjust with even longer lag — if "Trump wins nomination" resolves, "Republican wins presidency" takes minutes to hours to fully reprice, creating one of the most consistent edge sources.

**Calendar-based strategies for recurring markets:**
- Crypto 5-minute/15-minute Up/Down markets run continuously (288 or 96 markets per day), creating hundreds of daily opportunities for latency-based strategies
- Monthly economic releases (CPI, NFP, GDP) shift FOMC probabilities and can be pre-positioned around
- Congressional votes, executive orders, and court decisions follow trackable legislative calendars

---

## 6. Resolution sniping targets the oracle delay window

**Chainlink-resolved markets** (crypto price contracts) offer the tightest resolution sniping window. Chainlink Data Streams deliver low-latency, timestamped prices, and Chainlink Automation triggers on-chain settlement automatically. The exploitable window is in the **final 10–30 seconds** before automated settlement fires — BTC price direction is approximately 85% determined 10 seconds before the window closes, but Polymarket odds haven't fully adjusted. Place maker orders at $0.90–$0.95 on the likely winning side, collect $0.05–$0.10 per contract at zero maker fees plus rebates.

**UMA-resolved markets** (political, sports, geopolitical) offer a much wider window. After a proposer stakes a $750 USDC bond on an outcome, there's a **2-hour challenge period**. If disputed twice, it escalates to UMA's DVM voter system for **48–72 hours**. The market often remains open for trading during this entire period, creating extended opportunities to trade on the known outcome while waiting for formal resolution. Approximately 98.5% of UMA requests go undisputed.

**The primary risk is oracle manipulation.** In March 2025, a UMA token whale cast 5 million UMA tokens (25% of total votes) through 3 accounts to falsely resolve the Ukraine mineral deal market, causing ~$7 million in losses. This single event demonstrates the concentrated voting power vulnerability in UMA governance. Polymarket has never overridden UMA, though it maintains admin powers to reset, pause, or emergency-resolve markets.

---

## 7. The rewards program pays market makers three ways simultaneously

Polymarket now operates **three separate reward mechanisms** that stack:

**Liquidity Rewards** use a quadratic scoring formula that exponentially rewards tightness to the midpoint. The exact formula: `S(v, s) = ((v - s) / v)^2 * b`, where `v` = max incentive spread and `s` = your order's distance from midpoint. An order 1c from midpoint in a 3c max-spread market scores **4x more** than an order 2c away. Orders are sampled every minute, with rewards distributed daily in USDC. Your total score equals the minimum of your two side scores (with a 1/3 adjustment for single-sided in the 0.10–0.90 price range), meaning your reward is entirely constrained by your weakest side.

**Maker Rebates** redistribute collected taker fees to makers daily. Introduced January 2026 on 15-minute crypto markets, expanded to NCAAB and Serie A in February 2026. Performance-based: you earn proportional to the liquidity you provided that actually got filled.

**Holding Rewards** pay **4% APY** on eligible positions in long-term political/geopolitical markets (2028 Presidential Election, 2026 Midterms, geopolitical leader outcome markets), funded by Polymarket Treasury.

**Optimization strategy:** The quadratic formula means the single biggest lever is quoting as tight as possible — being twice as tight approximately quadruples your score. Balance both sides equally (your score = min of sides). Maximize uptime across all 10,080 minute-samples per weekly epoch. Stack all three reward types in eligible markets. The poly-maker creator reported **$200–300/day with $10K capital** during peak 2024, scaling to $700–800/day at higher capital. Current realistic estimates are lower: approximately **10% annualized** combining spread capture plus rewards in long-dated stable markets, with competition having increased significantly since 2024 when there were only 3–4 serious liquidity providers platform-wide.

---

## 8. Combining strategies into an anti-correlated portfolio

The most powerful insight from this research is that **market making and news sniping are naturally anti-correlated** — when volatile events cause adverse selection losses on MM positions, sniping positions generate outsized returns. Running both creates a hedge that reduces drawdowns while maintaining returns.

**Practical portfolio allocations used by practitioners:**

- **Conservative (targeting 1–3% monthly):** 50% market making across stable political markets, 30% correlation/structural arbitrage, 20% cash reserve
- **Balanced (targeting 3–6% monthly):** 30% market making, 30% AI-powered directional trading, 20% correlation arbitrage, 20% cash
- **Aggressive (targeting 6–12% monthly):** 20% market making, 30% AI probability trading, 25% latency/momentum sniping, 15% cross-platform arbitrage, 10% cash

**Strategy interactions to understand:** Market making and liquidity reward farming are perfectly complementary — identical activity, dual income streams. Correlation arb identifies mispriced markets where MM spreads tend to be wider, feeding the MM strategy. Copy trading conflicts with market making because copy trading follows trends while MM provides counter-trend liquidity. Multiple HFT arb strategies on the same markets create self-competition.

Capital allocation should follow the principle: market making capital stays permanently deployed, while arb/sniping capital sits in reserve and deploys opportunistically. **Position merging** via Polymarket's CTF split/merge operations is critical for capital efficiency when running multiple strategies simultaneously.

---

## 9. Infrastructure and tools for multi-strategy automation

The core Python stack for automated Polymarket trading centers on three official libraries. **`py-clob-client`** (v0.34.6, 915+ GitHub stars) handles all CLOB interactions — orderbook queries, order placement, cancellation. **`polymarket-apis`** on PyPI provides a unified Pydantic-validated wrapper across CLOB, Gamma, Data, Web3, and WebSocket clients. For production-grade systems, **NautilusTrader** (`pip install "nautilus_trader[polymarket]"`) offers a professional Rust/Cython-core algorithm trading platform with identical backtesting and live execution code, built-in Polymarket adapters, and automatic WebSocket connection management.

**Open-source bot frameworks worth studying:**
- `warproxxx/poly-maker` — The reference MM bot (900 stars, 370 forks), uses Google Sheets for configuration, includes position merging module
- `ent0n29/polybot` — Full Java/Spring Boot microservices architecture with Kafka data pipeline, ClickHouse analytics, Grafana monitoring, Slack alerts, and paper trading mode
- `Polymarket/agents` — Official AI agent framework (2.5K stars) using LangChain and ChromaDB for autonomous trading
- `discountry/polymarket-trading-bot` — Python bot with flash crash strategy for 15-minute markets and real-time WebSocket integration

**Per-strategy infrastructure requirements:** Market making needs medium latency (seconds) and a $5–60/month VPS monitoring 100+ markets. Structural arbitrage demands critical latency (<100ms), dedicated VPS near Polygon nodes, and private RPC access — Rust is preferred over Python. AI/news arbitrage requires GPU compute or LLM API costs ($50–300/month). Copy trading works on basic infrastructure with 1–5 second detection latency. Polygon gas costs are negligible at ~$0.007 per transaction.

**There are no native stop-losses, position limits, or circuit breakers on Polymarket** — all risk management must be built into bot logic. Essential controls include inventory limits (never >30% exposure on one side), dynamic spread widening during volatility, automatic liquidity withdrawal before scheduled events, maximum position sizes per market (10% of portfolio), and cross-market exposure tracking.

---

## What's actually working right now and where the edge lives

Polymarket's monthly volume recovered from an 84% post-election crash to exceed **$3 billion by October 2025** — surpassing election-era peaks through diversification into sports (now >60% of open interest), short-duration crypto markets, and policy/macro events. ICE (NYSE owner) invested $2 billion at an $8–9 billion valuation. The CFTC probe ended. U.S. re-entry via the QCX acquisition is underway. A token airdrop has been confirmed by Polymarket's CMO.

**The February 2026 fee restructuring was the biggest meta shift.** The removal of the 500ms taker delay and introduction of dynamic taker fees killed the old taker-arbitrage meta overnight — half of existing bots broke. The new meta favors maker-based strategies: zero fees plus USDC rebates plus rewards. For someone already running a profitable MM bot, this is favorable.

**The strategies with the most evidence of current profitability, ranked by accessibility for an existing MM operator:**

1. **Rewards optimization on your existing bot** — Immediate, requires only tuning quote tightness and balancing per the quadratic formula. Stack liquidity rewards + maker rebates + holding rewards
2. **NegRisk multi-outcome arbitrage** — The largest source of arb profit ($29M of $40M total), requires monitoring multi-outcome markets via Gamma API and executing when YES prices sum below $1.00
3. **Informed market making** — Integrate AI probability signals to skew quotes directionally. The Cartea & Wang framework is directly applicable
4. **Crypto 15-minute latency sniping** — Exploit the oracle lag between real-time exchange prices and Polymarket resolution. Requires low-latency infrastructure and careful risk management
5. **Cross-market correlation arbitrage** — Less competitive because it requires NLP/semantic analysis to identify related markets, creating a higher barrier to entry

The honest bottom line: **92.4% of Polymarket traders lose money**. The 7.6% who win are overwhelmingly automated. Simple arbitrage is a declining opportunity — windows last 2.7 seconds and sub-100ms bots capture 73% of profits. But structural inefficiencies in multi-outcome markets, category-specific biases (entertainment and world events markets show 5–7x more mispricing than finance), and the natural anti-correlation between market making and information-driven strategies create a real, compounding portfolio opportunity for a technically sophisticated operator willing to build and maintain the infrastructure.
