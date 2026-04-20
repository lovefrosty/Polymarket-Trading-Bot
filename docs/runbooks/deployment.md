# Deployment Runbook

Covers how to deploy, update, configure, and roll back the Polymarket Trading Bot safely.

---

## Prerequisites

```bash
# Confirm Python environment
python --version  # 3.10+
pip show streamlit altair pandas  # should resolve without error

# Confirm systemd service files exist
ls ops/systemd/trader.service ops/systemd/dashboard.service

# Confirm config files are valid
python -c "from config.settings import load_settings; s = load_settings(); print('Config OK,', len(s.markets), 'markets')"
```

---

## Environment Variable Reference

| Variable | Required | Default | Purpose |
|----------|----------|---------|---------|
| `RUNTIME_DB_PATH` | No | `runtime.db` | SQLite DB path for dashboard |
| `POLYMARKET_API_KEY` | Yes (live) | — | Polymarket CLOB API key |
| `POLYMARKET_SECRET` | Yes (live) | — | Polymarket CLOB API secret |
| `POLYMARKET_PASSPHRASE` | Yes (live) | — | Polymarket CLOB passphrase |
| `MODE` | No | `OBSERVE` | `OBSERVE` / `PAPER` / `LIVE` |
| `LOG_LEVEL` | No | `INFO` | Python logging level |
| `RUN_DIR` | No | `tmp/<timestamp>` | Where tapes and DB are written |

**Never commit secrets.** Use a `.env` file (gitignored) or systemd `EnvironmentFile`.

---

## Initial Deployment

### 1. Install Dependencies
```bash
pip install -r requirements.txt
```

### 2. Set Up Config
```bash
# Copy and edit market config
cp config/markets.yaml.example config/markets.yaml
# Edit markets.yaml with your target markets

# Review constitution (risk params)
cat config/constitution.yaml
# Edit gate thresholds only with care — they control risk limits
```

### 3. Deploy systemd Services
```bash
# Copy service files
sudo cp ops/systemd/trader.service /etc/systemd/system/
sudo cp ops/systemd/dashboard.service /etc/systemd/system/

# Set environment variables in service or EnvironmentFile
sudo systemctl edit trader.service
# Add [Service] section with Environment= lines

# Enable and start
sudo systemctl daemon-reload
sudo systemctl enable trader.service dashboard.service
sudo systemctl start dashboard.service  # Start dashboard first, safe read-only

# Verify dashboard is up before starting trader
curl -s http://localhost:8501/healthz
sudo systemctl start trader.service
```

### 4. Start in Paper Mode First
```bash
# Always start in paper mode before live
export MODE=PAPER
systemctl start trader.service

# Watch for 30 min, confirm:
# - Decisions being made (check dashboard CORE MM tab)
# - Gate A–E all green
# - No crashes

journalctl -fu trader.service
```

### 5. Promote to Live
Only after paper mode runs cleanly for >1 session:
```bash
systemctl stop trader.service

# Update EnvironmentFile to set MODE=LIVE
# Or: sudo systemctl edit trader.service → add Environment=MODE=LIVE

systemctl start trader.service
journalctl -fu trader.service | head -40  # confirm LIVE mode logged
```

---

## Updating the Bot (Code Change)

```bash
# 1. Pull new code (never pull to main directly — PR first)
git fetch origin
git checkout main
git pull origin main

# 2. Run tests
pytest tests/ -q
# If tests fail: do NOT deploy, fix first

# 3. Stop engine (not dashboard — keep monitoring during deploy)
systemctl stop trader.service

# 4. Verify config still valid after code change
python -c "from config.settings import load_settings; load_settings(); print('OK')"

# 5. Restart engine
systemctl start trader.service
journalctl -fu trader.service | head -30

# 6. Restart dashboard to pick up new code
systemctl restart dashboard.service
```

**Rollback if needed:**
```bash
git checkout <previous-good-commit>
systemctl restart trader.service dashboard.service
```

---

## Config Change Procedures

### Adding a Market
1. Edit `config/markets.yaml` — add entry with `slug`, `condition_id`, `token_ids`
2. Validate:
   ```bash
   python -c "from config.settings import load_settings; s = load_settings(); print([m.slug for m in s.markets])"
   ```
3. Restart engine: `systemctl restart trader.service`
4. Watch dashboard for the new market appearing in CORE MM tab within 2–3 minutes

### Removing a Market
1. Remove the entry from `config/markets.yaml`
2. Restart engine: `systemctl restart trader.service`
3. Confirm no open orders remain for the removed market:
   ```bash
   sqlite3 tmp/$(ls -t tmp/ | head -1)/runtime.db "SELECT * FROM orders WHERE LOWER(status) IN ('open','working') ORDER BY ts_ms DESC LIMIT 5;"
   ```

### Changing Risk Params (`constitution.yaml`)
These directly affect risk/PnL. Always:
1. Make change in a branch, not directly on main
2. Paper-mode test for at least 1 hour
3. Document the change and expected impact in a git commit message
4. Never increase `trade_size_usdc` by more than 2x in a single deploy

---

## Rollback Procedure

```bash
# 1. Stop engine immediately
systemctl stop trader.service

# 2. Get last known good commit
git log --oneline -10

# 3. Checkout that commit
git checkout <good-commit-hash>

# 4. Restart engine
systemctl start trader.service
journalctl -fu trader.service | head -20

# 5. Verify
sqlite3 tmp/$(ls -t tmp/ | head -1)/runtime.db "SELECT datetime(MAX(ts_ms)/1000,'unixepoch') FROM decisions;"
```

---

## Run Modes

| Mode | Orders Placed | Real Money | Use For |
|------|--------------|------------|---------|
| `OBSERVE` | No | No | Data collection, testing |
| `PAPER` | Simulated | No | Strategy validation |
| `LIVE` | Real | Yes | Production trading |

**Promotion path:** OBSERVE → PAPER → LIVE (never skip steps)

---

## Health Check After Deploy
```bash
# Run 5-minute health check
DB=tmp/$(ls -t tmp/ | head -1)/runtime.db

# Check: engine writing decisions
sqlite3 $DB "SELECT datetime(MAX(ts_ms)/1000,'unixepoch') AS last_decision FROM decisions;"

# Check: no freeze
sqlite3 $DB "SELECT is_frozen, reasons FROM system_state ORDER BY as_of_ts DESC LIMIT 1;"

# Check: gates
sqlite3 $DB "SELECT datetime(ts_ms/1000,'unixepoch'), valid FROM pstar_stats ORDER BY ts_ms DESC LIMIT 3;"

# Dashboard reachable
curl -s -o /dev/null -w "%{http_code}" http://localhost:8501/healthz
```
