# Setup Guide: Polymarket + Kalshi Market-Making Bot

## 1. Git & Cloud Backup

**Your repo is now on your machine with version control:**

```bash
cd ~/Desktop/Polymarket\ Bot

# View commit history
git log --oneline

# Check status
git status

# Create a feature branch for new work
git checkout -b my-feature-name

# After making changes
git add -A
git commit -m "your message"

# Push to GitHub (requires GitHub repo + SSH key)
git push origin my-feature-name
```

**To push to GitHub (first time):**
```bash
# Install GitHub CLI if needed
brew install gh

# Log in
gh auth login

# Create a private repo
gh repo create polymarket-bot --private --source=. --push
```

Your `.gitignore` already protects:
- `.env` (credentials)
- `*.pem` (private keys)
- `*.db` and `runtime.db` (runtime data)
- `logs/` and `tmp/` (local artifacts)

---

## 2. Polymarket Credentials (Existing)

Ensure you have your Polymarket API credentials in `.env`:

```bash
cp .env.template .env
# Edit .env with your Polymarket credentials
```

Test connectivity:
```bash
POLYMARKET_API_KEY=... python3 scripts/run_core_mm.py \
  --exchange polymarket \
  --mode OBSERVE \
  --runtime-root tmp/runs/polymarket-test \
  --duration-secs 60
```

---

## 3. Kalshi Credentials (New)

### Step 1: Create Kalshi Account
- Visit [kalshi.com](https://kalshi.com)
- Sign up and complete identity verification

### Step 2: Create API Key and Save Private Key Locally
```bash
mkdir -p secrets
# Move the downloaded Kalshi private key file into your local-only folder
# Example:
mv ~/Downloads/kalshi-api.key ./secrets/kalshi-api.key
```

### Step 3: Upload to Kalshi & Get API Key
1. Log into Kalshi dashboard
2. Settings → API Keys → Create New Key
3. Download the private key file when prompted
4. Copy the **API Key ID** (looks like `a952bcbe-ec3b-4b5b-b8f9-11dae589608c`)
5. Save both securely

### Step 4: Update `.env`
```bash
KALSHI_API_KEY_ID=your_kalshi_api_key_id
KALSHI_PRIVATE_KEY_PATH=./secrets/kalshi-api.key
KALSHI_BASE_URL=https://api.elections.kalshi.com
```

**Important:** Keep the downloaded private key under `./secrets/`. It is ignored by Git and should never be committed.

---

## 4. Safe Kalshi Progression

### Stage 1: OBSERVE (Demo)
Run the bot in read-only mode on Kalshi's demo environment:

```bash
python3 scripts/run_core_mm.py \
  --exchange kalshi \
  --mode OBSERVE \
  --runtime-root tmp/runs/kalshi-observe-001 \
  --duration-secs 300 \
  --symbol BTC
```

**Checks:**
- Does `tmp/runs/kalshi-observe-001/meta/status.json` show markets discovered?
- Do `applied_book_updates` > 0?
- Are there any errors in the console?

### Stage 2: PAPER (Demo)
Simulate trades without spending money:

```bash
python3 scripts/run_core_mm.py \
  --exchange kalshi \
  --mode PAPER \
  --runtime-root tmp/runs/kalshi-paper-001 \
  --duration-secs 600 \
  --symbol BTC \
  --usdc-balance 1000
```

**Checks:**
- Fill rate and markout distribution reasonable?
- Spread/quote behavior similar to Polymarket?
- PnL stats make sense?

### Stage 3: PAPER (Production)
Switch to production markets but still simulate:

```bash
# Update .env
KALSHI_BASE_URL=https://trading-api.kalshi.com

python3 scripts/run_core_mm.py \
  --exchange kalshi \
  --mode PAPER \
  --runtime-root tmp/runs/kalshi-paper-prod-001 \
  --duration-secs 600 \
  --symbol BTC
```

### Stage 4: LIVE (Production - Small Limits)
Place real orders with conservative risk limits:

```bash
python3 scripts/run_core_mm.py \
  --exchange kalshi \
  --mode LIVE \
  --runtime-root tmp/runs/kalshi-live-001 \
  --duration-secs 300 \
  --symbol BTC \
  --max-order-notional 5.0 \
  --max-position-notional 10.0 \
  --max-daily-loss 3.0
```

**Risk limits you can adjust:**
- `--max-order-notional` — max notional per order ($5 default)
- `--max-position-notional` — max position size per token ($10 default)
- `--max-daily-loss` — halt if realized loss exceeds this ($3 default)

**LIVE mode will:**
1. Place real orders on Kalshi production
2. Track fills and PnL in `runtime.db`
3. Cancel all orders on shutdown or loss cap hit
4. Log everything to `logs/`

---

## 5. Keeping Both Exchanges Running

You can run Polymarket and Kalshi simultaneously in separate terminals:

**Terminal 1 — Polymarket (Paper):**
```bash
python3 scripts/run_core_mm.py \
  --exchange polymarket \
  --mode PAPER \
  --runtime-root tmp/runs/polymarket-paper \
  --symbol BTC
```

**Terminal 2 — Kalshi (Paper):**
```bash
python3 scripts/run_core_mm.py \
  --exchange kalshi \
  --mode PAPER \
  --runtime-root tmp/runs/kalshi-paper \
  --symbol BTC
```

Each run has:
- Separate market selection (auto-discovers based on symbol)
- Separate book manager, position tracker, telemetry
- Independent `.db` files (no collision)
- Separate status JSON (in `meta/status.json`)

---

## 6. Git Workflow for Ongoing Development

### Creating a feature branch:
```bash
git checkout -b feature/my-feature
# ... make changes ...
git add -A
git commit -m "Add feature X"
git push origin feature/my-feature
```

### Merging back to main:
```bash
git checkout main
git pull origin main  # stay in sync
git merge feature/my-feature
git push origin main
```

### Seeing what changed:
```bash
git diff main..feature/my-feature
git log --oneline main..feature/my-feature
```

---

## 7. File Structure

```
.
├── core_mm/
│   ├── kalshi/               # NEW: Kalshi adapter
│   │   ├── client.py         # REST client with RSA signing
│   │   ├── market_feed.py    # Orderbook polling
│   │   ├── market_selector.py # Market discovery
│   │   ├── execution_bridge.py # Order/fill normalization
│   │   └── fill_poller.py    # Async fill ingestion
│   ├── market_selector.py    # Polymarket market discovery
│   ├── market_ws_adapter.py  # Polymarket WebSocket feed
│   ├── execution.py          # Generic order execution adapter
│   ├── runner.py             # Core trading loop
│   └── ... (other components)
├── scripts/
│   ├── run_core_mm.py        # Entry point (--exchange flag)
│   └── run_dashboard.py      # Telemetry dashboard
├── config/
│   └── settings.py           # Kalshi env vars added
├── tests/
│   └── core_mm/kalshi/       # Kalshi unit tests (48 tests)
├── tmp/
│   └── core_mm_runs/         # Runtime artifacts (not committed)
├── .env.template             # Credential template
└── .gitignore                # Protects .env, *.pem, *.db
```

---

## 8. Dashboard Access

After a run completes, view results:

```bash
# Start the dashboard
python3 scripts/run_dashboard.py

# Open in browser: http://localhost:8501
```

The dashboard shows:
- Live trading status
- Book snapshots
- Orders and fills
- PnL and position history
- Signal contributions

---

## Next Steps

1. **Set up Kalshi credentials** (steps 3-4 above)
2. **Test OBSERVE on demo** (Stage 1) to validate connectivity
3. **Run PAPER on demo** (Stage 2) to test strategy behavior
4. **Switch to production** (Stage 3) when ready
5. **Go LIVE with small limits** (Stage 4) for final validation
6. **Expand limits gradually** as you gain confidence
7. **Track improvements** by committing runs to git

Good luck!
