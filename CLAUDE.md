# Polymarket Trading Bot — Claude Code Project Memory

## Project Overview
Automated market-making bot for Polymarket prediction markets. Python/asyncio core with Streamlit dashboard, SQLite telemetry, and a gate-based risk framework (Gates A–E). Growing in complexity — use this file to orient fast.

## Critical Safety Rules
- **Never push to `main` without a PR.** All changes go on `claude/<description>` branches.
- **Never touch `config/constitution.yaml` gate thresholds** without explicit user approval.
- **Never write to `runtime.db`** — dashboard reads only (`PRAGMA query_only = 1`).
- **Paper mode** (`MODE=PAPER`) before any live deployment change. Live = real money.
- The engine self-freezes on Gate A/B/C/D/E breaches. Do not bypass gate checks.

## Key File Map

| Path | Purpose |
|------|---------|
| `core_mm/runner.py` | Main trading engine — OBSERVE / PAPER / LIVE modes |
| `core_mm/main_loop.py` | Per-market-cycle decision logic |
| `core_mm/telemetry.py` | Writes all SQLite tables and JSONL tapes |
| `dashboard/app.py` | Streamlit dashboard (2300+ lines) |
| `dashboard/data_access.py` | All SQLite read queries |
| `dashboard/panels/` | Modular dashboard panels |
| `config/constitution.yaml` | Risk gates, strategy params — handle with care |
| `config/markets.yaml` | Active markets list |
| `config/portfolio.yaml` | Portfolio-level caps and limits |
| `scripts/run_dashboard.py` | Dashboard launcher |
| `scripts/replay_runner.py` | Deterministic replay from tapes |
| `ops/systemd/` | systemd service definitions |

## Common Commands

### Run & Monitor
```bash
# Start dashboard (read-only, safe)
python scripts/run_dashboard.py --db-path tmp/<run>/runtime.db --port 8501

# Check systemd service status
systemctl status trader.service dashboard.service

# Tail live logs
journalctl -fu trader.service | tail -f

# Check gate status (quick SQLite query)
sqlite3 tmp/<run>/runtime.db "SELECT as_of_ts, is_frozen, reasons FROM system_state ORDER BY as_of_ts DESC LIMIT 3;"

# View recent alerts
sqlite3 tmp/<run>/runtime.db "SELECT datetime(ts_ms/1000,'unixepoch'), code, message FROM alerts ORDER BY ts_ms DESC LIMIT 20;"

# View recent fills
sqlite3 tmp/<run>/runtime.db "SELECT datetime(ts_ms/1000,'unixepoch'), token_id, fill_price, fill_qty FROM fills ORDER BY ts_ms DESC LIMIT 10;"
```

### Testing
```bash
# Full test suite
pytest tests/ -v

# Dashboard tests only
pytest tests/test_dashboard_*.py -v

# Fast smoke test
pytest tests/ -x -q
```

### Replay & Audit
```bash
# Replay a run tape (deterministic)
python scripts/replay_runner.py --tape-dir tmp/<run>/tapes/ --output-dir tmp/replay_<run>/

# Run audit analysis
python scripts/analyze_audit.py --run-dir tmp/<run>/

# Walk-forward report
python scripts/build_datasets.py --run-dir tmp/<run>/
```

### Market Management
```bash
# Validate markets.yaml config
python -c "from config.settings import load_settings; s = load_settings(); print('OK', len(s.markets), 'markets')"

# Check which markets have recent decisions
sqlite3 tmp/<run>/runtime.db "SELECT market, COUNT(*) as n, MAX(datetime(ts_ms/1000,'unixepoch')) as last FROM decisions GROUP BY market ORDER BY n DESC;"
```

## SQLite Table Reference

| Table | Key Columns | Written By |
|-------|-------------|-----------|
| `decisions` | `ts_ms, market, token_id, action, reason_codes, p_hat, expected_edge` | core_mm |
| `orders` | `ts_ms, order_id, token_id, side, price, qty, status, fsm_state` | core_mm |
| `fills` | `ts_ms, order_id, token_id, fill_price, fill_qty, payload_json` | core_mm |
| `inventory` | `ts_ms, token_id, yes_qty, no_qty, usdc` | core_mm |
| `pstar_stats` | `ts_ms, symbol, age_spot_ms, age_perp_ms, valid, disagreement_bps` | core_mm |
| `latency_stats` | `ts_ms, p50_send_ack_ms, p95_send_ack_ms, ws_lag_ms` | core_mm |
| `alerts` | `ts_ms, code, message` | core_mm |
| `system_state` | `as_of_ts, is_frozen, reasons, mode, payload_json` | core_mm |
| `market_data_book` | `ts_ms, token_id, side, price, size` | core_mm |
| `microstructure_stats` | `ts_ms, token_id, spread_bps, depth_at_qty_buy/sell` | core_mm |

## Gate Framework (A–E)

| Gate | Meaning | Common Breach |
|------|---------|--------------|
| **A** | Price feed validity (pstar) | Feed stale, spot/perp disagree |
| **B** | Feature/decision causality | Book timestamp leak |
| **C** | Book spread & depth | Spread too wide, slippage high |
| **D** | Hedge completeness | One-leg timeout |
| **E** | Latency & signal age | WS lag, ack latency |

## Architecture Diagram
```
Polymarket WS ──► BookManager ──► TradingMainLoop ──► OrderManager ──► Polymarket REST
                       │                │                    │
                       ▼                ▼                    ▼
                  PriceFeeds    RiskManager/Gates     PositionTracker
                       │                │                    │
                       └────────────────┴────────────────────┘
                                        │
                                   Telemetry
                                        │
                               SQLite runtime.db
                                        │
                              Streamlit Dashboard
```

## Development Conventions
- Branch: `claude/<short-description>-<session-id>` 
- Commits: imperative present tense ("add market filter", "fix gate C check")
- Tests required for any new dashboard panel or data query function
- No comments unless WHY is non-obvious
- Do not break the `PRAGMA query_only = 1` read-only guarantee on the dashboard

## Config Quick Reference

**`config/constitution.yaml` key parameters:**
- `gate_c_spread_bps_max` — Max spread before Gate C trips
- `gate_e_ws_lag_ms_max` — Max websocket lag before Gate E trips
- `trade_size_usdc` — Per-trade size in USD
- `max_net_exposure_usdc` — Total portfolio exposure cap

**`config/markets.yaml`:**
List of `{slug, condition_id, token_ids}` objects. Add new markets here.

**`config/portfolio.yaml`:**
Per-market and portfolio-level caps. Edit carefully — directly affects risk limits.
