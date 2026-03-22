# Deployment & Operations

## What You Have

✅ **Kalshi Integration** — Fully integrated, tested, and ready to deploy
✅ **Polymarket Support** — Existing infrastructure untouched
✅ **Git Version Control** — All changes tracked locally
✅ **Unit Tests** — 329 tests passing (48 Kalshi-specific)
✅ **Telemetry** — SQLite runtime.db + JSON status for each run
✅ **Risk Management** — Daily loss caps, position limits, emergency cancellation

---

## What Information the Bot Needs

The bot requires two types of information:

### 1. Exchange Credentials (in `.env`)

| Field | Required For | How to Get |
|-------|--------------|-----------|
| `POLYMARKET_API_KEY` | Polymarket trading | Polymarket dashboard → API Keys |
| `POLYMARKET_SECRET` | Polymarket trading | Polymarket dashboard → API Keys |
| `POLYMARKET_PASSPHRASE` | Polymarket trading | Polymarket dashboard → API Keys |
| `POLYMARKET_PRIVATE_KEY` | LIVE mode signing | Polymarket dashboard (hex format) |
| `KALSHI_API_KEY_ID` | Kalshi trading | Kalshi dashboard → API Keys |
| `KALSHI_PRIVATE_KEY_PATH` | Kalshi request signing | Generate locally, upload public key to Kalshi |
| `KALSHI_BASE_URL` | Kalshi connectivity | Demo: `https://demo-api.kalshi.co` or Prod: `https://trading-api.kalshi.com` |

**Security note:** All credentials are in `.env`, which is in `.gitignore` and will never be committed.

### 2. Market & Strategy Parameters (CLI args)

**Market Discovery:**
```
--symbol BTC        # Single symbol
--symbols BTC,ETH   # Multiple symbols
--horizon 15m       # Resolution (Polymarket only)
```

**Trading Behavior:**
```
--min-size 10.0                    # Min order size
--trade-size 12.0                  # Target position size
--max-size 150.0                   # Absolute max position
--within-pct 0.06                  # Quote depth (6% from mid)
--max-spread-bps 500.0             # Give up if spread too wide
```

**Sizing & Leverage:**
```
--kelly-fraction 0.25              # Kelly sizing (0.0 = disabled)
--inventory-skew-factor 1.0        # Continuous inventory adjustment
--max-skew-ticks 1                 # Max ticks to shift quote for inventory
```

**Risk Management:**
```
--daily-loss-cap -50.0             # Halt if realized PnL <= this (OBSERVE/PAPER)
--max-daily-loss 3.0               # Halt if realized loss >= this (LIVE)
--max-order-notional 5.0           # Per-order size cap (LIVE)
--max-position-notional 10.0       # Per-token position cap (LIVE)
```

**Execution:**
```
--fee-bps 25                       # Fee rate for calculations
--post-only-enabled true           # Post-only quotes (LIVE)
--quote-interval-ms 1000           # Quote refresh frequency
```

---

## Deployment Stages

### Stage 1: OBSERVE Mode ✅ (No Real Orders)

**What it does:**
- Connects to the exchange
- Discovers markets for your symbol(s)
- Polls orderbooks and tracks them
- Runs all strategy logic (alpha, sizing, positioning)
- Writes zero orders
- Records telemetry to `runtime.db`

**How to validate:**
- Check `status.json` shows markets found
- Verify `applied_book_updates > 0`
- Ensure no errors in console logs

**Kalshi example:**
```bash
python3 scripts/run_core_mm.py \
  --exchange kalshi \
  --mode OBSERVE \
  --runtime-root tmp/runs/kalshi-observe-001 \
  --duration-secs 300 \
  --symbol BTC
```

### Stage 2: PAPER Mode ✅ (Simulated Orders)

**What it does:**
- Same as OBSERVE, but also places simulated orders
- PaperBroker fills trades against live orderbooks
- Tracks simulated PnL, fills, positions
- Writes to same `runtime.db`
- Useful for tuning parameters without risk

**How to validate:**
- Check fill rate matches expectations
- Verify markout (entry vs market-to-exit spread) is positive
- Inspect PnL distribution (should have negative tail due to adverse fills)

**Kalshi example:**
```bash
python3 scripts/run_core_mm.py \
  --exchange kalshi \
  --mode PAPER \
  --runtime-root tmp/runs/kalshi-paper-001 \
  --duration-secs 600 \
  --symbol BTC \
  --usdc-balance 1000 \
  --kelly-fraction 0.25
```

### Stage 3: LIVE Mode ⚠️ (Real Money)

**What it does:**
- Places real orders on the exchange
- Receives real fills
- Incurs real fees
- Tracks real PnL
- Enforces strict risk limits

**Risk limits (you control):**
- `--max-order-notional 5.0` — Don't place orders > $5
- `--max-position-notional 10.0` — Don't hold > $10 per token
- `--max-daily-loss 3.0` — Halt if lost $3 today

**Kalshi example (production):**
```bash
KALSHI_BASE_URL=https://trading-api.kalshi.com python3 scripts/run_core_mm.py \
  --exchange kalshi \
  --mode LIVE \
  --runtime-root tmp/runs/kalshi-live-001 \
  --duration-secs 600 \
  --symbol BTC \
  --max-order-notional 5.0 \
  --max-position-notional 10.0 \
  --max-daily-loss 3.0
```

**On startup:**
- Fetches open orders from the exchange
- Syncs position tracker to match actual positions
- If reconciliation fails, halts

**On shutdown:**
- Cancels all resting orders
- Records final PnL

**On daily loss cap:**
- Halts quoting
- Cancels all orders
- Exits cleanly

---

## Recommended Progression

```
1. OBSERVE (Demo) ──→ Verify connectivity
                  ↓
2. PAPER (Demo) ──→ Tune parameters
                  ↓
3. PAPER (Prod) ──→ Test on real markets
                  ↓
4. LIVE (Prod, $5) ──→ Real orders, small limits
                  ↓
5. LIVE (Prod, $10) ──→ Increase size 2x
                  ↓
6. LIVE (Prod, $25) ──→ Gradual expansion
```

Each stage should run **30 minutes to 1 hour** before proceeding.

---

## Running Both Exchanges Simultaneously

You can market-make on Polymarket and Kalshi at the same time:

```bash
# Terminal 1: Polymarket PAPER
python3 scripts/run_core_mm.py \
  --exchange polymarket \
  --mode PAPER \
  --runtime-root tmp/runs/pm-paper \
  --symbol BTC \
  --usdc-balance 1000

# Terminal 2: Kalshi PAPER
python3 scripts/run_core_mm.py \
  --exchange kalshi \
  --mode PAPER \
  --runtime-root tmp/runs/kalshi-paper \
  --symbol BTC \
  --usdc-balance 1000
```

**Each run:**
- Has independent market selection
- Discovers separate orderbooks
- Maintains separate positions
- Writes to separate `runtime.db` files
- Does NOT share state

This lets you compare performance across exchanges in parallel.

---

## Monitoring & Operations

### View Status (Mid-Run)
```bash
# Check current state
cat tmp/runs/kalshi-live-001/meta/status.json | jq .

# Follow updates
watch -n 1 'cat tmp/runs/kalshi-live-001/meta/status.json | jq .'
```

### View Results (After Run)
```bash
# Recent decisions
sqlite3 tmp/runs/kalshi-live-001/runtime.db \
  "SELECT * FROM decisions ORDER BY decision_ts DESC LIMIT 10;"

# Fills
sqlite3 tmp/runs/kalshi-live-001/runtime.db \
  "SELECT * FROM fills ORDER BY fill_ts DESC LIMIT 10;"

# PnL summary
sqlite3 tmp/runs/kalshi-live-001/runtime.db \
  "SELECT SUM(realized_pnl) as total_pnl FROM fills;"
```

### Dashboard
```bash
python3 scripts/run_dashboard.py
# Open http://localhost:8501 in your browser
```

---

## Troubleshooting Deployment

### Markets not discovered
- **Check symbol exists on the exchange** — e.g., "BTC" vs "BTCUSD"
- **Wait 30+ seconds** — Market discovery is async
- **Check logs** — `logs/*.log` has detailed errors

### No fills in PAPER mode
- **Quote spread too tight** — Increase `--within-pct` to 0.10 or 0.15
- **Size too small** — Increase `--trade-size` to match orderbook depth
- **Midpoint calculation wrong** — Verify `--fee-bps` matches real fees

### LIVE orders rejected
- **Insufficient balance** — Check exchange account has funds
- **Order size invalid** — May be below min size or above max notional
- **Order type not supported** — Verify `--post-only-enabled` is compatible

### RSA signature errors (Kalshi only)
- **Private key path incorrect** — Check `KALSHI_PRIVATE_KEY_PATH` in `.env`
- **Private key file not readable** — Verify `chmod +r kalshi-private-key.pem`
- **Public key not uploaded** — Upload the `.pem` to Kalshi dashboard

---

## Scaling Up

Once you're confident in the strategy:

1. **Increase position sizes gradually:**
   - Start: `--max-position-notional 10.0`
   - Week 1: `--max-position-notional 25.0`
   - Week 2: `--max-position-notional 50.0`

2. **Add symbols:**
   - Start: `--symbol BTC`
   - Week 1: `--symbols BTC,ETH`
   - Week 2: `--symbols BTC,ETH,SOL,XRP`

3. **Enable multi-market trading:**
   - `--max-active-markets 1` (quote one market at a time)
   - `--max-active-markets 3` (quote up to 3 markets in parallel)

4. **Adjust kelly fraction:**
   - Start: `--kelly-fraction 0.0` (no sizing leverage)
   - Ramp: `--kelly-fraction 0.10, 0.25, 0.5`

5. **Monitor via dashboard:**
   - Track PnL, fill rate, slippage over time
   - Adjust parameters based on performance

---

## Key Safeguards (Already Built In)

✅ **Order cancellation on shutdown** — All resting orders cancelled on exit
✅ **Daily loss cap** — Halts quoting if PnL too negative
✅ **Position limits** — Won't exceed per-token or aggregate notional
✅ **Order size limits** — Won't place orders above per-order notional (LIVE)
✅ **Fee accounting** — Sized positions account for trading fees
✅ **Post-only quotes** — Avoids maker fees (configurable)
✅ **Reference price guards** — Skips quotes if reference too stale
✅ **Startup reconciliation** — Syncs to actual positions on startup

---

## Next: Push to GitHub

When ready to back up to the cloud:

```bash
# Create private repo on GitHub (via gh CLI or web)
gh repo create polymarket-bot --private --source=. --push

# Or, if already created:
git remote add origin https://github.com/YOUR_GITHUB_USER/polymarket-bot.git
git branch -M main
git push -u origin main
```

Your code + git history is now safely backed up. Kalshi credentials stay locally in `.env` (never pushed).

---

Good luck with your deployment! 🚀
