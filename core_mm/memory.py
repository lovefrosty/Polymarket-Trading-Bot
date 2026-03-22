"""Epic 4: OpenViking Memory Layer.

Persistent per-market memory that survives across trading sessions.
Stores learnings about each market/symbol that the alpha overlays and
risk manager can query to make better decisions.

Architecture:
    MemoryStore (SQLite) ← MarketMemory (per-market facts)
                         ← SessionSummary (per-run rollup)

Key memories stored:
- **Spread profile**: typical spread, spread volatility for this market
- **Fill quality**: historical adversity ratio, fill rate
- **Volatility profile**: typical vol regime, how often high-vol occurs
- **Position behaviour**: max position reached, avg holding time
- **PnL profile**: avg PnL per market, win rate per market

The memory layer does NOT make trading decisions — it provides context
that other components (alpha overlays, risk manager, market selector)
can use to adjust their parameters.
"""
from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional


@dataclass
class MarketMemory:
    """Facts remembered about a specific market or symbol."""

    symbol: str
    market_slug: str = ""

    # Spread profile
    avg_spread_bps: float = 0.0
    spread_observations: int = 0

    # Fill quality
    total_fills: int = 0
    adverse_fills: int = 0
    avg_fill_rate: float = 0.0

    # Volatility profile
    avg_realized_vol_bps: float = 0.0
    high_vol_fraction: float = 0.0
    vol_observations: int = 0

    # PnL profile
    total_pnl: float = 0.0
    total_sessions: int = 0
    best_session_pnl: float = 0.0
    worst_session_pnl: float = 0.0
    win_sessions: int = 0

    # Position behaviour
    max_position_seen: float = 0.0

    # Timestamp
    last_updated_ms: int = 0

    def adversity_ratio(self) -> float:
        if self.total_fills < 5:
            return 0.0
        return self.adverse_fills / self.total_fills

    def session_win_rate(self) -> float:
        if self.total_sessions == 0:
            return 0.0
        return self.win_sessions / self.total_sessions

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class SessionSummary:
    """Summary of a single trading session for memory ingestion."""

    run_id: str
    symbol: str
    market_slug: str = ""
    duration_secs: float = 0.0
    total_fills: int = 0
    adverse_fills: int = 0
    fill_rate: float = 0.0
    realized_pnl: float = 0.0
    avg_spread_bps: float = 0.0
    avg_vol_bps: float = 0.0
    high_vol_fraction: float = 0.0
    max_position: float = 0.0
    decisions: int = 0
    ts_ms: int = 0


# ── Memory Store ────────────────────────────────────────────────────────────


_SCHEMA = """
CREATE TABLE IF NOT EXISTS market_memory (
    symbol          TEXT NOT NULL,
    market_slug     TEXT NOT NULL DEFAULT '',
    data_json       TEXT NOT NULL,
    updated_at_ms   INTEGER NOT NULL,
    PRIMARY KEY (symbol, market_slug)
);

CREATE TABLE IF NOT EXISTS session_history (
    run_id          TEXT NOT NULL PRIMARY KEY,
    symbol          TEXT NOT NULL,
    market_slug     TEXT NOT NULL DEFAULT '',
    summary_json    TEXT NOT NULL,
    ts_ms           INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_session_symbol ON session_history (symbol, ts_ms);
"""


class MemoryStore:
    """SQLite-backed persistent memory for market learnings.

    Usage:
        store = MemoryStore(Path("memory.db"))
        mem = store.recall("BTC")          # Get what we know about BTC
        store.ingest_session(summary)      # Learn from a completed session
        store.save(mem)                    # Save updated memory
    """

    def __init__(self, db_path: Path) -> None:
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self._db_path))
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def recall(self, symbol: str, market_slug: str = "") -> MarketMemory:
        """Recall what we know about a market. Returns empty memory if new."""
        cursor = self._conn.execute(
            "SELECT data_json FROM market_memory WHERE symbol = ? AND market_slug = ?",
            (str(symbol).upper(), str(market_slug)),
        )
        row = cursor.fetchone()
        if row is None:
            return MarketMemory(symbol=str(symbol).upper(), market_slug=str(market_slug))
        data = json.loads(row[0])
        return MarketMemory(**{k: v for k, v in data.items() if k in MarketMemory.__dataclass_fields__})

    def save(self, memory: MarketMemory) -> None:
        """Persist market memory."""
        memory.last_updated_ms = int(time.time() * 1000)
        self._conn.execute(
            "INSERT OR REPLACE INTO market_memory (symbol, market_slug, data_json, updated_at_ms) VALUES (?, ?, ?, ?)",
            (memory.symbol, memory.market_slug, json.dumps(memory.to_dict()), memory.last_updated_ms),
        )
        self._conn.commit()

    def ingest_session(self, summary: SessionSummary) -> MarketMemory:
        """Learn from a completed trading session. Returns updated memory."""
        # Save session history
        self._conn.execute(
            "INSERT OR REPLACE INTO session_history (run_id, symbol, market_slug, summary_json, ts_ms) VALUES (?, ?, ?, ?, ?)",
            (
                summary.run_id,
                summary.symbol,
                summary.market_slug,
                json.dumps(asdict(summary)),
                summary.ts_ms or int(time.time() * 1000),
            ),
        )
        self._conn.commit()

        # Update market memory with session data
        mem = self.recall(summary.symbol, summary.market_slug)
        mem.total_sessions += 1
        mem.total_fills += summary.total_fills
        mem.adverse_fills += summary.adverse_fills
        mem.total_pnl += summary.realized_pnl

        if summary.realized_pnl > 0:
            mem.win_sessions += 1
        mem.best_session_pnl = max(mem.best_session_pnl, summary.realized_pnl)
        mem.worst_session_pnl = min(mem.worst_session_pnl, summary.realized_pnl)
        mem.max_position_seen = max(mem.max_position_seen, summary.max_position)

        # Running averages
        n = mem.total_sessions
        if n > 0:
            mem.avg_spread_bps = mem.avg_spread_bps * (n - 1) / n + summary.avg_spread_bps / n
            mem.avg_fill_rate = mem.avg_fill_rate * (n - 1) / n + summary.fill_rate / n
            mem.avg_realized_vol_bps = mem.avg_realized_vol_bps * (n - 1) / n + summary.avg_vol_bps / n
            mem.high_vol_fraction = mem.high_vol_fraction * (n - 1) / n + summary.high_vol_fraction / n

        mem.spread_observations += summary.decisions
        mem.vol_observations += summary.decisions

        self.save(mem)
        return mem

    def list_symbols(self) -> List[str]:
        """List all symbols with stored memory."""
        cursor = self._conn.execute(
            "SELECT DISTINCT symbol FROM market_memory ORDER BY symbol"
        )
        return [row[0] for row in cursor.fetchall()]

    def get_session_history(self, symbol: str, limit: int = 20) -> List[SessionSummary]:
        """Get recent session summaries for a symbol."""
        cursor = self._conn.execute(
            "SELECT summary_json FROM session_history WHERE symbol = ? ORDER BY ts_ms DESC LIMIT ?",
            (str(symbol).upper(), int(limit)),
        )
        results: List[SessionSummary] = []
        for row in cursor.fetchall():
            data = json.loads(row[0])
            results.append(
                SessionSummary(**{k: v for k, v in data.items() if k in SessionSummary.__dataclass_fields__})
            )
        return results

    def get_all_memories(self) -> List[MarketMemory]:
        """Get all stored market memories."""
        cursor = self._conn.execute("SELECT data_json FROM market_memory ORDER BY symbol")
        results: List[MarketMemory] = []
        for row in cursor.fetchall():
            data = json.loads(row[0])
            results.append(
                MarketMemory(**{k: v for k, v in data.items() if k in MarketMemory.__dataclass_fields__})
            )
        return results
