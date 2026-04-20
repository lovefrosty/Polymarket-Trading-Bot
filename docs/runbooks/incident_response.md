# Incident Response Playbook

Quick-reference for common failure modes. Each scenario follows: **Symptoms → Diagnose → Fix → Verify**.

---

## Gate A Breach — Stale / Invalid Price Feed

### Symptoms
- Dashboard top bar shows Gate A: CRITICAL or WARN (red/amber chip)
- Alert: `A_PSTAR_INVALID` or `A_PSTAR_STALE`
- `is_frozen=1` in system_state
- Bot holding, no new orders being placed

### Diagnose
```bash
DB=tmp/$(ls -t tmp/ | head -1)/runtime.db

# Check feed freshness and validity
sqlite3 $DB "SELECT datetime(ts_ms/1000,'unixepoch') as ts, symbol, valid, age_spot_ms, age_perp_ms, disagreement_bps FROM pstar_stats ORDER BY ts_ms DESC LIMIT 10;"

# Is the engine writing at all? (check decisions recency)
sqlite3 $DB "SELECT datetime(MAX(ts_ms)/1000,'unixepoch') as last_decision FROM decisions;"
```

### Fix
1. **Feed stale but engine running** → Check external feed connectivity:
   ```bash
   # Test Polymarket API reachability
   curl -s "https://clob.polymarket.com/markets" | python3 -c "import sys,json; d=json.load(sys.stdin); print('API OK, markets:', len(d.get('data',[])))"
   ```
2. **Feed invalid (disagreement)** → This is a legitimate market signal. Do not override. Wait for convergence or increase `gate_a_disagreement_bps_warn` threshold in `config/constitution.yaml` (requires risk review).
3. **Engine stuck / not writing** → Restart the engine:
   ```bash
   systemctl restart trader.service
   journalctl -fu trader.service | head -30
   ```

### Verify
```bash
DB=tmp/$(ls -t tmp/ | head -1)/runtime.db
sqlite3 $DB "SELECT valid, age_spot_ms, age_perp_ms FROM pstar_stats ORDER BY ts_ms DESC LIMIT 3;"
# Expected: valid=1, ages < 5000ms
```

---

## Gate C Breach — Spread Too Wide / Slippage High

### Symptoms
- Gate C: WARN or CRITICAL
- Alert: `C_SPREAD_TOO_WIDE`, `C_SLIPPAGE_HIGH`, or `C_BOOK_STALE`
- Orders being cancelled, no new quotes placed

### Diagnose
```bash
DB=tmp/$(ls -t tmp/ | head -1)/runtime.db

sqlite3 $DB "SELECT datetime(ts_ms/1000,'unixepoch') as ts, token_id, spread_bps, depth_at_qty_buy, depth_at_qty_sell, book_health FROM microstructure_stats ORDER BY ts_ms DESC LIMIT 10;"
```

### Fix
- **Wide spread** (market is thin): This is normal in illiquid markets. Bot correctly steps out. No action needed unless this is persistent (>1 hour).
  - If persistent, consider removing the market from `config/markets.yaml`.
- **Book stale**: Check WebSocket connection:
  ```bash
  journalctl -u trader.service | grep -E "ws|websocket|reconnect" | tail -20
  ```
  Restart if WS reconnects aren't working:
  ```bash
  systemctl restart trader.service
  ```

### Verify
```bash
DB=tmp/$(ls -t tmp/ | head -1)/runtime.db
sqlite3 $DB "SELECT spread_bps, book_health FROM microstructure_stats ORDER BY ts_ms DESC LIMIT 3;"
```

---

## Gate E Breach — Latency High

### Symptoms
- Gate E: WARN or CRITICAL
- Alert: `E_WS_LAG_HIGH`, `E_ACK_LATENCY_HIGH`, or `E_SIGNAL_AGE_HIGH`
- Orders placed but fills delayed

### Diagnose
```bash
DB=tmp/$(ls -t tmp/ | head -1)/runtime.db

sqlite3 $DB "SELECT datetime(ts_ms/1000,'unixepoch') as ts, p50_send_ack_ms, p95_send_ack_ms, ws_lag_ms, p95_ws_lag_ms FROM latency_stats ORDER BY ts_ms DESC LIMIT 10;"
```

### Fix
1. **WS lag high**: Network issue. Check connection from the host:
   ```bash
   ping -c 5 clob.polymarket.com
   traceroute clob.polymarket.com
   ```
2. **Ack latency high**: Polymarket API under load. Wait 5–10 min. If persists > 30 min, contact Polymarket support.
3. **Signal age high**: Engine processing loop is slow. Check CPU/memory:
   ```bash
   top -bn1 | head -20
   free -h
   ```
   If memory is tight: `systemctl restart trader.service`

### Verify
```bash
DB=tmp/$(ls -t tmp/ | head -1)/runtime.db
sqlite3 $DB "SELECT p95_send_ack_ms, ws_lag_ms FROM latency_stats ORDER BY ts_ms DESC LIMIT 3;"
# Target: p95_send_ack_ms < 300, ws_lag_ms < 200
```

---

## Bot Crash / Silent Failure

### Symptoms
- No decisions written in >5 minutes
- Dashboard shows stale data (all timestamps old)
- `systemctl status trader.service` shows `failed` or `inactive`

### Diagnose
```bash
# Get last error
journalctl -u trader.service -n 50 --no-pager | tail -30

# When did writing stop?
DB=tmp/$(ls -t tmp/ | head -1)/runtime.db
sqlite3 $DB "SELECT datetime(MAX(ts_ms)/1000,'unixepoch') as last_write FROM decisions;"
```

### Fix
```bash
# Restart the engine
systemctl restart trader.service

# Watch startup
journalctl -fu trader.service
```

If crash recurs within 5 minutes, it's a code bug or config error — check logs carefully before restarting again.

### Verify
```bash
DB=tmp/$(ls -t tmp/ | head -1)/runtime.db
# Should show recent timestamp
sqlite3 $DB "SELECT datetime(MAX(ts_ms)/1000,'unixepoch') as last_decision FROM decisions;"
```

---

## Runaway Position / Emergency Halt

### Symptoms
- Net USD exposure >> portfolio cap
- Hedge completeness < 0.5 for > 15 min
- Gate D: CRITICAL (`D_HEDGE_INCOMPLETE`)

### Diagnose
```bash
DB=tmp/$(ls -t tmp/ | head -1)/runtime.db

# Check current positions
sqlite3 $DB "
SELECT i.token_id, i.yes_qty, i.no_qty, i.usdc 
FROM inventory i
JOIN (SELECT token_id, MAX(ts_ms) AS m FROM inventory GROUP BY token_id) x 
  ON x.token_id=i.token_id AND x.m=i.ts_ms;
"

# Check open orders
sqlite3 $DB "SELECT token_id, side, price, qty, status FROM orders WHERE LOWER(status) IN ('open','working','resting') ORDER BY ts_ms DESC;"
```

### Fix — Emergency Stop
```bash
# Stop the engine immediately
systemctl stop trader.service

# Dashboard remains up for inspection
systemctl status trader.service  # should show 'inactive (dead)'
```

**Do NOT restart** until you understand the root cause. Manually review the position and hedge state. If a one-legged position is open, you may need to manually cancel orders via the Polymarket UI or API.

### Verify
After resolution:
```bash
systemctl start trader.service
journalctl -fu trader.service | grep -E "started|error|gate|freeze"
```

---

## SQLite DB Lock

### Symptoms
- Dashboard fails to load, shows database error
- Engine logs show `database is locked` errors

### Diagnose
```bash
# Is anything else writing to the DB?
DB=tmp/$(ls -t tmp/ | head -1)/runtime.db
lsof "$DB" 2>/dev/null

# Check for WAL journal files
ls -la ${DB}-wal ${DB}-shm 2>/dev/null
```

### Fix
1. **WAL files present but engine stopped**: Safe to checkpoint:
   ```bash
   sqlite3 $DB "PRAGMA wal_checkpoint(TRUNCATE);"
   ```
2. **Another process holding lock**: Find and kill it, or restart services:
   ```bash
   systemctl restart trader.service dashboard.service
   ```
3. **Corrupted WAL**: Last resort — delete WAL files (risk of losing last few seconds of data):
   ```bash
   systemctl stop trader.service dashboard.service
   rm -f ${DB}-wal ${DB}-shm
   sqlite3 $DB "PRAGMA integrity_check;"  # verify DB is OK
   systemctl start trader.service dashboard.service
   ```

### Verify
```bash
sqlite3 tmp/$(ls -t tmp/ | head -1)/runtime.db "SELECT COUNT(*) FROM decisions;"
# Should return a number without error
```

---

## Dashboard Unreachable

### Symptoms
- Browser shows "Connection refused" or timeout on port 8501

### Fix
```bash
systemctl restart dashboard.service
journalctl -fu dashboard.service | head -20
```

If it fails to start:
```bash
# Check what's on port 8501
ss -tlnp | grep 8501

# Kill stale process if needed
fuser -k 8501/tcp

# Restart
systemctl start dashboard.service
```

### Verify
```bash
curl -s -o /dev/null -w "%{http_code}" http://localhost:8501/healthz
# Expected: 200
```

---

## Severity Matrix

| Severity | Example | Response Time | Action |
|----------|---------|---------------|--------|
| P1 — Critical | Engine crashed, runaway position | Immediate | Emergency halt, investigate |
| P2 — High | Gate A/E breach > 15 min | < 15 min | Diagnose & fix per playbook |
| P3 — Medium | Gate C breach (thin market) | < 1 hour | Monitor; remove market if persistent |
| P4 — Low | Dashboard unreachable | < 4 hours | Restart dashboard service |
