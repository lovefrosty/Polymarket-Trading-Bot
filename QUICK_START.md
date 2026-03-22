# Quick Start: Running the Bot

## Prerequisites
1. Python 3.10+
2. Dependencies: `pip install -r requirements.txt`
3. `.env` file with credentials (copy from `.env.template`)

## Credentials Checklist

### For Polymarket
- [ ] `POLYMARKET_API_KEY`
- [ ] `POLYMARKET_SECRET`
- [ ] `POLYMARKET_PASSPHRASE`
- [ ] `POLYMARKET_PRIVATE_KEY`

### For Kalshi
- [ ] RSA private key generated: `openssl genrsa -out kalshi-private-key.pem 4096`
- [ ] Public key uploaded to Kalshi dashboard
- [ ] `KALSHI_API_KEY_ID` (from dashboard)
- [ ] `KALSHI_PRIVATE_KEY_PATH=./kalshi-private-key.pem`
- [ ] `KALSHI_BASE_URL=https://demo-api.kalshi.co` (or production URL)

---

## One-Liners

### Polymarket — Observe
```bash
python3 scripts/run_core_mm.py --mode OBSERVE --runtime-root tmp/runs/pm-obs
```

### Polymarket — Paper Trade
```bash
python3 scripts/run_core_mm.py --mode PAPER --runtime-root tmp/runs/pm-paper --usdc-balance 1000
```

### Kalshi — Observe (Demo)
```bash
python3 scripts/run_core_mm.py --exchange kalshi --mode OBSERVE --runtime-root tmp/runs/kalshi-obs
```

### Kalshi — Paper Trade (Demo)
```bash
python3 scripts/run_core_mm.py --exchange kalshi --mode PAPER --runtime-root tmp/runs/kalshi-paper --usdc-balance 1000
```

### Kalshi — Paper Trade (Production)
```bash
KALSHI_BASE_URL=https://trading-api.kalshi.com python3 scripts/run_core_mm.py --exchange kalshi --mode PAPER --runtime-root tmp/runs/kalshi-paper-prod
```

### Kalshi — Live (Production, Small Limits)
```bash
KALSHI_BASE_URL=https://trading-api.kalshi.com python3 scripts/run_core_mm.py --exchange kalshi --mode LIVE --runtime-root tmp/runs/kalshi-live-001 --max-order-notional 5.0 --max-position-notional 10.0 --max-daily-loss 3.0
```

### Both Exchanges (Side-by-Side)
```bash
# Terminal 1
python3 scripts/run_core_mm.py --exchange polymarket --mode PAPER --runtime-root tmp/runs/pm-paper

# Terminal 2
python3 scripts/run_core_mm.py --exchange kalshi --mode PAPER --runtime-root tmp/runs/kalshi-paper
```

---

## Common Options

| Flag | Default | Example | Notes |
|------|---------|---------|-------|
| `--exchange` | `polymarket` | `kalshi` | Which exchange to use |
| `--mode` | `OBSERVE` | `PAPER`, `LIVE` | Trading mode |
| `--runtime-root` | *required* | `tmp/runs/test-001` | Output directory for logs/db |
| `--duration-secs` | 120 | 600 | Run duration in seconds |
| `--symbol` | `BTC` | `ETH`, `SOL` | Symbol to trade |
| `--symbols` | — | `BTC,ETH,SOL` | Multiple symbols |
| `--usdc-balance` | 1000 | 5000 | Simulated balance (PAPER/LIVE) |
| `--max-order-notional` | 5.0 | 10.0 | Max per-order size (LIVE only) |
| `--max-position-notional` | 10.0 | 25.0 | Max position size (LIVE only) |
| `--max-daily-loss` | 3.0 | 10.0 | Halt if loss exceeds this (LIVE only) |
| `--kelly-fraction` | 0.0 | 0.25 | Kelly-fraction sizing (0 = disabled) |

---

## After Running

View results:
```bash
# Status file
cat tmp/runs/test-001/meta/status.json

# Telemetry database
sqlite3 tmp/runs/test-001/runtime.db "SELECT * FROM decisions LIMIT 5;"

# Dashboard
python3 scripts/run_dashboard.py  # then open http://localhost:8501
```

---

## Git Workflow

```bash
# Check what changed
git status

# Commit changes
git add -A
git commit -m "Update strategy params"

# Push to GitHub
git push origin main

# Create a feature branch
git checkout -b feature/my-idea
# ... make changes ...
git commit -m "Add feature X"
git push origin feature/my-idea
# Then create PR on GitHub

# Merge back to main
git checkout main
git merge feature/my-idea
git push origin main
```

---

## Troubleshooting

### "Kalshi credentials missing"
→ Check `.env` has `KALSHI_API_KEY_ID` and `KALSHI_PRIVATE_KEY_PATH`

### "No markets found"
→ Check symbol matches available markets on the exchange
→ In PAPER/LIVE mode, allow 30+ seconds for market discovery

### "RSA signature error"
→ Verify private key path is correct and file is readable
→ Verify public key was uploaded to Kalshi dashboard

### Tests failing
```bash
python3 -m pytest tests/core_mm/ -v
```

### Dashboard shows no data
→ Check `runtime.db` exists in `--runtime-root`
→ Run with `--duration-secs 300+` to generate enough data

---

## Files to Know

- **`.env`** — Your credentials (create from `.env.template`, don't commit)
- **`SETUP.md`** — Full setup guide
- **`QUICK_START.md`** — This file
- **`core_mm/kalshi/`** — Kalshi adapter code
- **`scripts/run_core_mm.py`** — Entry point
- **`config/settings.py`** — Env var loading
- **`tests/core_mm/kalshi/`** — Kalshi unit tests

---

For detailed setup, see **SETUP.md**.
