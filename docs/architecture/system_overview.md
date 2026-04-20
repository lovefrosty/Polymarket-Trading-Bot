# System Architecture Overview

## High-Level Data Flow

```
Polymarket WebSocket ──► BookManager ──► TradingMainLoop ──► OrderManager ──► Polymarket REST API
         │                    │                  │                  │
         │                    ▼                  ▼                  ▼
         │               PriceFeeds       RiskManager         PositionTracker
         │              (pstar feed)      (Gates A–E)
         │                    │                  │
         └────────────────────┴──────────────────┘
                                      │
                                 Telemetry
                                      │
                            ┌─────────┴──────────┐
                            │   SQLite runtime.db │
                            │   (WAL mode)        │
                            └─────────┬──────────┘
                                      │  (read-only)
                              Streamlit Dashboard
                              (port 8501)
```

---

## Component Responsibilities

### `core_mm/runner.py` — CoreMMRunner
The top-level orchestrator. Manages:
- WebSocket connection to Polymarket book feed
- Calling `TradingMainLoop.run_market_cycle()` for each active market
- Modes: `OBSERVE` (watch only), `PAPER` (simulated fills), `LIVE` (real orders)
- Graceful shutdown and freeze handling

### `core_mm/main_loop.py` — TradingMainLoop
Per-market-cycle decision engine. For each cycle:
1. **BookDiagnostic** — Classify book state (healthy / stale / gapped)
2. **FlowFilter** — Evaluate order flow imbalance signals
3. **QuotePlan** — Determine desired bid/ask prices
4. **SizePlan** — Calculate buy/sell quantities within risk limits
5. **GateCheck** — Validate all A–E gates pass
6. **OrderActions** — Emit create/cancel/replace order events

Output: `MarketCycleResult` with decisions, quotes, and order actions.

### `core_mm/telemetry.py` — StandaloneTelemetry
Writes all observability data:
- **JSONL tapes**: `decisions.jsonl`, `orders.jsonl`, `fills.jsonl` (deterministic replay source)
- **SQLite tables**: flushed periodically from in-memory buffers
- **Markouts**: 1s and 5s post-fill PnL calculations

### `core/order_book.py` — OrderBook
L2 order book state per token. Key operations:
- `apply_snapshot()` / `apply_update()` — Maintain bid/ask price→size maps
- `best_bid()` / `best_ask()` / `mid()` — Top-of-book queries
- `vwap_to_fill(side, qty)` — Volume-weighted execution price
- `depth_within_ticks_bid/ask()` — Available depth near BBO
- `expected_slippage_to_fill()` — Pre-trade slippage estimate

### `dashboard/app.py` — Streamlit Dashboard
Read-only monitoring UI. Architecture:
- **Fragments** (`@st.fragment(run_every=Ns)`) for smooth partial re-renders
- **Tab layout**: CORE MM / DATA / INTEL / SYSTEM
- **Panel system**: modular renderers in `dashboard/panels/`
- **Caching**: `_heavy_df()` for expensive queries, `@st.cache_data(ttl=5)` for label registry

---

## Gate Framework (A–E)

Gates protect the bot from trading in bad conditions. All must pass for orders to be placed.

| Gate | Check | Breach Action |
|------|-------|--------------|
| **A** | Price feed valid & fresh | Freeze all trading |
| **B** | Feature timestamps causally consistent | Freeze (data quality) |
| **C** | Book spread ≤ threshold, slippage acceptable | Hold quotes for this token |
| **D** | Hedge completeness ≥ threshold | Block new primary orders |
| **E** | WS lag & ack latency ≤ thresholds | Pause until latency recovers |

---

## SQLite Data Model

All tables written by the trading engine, read by the dashboard.

```
decisions        ← core decision per tick per token
orders           ← order lifecycle events (new/cancel/fill/replace)
fills            ← confirmed fills with slippage metrics
inventory        ← position snapshots per token
pstar_stats      ← price feed quality metrics (spot + perp)
latency_stats    ← WebSocket and order ack latency
alerts           ← gate breach log
system_state     ← frozen/mode/readiness snapshot
market_data_book ← L2 book snapshots (price, size, side)
microstructure_stats ← derived spread, depth, slippage per token
discovery_requests ← market selection history
```

Database is opened with `PRAGMA query_only = 1` by the dashboard — no writes possible from UI.

---

## Configuration Files

| File | Purpose | Edit Frequency |
|------|---------|---------------|
| `config/constitution.yaml` | Risk gate thresholds, strategy params | Rarely (with testing) |
| `config/markets.yaml` | Active market list | Weekly as markets are added/removed |
| `config/portfolio.yaml` | Portfolio-level caps, per-market limits | Rarely |
| `config/settings.py` | Config loading / validation logic | Never (code, not config) |
| `config/whales.json` | Known large-player addresses | As needed |

---

## Operational Architecture

```
/etc/systemd/system/
├── trader.service      ← core_mm engine (Python asyncio)
└── dashboard.service   ← Streamlit web UI

/home/user/Polymarket-Trading-Bot/
├── tmp/
│   └── <run-timestamp>/
│       ├── runtime.db          ← live SQLite telemetry
│       ├── tapes/
│       │   ├── decisions.jsonl ← replay-capable decision log
│       │   ├── orders.jsonl
│       │   └── fills.jsonl
│       ├── logs/
│       │   └── *.log
│       └── meta/
│           └── run_summary.json
└── exports/            ← archived CSV/JSON exports
```

---

## Decision Pipeline (Per Token, Per Cycle)

```
Book Update received
       │
       ▼
BookDiagnostic.classify()
  → healthy / stale / gapped
       │
       ▼ (healthy only)
pstar feed check (Gate A)
       │
       ▼
FlowFilter.evaluate()
  → imbalance signal, whale detection
       │
       ▼
QuotePlan.compute()
  → desired bid/ask prices around pstar
       │
       ▼
SizePlan.compute()
  → buy_qty, sell_qty (respecting inventory limits)
       │
       ▼
GateCheck B/C/D/E
  → all pass? → TRADE
  → any fail? → HOLD + emit alert
       │
       ▼
OrderActions
  → create / cancel / replace orders
       │
       ▼
Telemetry.record_cycle()
  → write to SQLite + JSONL
```

---

## Key Design Decisions

**Why SQLite over a time-series DB?**  
Simplicity. The dashboard, replay, and ML pipeline all speak SQL. No separate service to run, easy to back up, easy to inspect.

**Why Streamlit over a custom frontend?**  
Fast iteration. The dashboard is purely observational; Streamlit's Python-native rendering is sufficient and eliminates a JS/API layer.

**Why fragments for refresh?**  
Streamlit's `@st.fragment(run_every=Ns)` allows partial re-renders without a full page reload. This avoids scroll jumps and keeps the UI responsive while data updates every 1–2 seconds.

**Why JSONL tapes alongside SQLite?**  
JSONL tapes are the canonical source of truth for deterministic replay. SQLite is derived from them and can be reconstructed. Never delete tapes from a completed run.

**Why Gates A–E instead of a single risk check?**  
Granularity. Each gate has a distinct cause, distinct response (freeze vs hold vs pause), and distinct alerting. Mixing them would make root-cause analysis harder.

---

## Scaling Considerations

**As markets grow (>10 active):**
- Each market adds ~1 WebSocket subscription
- CPU per cycle scales roughly linearly with market count
- Consider parallelizing `run_market_cycle()` across markets (currently sequential per runner tick)
- DB write load increases; consider periodic `VACUUM` and WAL checkpoint scheduling

**As data volume grows (>100k decisions/day):**
- Dashboard queries may slow. Add indexes on `decisions(ts_ms)`, `orders(ts_ms)`, `fills(ts_ms)`
- Consider partitioned SQLite (daily DB files) with a view over them
- Archive old data to Parquet for ML training

**As complexity grows (multiple strategies):**
- `constitution.yaml` already supports strategy-level config
- Add per-strategy telemetry columns to decisions table
- Dashboard `build_signals_table_for_view()` has `strategy` column support built-in
