# REST Polling vs WebSocket: Performance Analysis

## TL;DR
**No, you're not giving up meaningful performance with REST polling at 5 req/s.**

For your use case (binary options market-making, not high-frequency trading), REST polling is actually **the better choice**. WebSocket would add complexity with marginal returns.

---

## Latency Comparison

### REST Polling (Current: 1s interval)
```
Timeline per orderbook update:
└─ Bot wakes up at t=0.00s
   ├─ Request orderbook: 50-100ms (network roundtrip)
   ├─ Parse JSON: <5ms
   ├─ Update BookManager: <5ms
   └─ Ready to quote at t=0.15s

Next update at t=1.00s
Latency: ~150ms per book update
Staleness: 0-1s (varies with polling interval)
```

### WebSocket (Ideal case)
```
Timeline when orderbook changes:
└─ Kalshi server publishes update at t=0.00s
   ├─ Network transmission: 20-50ms
   ├─ Parse JSON: <5ms
   ├─ Update BookManager: <5ms
   └─ Ready to quote at t=0.05s

Latency: ~50ms per book update
Staleness: <50ms max
```

### Performance Gap
- **REST polling loses:** ~100ms latency per update
- **WebSocket gains:** ~10-20x lower latency for market changes

---

## But: Does This Matter for Binary Options MM?

### Market Characteristics
- **Order size:** Typically $1-100 per order
- **Spreads:** 1-3 cents (wider than equity options)
- **Tick time:** Markets move on **minutes/hours**, not milliseconds
- **Competition:** Mostly retail traders, not aggressive MM algorithms

### Your Strategy Impact
Binary options market-making doesn't require:
- ❌ Sub-millisecond response times
- ❌ Aggressive order cancellation/replacement
- ❌ Real-time gamma hedging
- ❌ Latency arbitrage

### Latency Requirement for Your Bot
```
Best case: Quote within 150ms of market change
Market move: ±5% (50 bps) over 5 minutes
Your response time: 150ms = 0.003% of market movement window

Conclusion: REST polling (150ms) is FAST ENOUGH for binary MM
```

---

## Real Cost Analysis

### REST Polling @ 1s Interval (Current)

**Costs:**
- Implementation: ✅ Already done
- Complexity: ✅ Simple async loop
- Reliability: ✅ No connection state to maintain
- Rate limit usage: 5 req/s (25% of 20 req/s available)
- Bandwidth: ~1-2 KB/request × 5 = 5-10 KB/s

**Performance:**
- Orderbook age: 0-1s (avg 0.5s)
- Update latency: ~150ms
- Miss urgent fills: None (polling catches them)

---

### WebSocket (If You Implemented It)

**Costs:**
- Implementation time: 4-8 hours
- Complexity:
  - Connection state machine (connect → auth → subscribe → listen)
  - Message framing and sequencing
  - Reconnection logic with exponential backoff
  - Orderbook delta reconstruction (can't use snapshots directly)
  - Out-of-order message handling
  - Heartbeat/ping-pong management
- Testing: Additional 40+ tests
- Debugging: Harder to reproduce connection issues
- Maintenance: More code surface area

**Performance gains:**
- Orderbook age: 0-50ms (avg 25ms)
- Update latency: ~50ms
- Avoid missed fills: Already handled by async fill poller

---

## Where WebSocket Would Help (And Where It Wouldn't)

### ✅ WebSocket Would Help If:
1. **You were running HFT strategies** (millisecond response needed)
   - Not your case

2. **You were market-making 100+ simultaneous markets**
   - Polling 100 × 5 req/s = 500 req/s (way over 20 req/s limit)
   - Your current approach: 1-5 markets simultaneously

3. **You needed sub-second order cancellation**
   - Aggressive rebalancing on inventory
   - Your case: Posting & leaving orders for minutes

### ❌ WebSocket Wouldn't Help With:
1. **Order placement latency** — Still same HTTP roundtrip (WebSocket doesn't speed up order API)
2. **Fill ingestion** — You'd still need to poll fills endpoint (Kalshi doesn't stream fills over WS efficiently)
3. **Market discovery** — Markets change infrequently, polling fine
4. **Position tracking** — WebSocket can stream positions but REST polling is simpler

---

## Decision Matrix: Should You Switch?

| Factor | REST (Current) | WebSocket |
|--------|---|---|
| **Time to implement** | ✅ 0 hours (done) | ❌ 6-8 hours |
| **Latency** | ✅ 150ms | ✅ 50ms (10x better, irrelevant gain) |
| **Reliability** | ✅ Mature | ⚠️ More complex |
| **Scalability** | ⚠️ Polling 5 markets | ✅ Could handle 20 markets |
| **Testability** | ✅ Synchronous (easy) | ⚠️ Async state (harder) |
| **Debugging** | ✅ Simple (replay polled data) | ❌ Hard (connection state) |
| **Code maintenance** | ✅ ~100 lines | ❌ ~400 lines |
| **Rate limit safety** | ✅ 25% utilization | ✅ <5% utilization |
| **Cost/benefit ratio** | ✅ 0 hours / huge value | ❌ 8 hours / marginal gain |

---

## What You Should Do Instead of WebSocket

If you want to improve performance **in order of ROI:**

### Priority 1: Reduce Order Placement Latency (Easy, High Impact)
```python
# Current: ~200ms per order (HTTP roundtrip)
# Current code placement latency

# Optimization: Batch order placement
# Cost: 1-2 hours
# Gain: Place 10 orders in 200ms instead of 10×200ms = 50 req/s → 1 req
```
**Impact: 10x faster order execution**

### Priority 2: Optimize Orderbook Parsing (Trivial, Low Impact)
```python
# Current: Full 20-level parse every poll
# Optimization: Cache unchanged levels, only parse deltas

# Cost: 2-3 hours
# Gain: JSON parse time 50ms → 5ms
```
**Impact: 10% latency improvement**

### Priority 3: Tighten Quote Refresh Loop (Easy, Medium Impact)
```python
# Current: 1s polling interval
# Optimization: 500ms polling interval (still well within rate limit)

# Cost: 0 hours (just change 1 parameter)
# Gain: Orderbook age 0-1s → 0-0.5s
```
**Impact: 2x faster staleness reduction, zero cost**

### Priority 4: Add Batch Order Cancellation (Medium, High Impact)
```python
# Current: Cancel orders one-by-one
# Optimization: Batch cancel (Kalshi supports this)

# Cost: 1 hour
# Gain: Cancel 10 orders in 200ms instead of 2000ms
```
**Impact: 10x faster rebalancing**

### Priority 5: WebSocket Streaming (Hard, Marginal Impact)
```python
# Cost: 8 hours
# Gain: Latency 150ms → 50ms (irrelevant for binary MM)
```
**Don't do this yet. Do the above first.**

---

## Historical Context: When WebSocket Makes Sense

WebSocket became popular for trading bots because:
1. **Equity/crypto markets move fast** (sub-second)
2. **Exchanges provided WebSocket as primary API** (e.g., Binance)
3. **Latency arbitrage required sub-100ms response** (HFT)

**But Kalshi is different:**
- Binary markets move on timescales of minutes/hours
- Kalshi provides REST as primary API (WebSocket is secondary)
- Your competition is retail traders, not algorithms
- You have plenty of rate limit headroom

---

## The Real Constraint: Order Placement Latency

Here's what actually matters for market-making:

```
Your bot flow:
1. Fetch orderbook (REST): 150ms
2. Run strategy loop: <1ms
3. Place order (HTTP): 200ms ← THIS IS YOUR BOTTLENECK
4. Receive fill: async, 50-200ms

Total: ~400ms from book snapshot to execution

This latency is DOMINATED by order placement (200ms),
not orderbook polling (150ms).

Switching to WebSocket saves only 100ms (~25% improvement).
Batch order placement saves 150ms per additional order (10x gain per order after first).
```

---

## My Recommendation

### ✅ DO THIS FIRST (Quick Wins)
1. **Drop polling interval to 500ms** (currently 1s)
   - Cost: 0 hours (change 1 line)
   - Gain: 2x faster book staleness
   - Rate limit: Still 10 req/s (50% headroom)

2. **Implement batch order placement** (if you quote multiple symbols)
   - Cost: 1-2 hours
   - Gain: 10x faster multi-order execution
   - Payoff: Immediate when scaling to 3+ markets

3. **Add batch cancellation** (for rebalancing)
   - Cost: 1 hour
   - Gain: 10x faster position adjustment
   - Payoff: High when inventory drifts

### ❌ DON'T DO THIS YET
WebSocket streaming (save for later, if needed):
- Cost: 8 hours
- Gain: 100ms latency improvement (not decisive for binary MM)
- Payoff: Only if scaling to 20+ simultaneous markets
- Blocker: Fills still need async polling (WebSocket doesn't help there)

### ✅ DO LATER (Strategic)
Once you have profitable runs on Kalshi:
- A/B test 500ms vs 1s polling to measure actual edge
- If edge requires sub-50ms response: then add WebSocket
- If edge is stable at current latency: leave it alone

---

## Summary

**Performance Question:** "Am I giving up performance at 5 req/s?"

**Answer:** No, you have headroom.
- Kalshi limit: 20 req/s read
- Your usage: 5 req/s (25% utilization)
- Orderbook age: 0-1s (totally acceptable for binary MM)
- Order placement: Still the bottleneck (200ms), not orderbook polling (150ms)

**WebSocket Decision:** Don't implement yet.
- Setup time: 8 hours
- Latency gain: 100ms (10% of total round-trip)
- Relevance: Low (binary MM doesn't move that fast)
- Better ROI: Batch orders, tighter polling, order cancellation

**What to do now:**
1. Reduce polling to 500ms (0 hours, 2x improvement)
2. Implement batch orders (2 hours, 10x improvement at scale)
3. Trade successfully and measure real edge
4. If latency becomes limiting, implement WebSocket

---

## Confidence: 🟢 STICK WITH REST POLLING

Your current implementation is correctly sized for the Kalshi market environment. Premature optimization toward WebSocket would consume time better spent on strategy validation and market-making parameter tuning.
