# Daily Operations Runbook

Intended for the operator managing the Polymarket Trading Bot day-to-day. Run through this checklist each morning, monitor throughout the day, and archive each evening.

---

## Morning Checklist (5–10 min)

### 1. Service Health
```bash
# Are the services up?
systemctl status trader.service dashboard.service

# Any crash restarts overnight?
journalctl -u trader.service --since "yesterday" | grep -E "Started|Stopped|Failed|Error" | tail -20
```

**Expected:** Both `active (running)`. If either shows `failed`, go to [Incident Response](./incident_response.md).

### 2. Gate Status
```bash
DB=tmp/$(ls -t tmp/ | head -1)/runtime.db

# Is the bot frozen?
sqlite3 $DB "SELECT datetime(as_of_ts/1000,'unixepoch') as ts, is_frozen, reasons, mode FROM system_state ORDER BY as_of_ts DESC LIMIT 3;"

# Recent alerts
sqlite3 $DB "SELECT datetime(ts_ms/1000,'unixepoch') as ts, code, message FROM alerts ORDER BY ts_ms DESC LIMIT 15;"
```

**Expected:** `is_frozen=0`, no alerts in last hour. Gates A–E green on dashboard.

### 3. Overnight Fills & PnL
```bash
DB=tmp/$(ls -t tmp/ | head -1)/runtime.db

# Fills since midnight UTC
sqlite3 $DB "SELECT datetime(ts_ms/1000,'unixepoch') as ts, token_id, fill_price, fill_qty FROM fills WHERE ts_ms > strftime('%s','now','start of day')*1000 ORDER BY ts_ms;"

# Current inventory
sqlite3 $DB "SELECT token_id, yes_qty, no_qty, usdc FROM inventory i JOIN (SELECT token_id, MAX(ts_ms) AS m FROM inventory GROUP BY token_id) x ON x.token_id=i.token_id AND x.m=i.ts_ms;"
```

### 4. Market Window Status
Check dashboard → CORE MM tab → "Window End ETA". If a market is closing within 2 hours, review the rollover plan (ROLLOVER tab in dev mode).

---

## Throughout the Day

### Monitoring Cadence

| Interval | Check |
|----------|-------|
| Every 15 min | Dashboard top bar — gate chips, freeze state |
| Every 1 hour | Fill count, decisions count on dashboard |
| Every 2 hours | Net USD exposure vs portfolio cap |
| As needed | Alert history, latency metrics |

### Quick Health Check Command
```bash
DB=tmp/$(ls -t tmp/ | head -1)/runtime.db

sqlite3 $DB "
SELECT 
  (SELECT COUNT(*) FROM alerts WHERE ts_ms > (strftime('%s','now')-3600)*1000) AS alerts_1h,
  (SELECT COUNT(*) FROM fills WHERE ts_ms > (strftime('%s','now')-3600)*1000) AS fills_1h,
  (SELECT COUNT(*) FROM decisions WHERE ts_ms > (strftime('%s','now')-3600)*1000) AS decisions_1h,
  (SELECT is_frozen FROM system_state ORDER BY as_of_ts DESC LIMIT 1) AS frozen;
"
```

### Checking Latency
```bash
DB=tmp/$(ls -t tmp/ | head -1)/runtime.db

sqlite3 $DB "SELECT datetime(ts_ms/1000,'unixepoch') as ts, p50_send_ack_ms, p95_send_ack_ms, ws_lag_ms FROM latency_stats ORDER BY ts_ms DESC LIMIT 5;"
```

**Thresholds:** `p95_send_ack_ms < 300ms`, `ws_lag_ms < 200ms`. Over threshold → check Gate E.

### Checking Price Feed (Gate A)
```bash
DB=tmp/$(ls -t tmp/ | head -1)/runtime.db

sqlite3 $DB "SELECT datetime(ts_ms/1000,'unixepoch') as ts, symbol, valid, disagreement_bps, age_spot_ms, age_perp_ms FROM pstar_stats ORDER BY ts_ms DESC LIMIT 5;"
```

**Expected:** `valid=1`, `age_spot_ms < 5000`, `disagreement_bps < 50`.

---

## Evening Archive (2–3 min)

### Export Fills & Summary
```bash
DB=tmp/$(ls -t tmp/ | head -1)/runtime.db
DATE=$(date +%Y-%m-%d)

# Export fills to CSV
sqlite3 -csv $DB "SELECT * FROM fills ORDER BY ts_ms;" > exports/fills_${DATE}.csv

# Run audit analysis
python scripts/analyze_audit.py --run-dir tmp/$(ls -t tmp/ | head -1)/

# Archive summary
python scripts/build_datasets.py --run-dir tmp/$(ls -t tmp/ | head -1)/
```

### Position Snapshot
```bash
DB=tmp/$(ls -t tmp/ | head -1)/runtime.db
DATE=$(date +%Y-%m-%d)

sqlite3 -json $DB "
SELECT i.token_id, i.yes_qty, i.no_qty, i.usdc, datetime(i.ts_ms/1000,'unixepoch') as ts
FROM inventory i
JOIN (SELECT token_id, MAX(ts_ms) AS m FROM inventory GROUP BY token_id) x 
  ON x.token_id=i.token_id AND x.m=i.ts_ms
ORDER BY i.token_id;
" > exports/positions_${DATE}.json
echo "Positions snapshot saved to exports/positions_${DATE}.json"
```

---

## Useful One-Liners

```bash
# Show last 10 bot decisions (brief)
DB=tmp/$(ls -t tmp/ | head -1)/runtime.db
sqlite3 $DB "SELECT datetime(ts_ms/1000,'unixepoch'), market, action, reason_codes, p_hat FROM decisions ORDER BY ts_ms DESC LIMIT 10;"

# Count decisions by action type today
sqlite3 $DB "SELECT action, COUNT(*) as n FROM decisions WHERE ts_ms > strftime('%s','now','start of day')*1000 GROUP BY action ORDER BY n DESC;"

# Restart dashboard (if needed)
systemctl restart dashboard.service && journalctl -fu dashboard.service

# Tail bot logs live
journalctl -fu trader.service

# View open orders
sqlite3 $DB "SELECT token_id, side, price, qty, status FROM orders WHERE LOWER(status) IN ('open','working','resting') ORDER BY ts_ms DESC LIMIT 20;"
```

---

## Escalation

| Symptom | Action |
|---------|--------|
| Bot frozen > 5 min, Gate A breach | Check price feed connectivity; see [incident_response.md](./incident_response.md) |
| Net exposure > portfolio cap | Review positions, check Gate D (hedge completeness) |
| Fill rate drops to zero for > 30 min | Check Gate C (spread width), order book health |
| Dashboard unreachable | `systemctl restart dashboard.service` |
| DB file locked | See [incident_response.md — DB lock](./incident_response.md#sqlite-db-lock) |
