# Market Management Automation

Scripts and procedures for adding/removing markets, tuning parameters, and managing the bot's market portfolio as it scales.

---

## Adding a New Market

### Step 1 — Discover Market Details
```bash
# Search Polymarket CLOB API for market by keyword
python3 - <<'EOF'
import requests, json, sys

keyword = sys.argv[1] if len(sys.argv) > 1 else "bitcoin"
resp = requests.get(f"https://clob.polymarket.com/markets?search={keyword}")
markets = resp.json().get("data", [])
for m in markets[:5]:
    print(f"Slug: {m.get('market_slug')}")
    print(f"  condition_id: {m.get('condition_id')}")
    print(f"  tokens: {[t['token_id'] for t in m.get('tokens', [])]}")
    print(f"  outcomes: {[t['outcome'] for t in m.get('tokens', [])]}")
    print()
EOF python3 -c "" "bitcoin"
```

Or use the market discovery via the bot's read-only mode:
```bash
python scripts/run_readonly.py --discover --keyword "bitcoin" 2>&1 | head -40
```

### Step 2 — Add to markets.yaml
```bash
cat >> config/markets.yaml << 'EOF'
- slug: "bitcoin-updown-2025-q2-1234567890"
  condition_id: "0xabc123..."
  token_ids:
    - "111222333..."   # YES / UP token
    - "444555666..."   # NO / DOWN token
EOF
```

Validate:
```bash
python -c "from config.settings import load_settings; s = load_settings(); print('Markets:', [m.slug for m in s.markets])"
```

### Step 3 — Restart Engine
```bash
systemctl restart trader.service
journalctl -fu trader.service | grep -E "market|slug|discovered" | head -20
```

### Step 4 — Verify Market Active
```bash
# Wait 2–3 minutes, then check:
DB=tmp/$(ls -t tmp/ | head -1)/runtime.db
sqlite3 $DB "SELECT market, COUNT(*) as n, MAX(datetime(ts_ms/1000,'unixepoch')) as last FROM decisions GROUP BY market ORDER BY n DESC;"
```

---

## Removing a Market

```bash
# 1. Edit markets.yaml — remove the market entry
# (use your editor, not automated to avoid mistakes)

# 2. Validate
python -c "from config.settings import load_settings; s = load_settings(); print('Markets:', len(s.markets))"

# 3. Check for open orders (cancel manually if any)
DB=tmp/$(ls -t tmp/ | head -1)/runtime.db
sqlite3 $DB "SELECT token_id, side, price, qty, status FROM orders WHERE LOWER(status) IN ('open','working','resting') ORDER BY ts_ms DESC;"

# 4. Restart engine
systemctl restart trader.service
```

---

## Market Health Scorecard
```bash
DB=tmp/$(ls -t tmp/ | head -1)/runtime.db

# Per-market decision summary (last 24h)
sqlite3 $DB "
SELECT 
  market,
  COUNT(*) as decisions,
  SUM(CASE WHEN action NOT IN ('HOLD','FREEZE','SKIP') THEN 1 ELSE 0 END) as signals,
  ROUND(AVG(p_hat),3) as avg_phat,
  ROUND(AVG(expected_edge - expected_cost),4) as avg_ev
FROM decisions
WHERE ts_ms > (strftime('%s','now') - 86400) * 1000
GROUP BY market
ORDER BY decisions DESC;
"
```

---

## Scaling: Multi-Market Strategy

As the bot scales to more markets, keep these principles in mind:

**Per-market caps** — Each market has independent caps in `config/portfolio.yaml`. Review regularly:
```bash
cat config/portfolio.yaml
```

**Correlation risk** — Correlated prediction markets (e.g., "BTC > $100k" and "BTC > $80k") can create correlated positions. Monitor net exposure:
```bash
DB=tmp/$(ls -t tmp/ | head -1)/runtime.db
sqlite3 $DB "SELECT i.token_id, i.yes_qty, i.no_qty FROM inventory i JOIN (SELECT token_id, MAX(ts_ms) AS m FROM inventory GROUP BY token_id) x ON x.token_id=i.token_id AND x.m=i.ts_ms WHERE yes_qty > 0 OR no_qty > 0;"
```

**Compute budget** — Each active market adds ~1 WebSocket subscription and 1 decision loop. Watch CPU:
```bash
top -p $(pgrep -f "core_mm") -b -n 1 | grep python
```

**Market selection criteria (recommended):**
- Daily volume > $5k USDC
- Spread < 200bps (check via dashboard Gate C)
- Resolution date > 7 days away
- Outcome is binary and objectively verifiable

---

## Constitution Parameter Tuning

The `config/constitution.yaml` controls risk. Tune carefully. Common adjustments:

### Widen Gate C (Allow More Markets)
If a market you want to trade has normal spreads but Gate C is too strict:
```yaml
# config/constitution.yaml — increase these
gate_c_spread_bps_max: 250    # default: 150
gate_c_slippage_bps_max: 300  # default: 200
```
**Risk:** Bot will trade in thinner markets with higher slippage.

### Adjust Trade Size
```yaml
# Increase/decrease per-trade size
trade_size_usdc: 50.0    # default: 25.0
```
Never increase by more than 2x at a time. Test in paper mode first.

### Exposure Caps
```yaml
# config/portfolio.yaml
max_net_exposure_usdc: 500.0    # total portfolio
per_market_cap_usdc: 150.0      # per market limit
```

### Latency Thresholds (Gate E)
If your server has higher latency (e.g., not co-located):
```yaml
gate_e_ws_lag_ms_max: 400      # default: 200
gate_e_ack_latency_ms_max: 600  # default: 300
```

---

## Whale Address Management

The `config/whales.json` tracks known large players. When a whale address is seen in book updates, it influences flow filter signals.

```bash
# Add a whale address
python3 - <<'EOF'
import json
path = "config/whales.json"
with open(path) as f:
    data = json.load(f)
data.append({
    "address": "0xNEW_WHALE_ADDRESS",
    "label": "Known MM 2",
    "side_bias": "neutral"
})
with open(path, "w") as f:
    json.dump(data, f, indent=2)
print("Added. Restart engine to take effect.")
EOF
```

```bash
# View current whales
python3 -c "import json; [print(w['label'], w['address'][:10]+'...') for w in json.load(open('config/whales.json'))]"
```

---

## Replay & Backtesting

Before deploying a constitution change, run a replay to check impact:
```bash
# Replay last run with new constitution
python scripts/replay_runner.py \
  --tape-dir tmp/$(ls -t tmp/ | head -1)/tapes/ \
  --constitution config/constitution.yaml \
  --output-dir tmp/replay_test/

# Compare decisions
diff <(sqlite3 tmp/$(ls -t tmp/ | head -1)/runtime.db "SELECT action FROM decisions ORDER BY ts_ms;") \
     <(sqlite3 tmp/replay_test/runtime.db "SELECT action FROM decisions ORDER BY ts_ms;") \
  | head -40
```

---

## Automation Ideas for Future

As the bot grows, consider these automation scripts:

- **`scripts/auto_discover.py`** — Scan Polymarket API daily, flag new markets that meet criteria, append to `markets.yaml` automatically
- **`scripts/market_health_report.py`** — Weekly markdown report of per-market performance (fill rate, avg EV, spread environment)
- **`scripts/prune_markets.py`** — Remove markets with < N decisions/week automatically
- **`scripts/constitution_optimizer.py`** — Walk-forward optimization of gate thresholds using replay backtest
- **`scripts/alert_digest.py`** — Daily email/Slack digest of alert history and PnL summary
