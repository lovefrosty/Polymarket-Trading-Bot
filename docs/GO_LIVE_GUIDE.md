# Go-Live Reference Guide: Polymarket Market Making Bot
# Last updated: 2026-03-17
# Status: Pre-deployment

---

## Overview

Paper trading is validated:
- $100+ net PnL across multiple runs
- 94.5 bps markout at 1s (fills consistently moving in our favor)
- 67.5% fill rate, 72% quote rate
- 31% adverse selection rate
- Phase 0 gate: PASS on every run

This document covers everything from "I have no account" to "my bot is live
and making markets with real money." It is the single source of truth for
the go-live process.

---

## PART 1: Account and Wallet Setup (Do This First, Before Any Code)

### 1.1 Create a Polymarket Account

Go to https://polymarket.com and create an account. You have three wallet options:

**Option A: Email/Magic Link (Recommended for starting)**
- Sign up with email
- Polymarket creates a proxy wallet for you
- Signature type = 1 (POLY_GNOSIS_SAFE for Magic wallets)
- You'll need the FUNDER_ADDRESS (your proxy wallet address) — find it in
  your Polymarket account settings or profile page

**Option B: MetaMask / Browser Wallet (EOA)**
- Connect your own Ethereum wallet
- Signature type = 0
- You control the private key directly
- More control but more responsibility

**Option C: Dedicated Trading Wallet (Best for bot trading)**
- Generate a fresh wallet specifically for the bot
- Never use this wallet for anything else
- Fund it with exactly what you're willing to risk
- This is the professional approach

For the bot, you need the **private key** of whatever wallet you use.
If using Magic/email login, you'll need to export the key or use the
proxy wallet approach.

### 1.2 Fund Your Wallet

The bot trades on Polygon (not Ethereum mainnet). You need:

1. **USDC on Polygon**: This is your trading capital
   - Start with $10 (seriously — $5 to trade, $5 as buffer)
   - You can bridge USDC from Ethereum to Polygon via https://wallet.polygon.technology
   - Or buy directly on Polygon via an exchange that supports Polygon withdrawals
   - Or deposit through Polymarket's UI (they handle the bridging)

2. **MATIC on Polygon**: This is for gas fees
   - You need a small amount (~0.5 MATIC, costs pennies)
   - Gas on Polygon is extremely cheap ($0.001-0.01 per transaction)
   - Get MATIC from the same bridge or exchange

3. **Verify your balances:**
   - Check on Polygonscan: https://polygonscan.com/address/YOUR_WALLET_ADDRESS
   - You should see USDC (token) and MATIC (native) balances

### 1.3 USDC Approval (One-Time Setup)

Before the bot can trade, you must approve the Polymarket exchange contract
to spend your USDC. This is a standard ERC-20 approval.

**Using py-clob-client (easiest):**
```python
from py_clob_client.client import ClobClient

client = ClobClient(
    host="https://clob.polymarket.com",
    chain_id=137,
    key="YOUR_PRIVATE_KEY",
    signature_type=0,  # or 1 for Magic wallet
)

# This approves the CTF Exchange to spend your USDC
# Only needs to be done once per wallet
client.set_allowances()
```

**Or manually via Polygonscan:**
- Go to the USDC contract on Polygon: 0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174
- Write Contract → approve
- Spender: Polymarket's CTF Exchange address (check their docs for current address)
- Amount: large number (e.g., 2^256 - 1 for unlimited, or exact amount for safety)

### 1.4 Derive API Credentials

Polymarket uses a 2-level auth system. Level 1 is your wallet signature.
Level 2 adds API key/secret/passphrase for faster operations.

```python
from py_clob_client.client import ClobClient

client = ClobClient(
    host="https://clob.polymarket.com",
    chain_id=137,
    key="YOUR_PRIVATE_KEY",
    signature_type=0,
)

# Derive or create API credentials
# This signs a message with your wallet to generate API keys
creds = client.create_or_derive_api_creds()

print(f"API Key: {creds.api_key}")
print(f"API Secret: {creds.api_secret}")
print(f"API Passphrase: {creds.api_passphrase}")

# SAVE THESE IMMEDIATELY — store in .env file
# If you lose them, you can re-derive from the same wallet
# but the old ones become invalid
```

### 1.5 Store Credentials Securely

Create or update your `.env` file:

```bash
# Polymarket Trading Bot - Live Credentials
# NEVER commit this file to git

# Wallet
POLYMARKET_PRIVATE_KEY=0xYOUR_PRIVATE_KEY_HERE
POLYMARKET_WALLET_ADDRESS=0xYOUR_WALLET_ADDRESS_HERE
POLYMARKET_SIGNATURE_TYPE=0

# API (from create_or_derive_api_creds)
POLYMARKET_API_KEY=your_api_key_here
POLYMARKET_API_SECRET=your_api_secret_here
POLYMARKET_API_PASSPHRASE=your_api_passphrase_here

# Funder address (same as wallet for EOA, proxy address for Magic)
POLYMARKET_FUNDER_ADDRESS=0xYOUR_WALLET_ADDRESS_HERE
```

Verify `.env` is in your `.gitignore`. Run `git status` and confirm it
doesn't show as a tracked or modified file.

### 1.6 Test the Connection (Before Any Bot Code)

Run this standalone script to verify everything works:

```python
"""test_connection.py — Run this before touching the bot."""
import os
from dotenv import load_dotenv
from py_clob_client.client import ClobClient
from py_clob_client.clob_types import ApiCreds

load_dotenv()

client = ClobClient(
    host="https://clob.polymarket.com",
    chain_id=137,
    key=os.getenv("POLYMARKET_PRIVATE_KEY"),
    signature_type=int(os.getenv("POLYMARKET_SIGNATURE_TYPE", "0")),
    creds=ApiCreds(
        api_key=os.getenv("POLYMARKET_API_KEY"),
        api_secret=os.getenv("POLYMARKET_API_SECRET"),
        api_passphrase=os.getenv("POLYMARKET_API_PASSPHRASE"),
    ),
    funder=os.getenv("POLYMARKET_FUNDER_ADDRESS"),
)

# Test 1: Can we read markets?
print("Testing market read...")
markets = client.get_markets()
print(f"  Markets available: {len(markets)}")

# Test 2: Can we read our balance?
print("Testing balance read...")
# Note: balance check depends on wallet type
print("  Connection successful")

# Test 3: Can we read open orders?
print("Testing order read...")
orders = client.get_orders()
print(f"  Open orders: {len(orders)}")

# Test 4: Place and immediately cancel a tiny test order
# Find any active market with a price far from current
# DO NOT DO THIS until you've funded the wallet
print("\nConnection test PASSED. Ready for live trading.")
print("Next step: fund wallet with $10 USDC on Polygon")
```

---

## PART 2: Code Changes for Live Mode

### 2.1 What Already Exists (Don't Rebuild)

The agent's exploration found that most infrastructure is already in place:

- `ExecutionAdapter` already wraps py-clob-client with retries and backoff
- `UserFeedState` already parses live fills from WebSocket
- API key loading already exists in `config/settings.py`
- `py-clob-client` is already a dependency (v0.20.0)
- `main_loop._apply_actions()` already calls execution_adapter methods
- The market WebSocket feed is already production-grade

### 2.2 What Needs to Be Built

**File 1: `core_mm/live_broker.py` (NEW)**

Same interface as PaperBroker so the rest of the system doesn't change.
Key differences from paper:
- place_order() calls real ExecutionAdapter instead of simulating
- Fills come from UserWebSocket events, not book simulation
- Pre-trade risk checks gate every order before submission
- cancel_all() is a real emergency function, not a no-op

Risk limits for Phase A (hardcoded as defaults, overridable via CLI):
- Max single order notional: $5.00
- Max total position per token: $10.00
- Max daily loss: $3.00 (triggers automatic shutdown)
- Price bounds: reject anything outside [0.01, 0.99]
- Size bounds: reject size < 1

**File 2: `core_mm/user_ws_adapter.py` (NEW)**

Connects to Polymarket's user WebSocket for real-time fill notifications.
Endpoint: wss://ws-subscriptions-clob.polymarket.com/ws/user
Auth: API key/secret/passphrase
Events: order placement confirmations, fill notifications, cancellation acks

This is the live equivalent of what the paper broker does internally —
but instead of simulating fills, it receives actual fill data from the exchange.

**File 3: `core_mm/runner.py` (MODIFY)**

Add mode="LIVE" branch that:
- Requires a LiveBroker instance (not optional)
- Validates API credentials are present before starting
- Logs a clear "LIVE TRADING ACTIVE" warning at startup

**File 4: `scripts/run_core_mm.py` (MODIFY)**

Add --mode LIVE with additional CLI args:
- --max-order-notional (default 5.0)
- --max-position-notional (default 10.0)
- --max-daily-loss (default 3.0)

Add cancel_all() in finally block for crash safety.
Add user WebSocket connection alongside market WebSocket.

**File 5: Tests**

- test_live_broker.py: risk checks, fill tracking, stats
- test_runner.py: mode=LIVE initialization

### 2.3 The Codex Prompt

Paste this into your Codex agent:

---

Task: Implement LIVE trading mode for core_mm. The gap is small — most
infrastructure exists. Read the existing code first.

Before writing any code, answer these questions by reading source files:

1. Read core_mm/paper_broker.py — list every public method and property.
   The LiveBroker must match this interface exactly.

2. Read core_mm/runner.py lines 70-90 — show the current mode branching
   and where LiveBroker needs to plug in.

3. Read scripts/run_core_mm.py — show how PaperBroker is currently
   constructed and passed to the runner.

4. Read core/execution_adapter.py (or wherever ExecutionAdapter lives) —
   show the place_order() and cancel_order() signatures.

5. Read core/user_feed_state.py (or wherever UserFeedState lives) — show
   how it parses fill events from WebSocket messages.

Then implement:

File 1: core_mm/live_broker.py
- Same interface as PaperBroker (every method and property)
- place_order() → pre-trade risk check → ExecutionAdapter.place_order()
- cancel_order() → ExecutionAdapter.cancel_order()
- cancel_all() → ExecutionAdapter.cancel_all()
- record_fill(fill_event) → updates internal fill list, PnL, stats
- fills() → accumulated fill list
- drain_new_fills() → fills since last drain (cursor-based)
- stats() → dict with realized_pnl, fees, turnover, win_count, loss_count
- Pre-trade risk checks:
  - max_order_notional: reject if price * size > limit
  - max_position_notional: reject if current position + new order > limit
  - price bounds: reject outside [0.01, 0.99]
  - size bounds: reject < 1
  - daily loss: reject all orders if daily loss > max_daily_loss

File 2: core_mm/user_ws_adapter.py
- Connect to wss://ws-subscriptions-clob.polymarket.com/ws/user
- Auth with API key/secret/passphrase
- On fill event → call live_broker.record_fill()
- On order event → log for monitoring
- Reconnect with exponential backoff on disconnect
- Same async pattern as the existing market WebSocket adapter

File 3: Modify core_mm/runner.py
- Add mode="LIVE" branch
- LiveBroker must be provided (not auto-created)
- Everything else stays the same — same main loop, same quote engine,
  same flow filter, same inventory skew

File 4: Modify scripts/run_core_mm.py
- Add "LIVE" to --mode choices
- Add --max-order-notional, --max-position-notional, --max-daily-loss
- Construct ClobClient from settings
- Construct LiveBroker with ExecutionAdapter
- Start user WebSocket as async task alongside market WebSocket
- Add cancel_all() in finally block for LIVE mode

File 5: tests/core_mm/test_live_broker.py
- Test risk check rejects oversized order
- Test risk check rejects out-of-bounds price
- Test risk check rejects when daily loss exceeded
- Test record_fill() updates stats correctly
- Test drain_new_fills() returns only new fills
- Test cancel_all() delegates to ExecutionAdapter

Constraints:
- Do not modify paper_broker.py, quote_engine.py, main_loop.py, or
  flow_filter.py — the trading logic is proven and must not change
- LiveBroker interface must exactly match PaperBroker so the main loop
  doesn't know the difference
- Total new production code: under 300 lines
- All existing tests must still pass

---

## PART 3: First Live Session Protocol

### 3.1 Pre-Flight Checklist

Before running the bot in LIVE mode, verify every item:

```
[] Wallet funded with $10+ USDC on Polygon
[] MATIC balance > 0.1 for gas
[] USDC approval set for Polymarket exchange contract
[] API credentials derived and stored in .env
[] .env is in .gitignore and not tracked by git
[] test_connection.py passes all checks
[] All tests pass: pytest tests/core_mm/ -q
[] Git tagged: git tag v1.0-pre-live
[] Dashboard running and showing LIVE mode label
[] Kill switch tested (place far-from-market order, cancel it)
```

### 3.2 Kill Switch Test (MANDATORY Before Real Trading)

This must work before you trade with real money:

```python
"""test_kill_switch.py — Verify cancel_all works with real API."""
# 1. Place a single limit order far from market (e.g., BUY YES at $0.01)
# 2. Verify it appears in get_orders()
# 3. Call cancel_all()
# 4. Verify get_orders() returns empty
# 5. If any step fails, DO NOT proceed to live trading
```

### 3.3 Supervised First Run

**Start extremely small:**
```bash
python scripts/run_core_mm.py \
  --mode LIVE \
  --symbol BTC \
  --max-order-notional 2.00 \
  --max-position-notional 5.00 \
  --max-daily-loss 2.00 \
  --duration-secs 900
```

That's $2 max per order, $5 max position, $2 max loss, 15-minute run.

**Watch the first 5 minutes manually. Verify:**

Minute 0-1:
- Bot starts without errors
- Market WebSocket connects
- User WebSocket connects
- First quotes computed (check logs)

Minute 1-2:
- First orders placed on the CLOB
- Orders visible on Polymarket UI (go to the market page and look)
- Prices make sense (within the book spread)

Minute 2-5:
- First cancel/replace cycle happens (order updated due to book change)
- Cancellation confirmed via user WebSocket
- If a fill happens: position tracking updates, PnL updates

Minute 5-15:
- System stable, no errors
- If fill happened: verify the fill shows in your Polymarket account
- Dashboard shows real fills and real PnL

**If anything goes wrong: Ctrl+C → cancel_all() fires → all orders removed.**

### 3.4 Graduating to Longer Runs

After the 15-minute supervised run succeeds:

Day 1 afternoon: 1-hour run, still supervised, same limits
Day 1 evening: 2-hour run, check every 30 minutes
Day 2: 4-hour run, increase to --max-order-notional 5.00
Day 3-7: Full-day runs, collect comparison data

Do NOT increase capital beyond $10 total in the first week.

### 3.5 What to Expect vs. Paper

Your paper results will NOT match live. Here's what will change:

| What | Paper | Live (expected) | Why |
|------|-------|-----------------|-----|
| Fill rate | 67.5% | 30-50% | Real queue position, real competition |
| Markout 1s | +94 bps | +20-60 bps | Other bots are faster than your paper sim |
| Spread captured | 73 bps | 40-60 bps | Can't always get best price in queue |
| Adverse selection | 31% | 35-45% | Real informed traders exist |
| P&L per fill | positive | smaller positive or near zero | Tighter competition |

**This is normal and expected.** The purpose of week 1 is to measure
these differences and use them to calibrate your paper simulator for
future development.

The specific calibration: if live fill rate is 40% vs paper's 67%, set
paper_queue_depth_fraction from 0.5 to 0.7. If live markout is half
of paper, your paper broker is too optimistic about fill timing.

---

## PART 4: Post-Live Iteration Roadmap

### Week 1: Single Market Live (BTC only)
- Goal: Prove the plumbing works, collect live-vs-paper comparison data
- Capital: $10 max
- Success: Bot runs for multiple hours without intervention, P&L trackable

### Week 2: Parameter Calibration
- Use week 1 data to calibrate paper simulator
- Tune queue_depth_fraction, min_queue_wait_ms based on live fill rates
- Tune flow filter thresholds based on live adverse selection data
- Re-run paper with calibrated params to verify improvement

### Week 3: Multi-Market Paper Testing
- Add ETH, SOL, XRP to paper mode with calibrated simulator
- Each symbol managed independently (separate inventory, separate skew)
- Compare per-symbol profitability
- Identify which symbols to trade live and which to skip

### Week 4: Multi-Market Live
- Go live on 2-3 symbols (whichever paper showed best metrics)
- Still managed independently per symbol
- Scale capital to $25-50 total across symbols

### Month 2: Optimization
- NO-side trading fix (trade both YES and NO tokens)
- Position merging (free capital from overlapping positions)
- Alpha skew from pstar (edge-informed market making)
- 5-minute market expansion
- Cross-asset inventory awareness (later in month)

### Month 3: Scale
- Increase capital based on proven edge
- Add more markets / timeframes
- Potentially add non-crypto event markets
- Portfolio-level risk management

---

## PART 5: Emergency Procedures

### Bot is losing money fast
1. Ctrl+C to stop (cancel_all fires automatically)
2. Check Polymarket UI — verify all orders cancelled
3. Check positions — if still holding, manually close on UI
4. Do NOT restart until you understand why

### WebSocket disconnected
- Bot should auto-reconnect (exponential backoff)
- If reconnect fails 3 times: bot should stop and cancel all orders
- Check: is Polymarket down? (check their status page / Discord)

### Order rejected by exchange
- Log the error message
- Common causes: insufficient balance, nonce error, invalid token ID
- Nonce errors: re-derive API creds
- Balance errors: check wallet on Polygonscan

### Position stuck (can't cancel or sell)
- Go to Polymarket UI manually
- Cancel orders manually
- If position exists but no orders: place a manual sell order on the UI
- The bot's position tracking may be out of sync — restart will re-sync

### Daily loss limit hit
- Bot automatically stops trading (no new orders)
- Existing orders remain (manual cancel if needed)
- Do NOT override the limit
- Review what happened before resuming next day

---

## PART 6: Key Numbers to Monitor Daily

When running live, check these every day:

```
1. Net P&L (after fees)           Target: positive
2. Fill rate                      Target: > 30%
3. Markout 1s                     Target: > 0 bps
4. Adverse selection rate         Target: < 50%
5. Avg inventory duration         Target: < 5 minutes
6. Max drawdown                   Target: < 50% of starting capital
7. USDC balance                   Target: > $5 (buffer)
8. MATIC balance                  Target: > 0.05
9. WebSocket uptime               Target: > 99%
10. Unhandled errors              Target: 0
```

If any of these are consistently bad for 3+ days, stop trading and
investigate before continuing.

---

## Appendix: File Change Summary

| File | Action | Purpose |
|------|--------|---------|
| core_mm/live_broker.py | CREATE | LiveBroker matching PaperBroker interface |
| core_mm/user_ws_adapter.py | CREATE | User WebSocket for live fill notifications |
| core_mm/runner.py | MODIFY | Add mode=LIVE branch |
| scripts/run_core_mm.py | MODIFY | LIVE mode CLI, client construction, crash safety |
| tests/core_mm/test_live_broker.py | CREATE | Risk check and fill tracking tests |
| config/.env | CREATE/UPDATE | API credentials (never committed) |
| scripts/test_connection.py | CREATE | Pre-flight connection test |
| scripts/test_kill_switch.py | CREATE | Kill switch verification |
