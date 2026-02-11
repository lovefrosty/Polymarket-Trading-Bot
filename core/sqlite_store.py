from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import sqlite3
import threading
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple


_SCHEMA = [
    """
    CREATE TABLE IF NOT EXISTS market_data_book (
        ts_ms INTEGER NOT NULL,
        token_id TEXT NOT NULL,
        side TEXT NOT NULL,
        price REAL NOT NULL,
        size REAL NOT NULL,
        source TEXT NOT NULL DEFAULT 'ws'
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS market_trades (
        ts_ms INTEGER NOT NULL,
        token_id TEXT NOT NULL,
        side TEXT,
        price REAL NOT NULL,
        size REAL NOT NULL,
        trade_id TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS pstar (
        ts_ms INTEGER NOT NULL,
        symbol TEXT NOT NULL,
        value REAL,
        ts_event_ms INTEGER,
        confidence REAL NOT NULL,
        valid INTEGER NOT NULL,
        sources_used TEXT NOT NULL,
        diagnostics_json TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS decisions (
        ts_ms INTEGER NOT NULL,
        decision_id TEXT PRIMARY KEY,
        market TEXT NOT NULL,
        token_id TEXT NOT NULL,
        action TEXT NOT NULL,
        reason_codes TEXT NOT NULL,
        p_hat REAL,
        expected_edge REAL,
        expected_cost REAL,
        decision_ts_event_ms INTEGER NOT NULL,
        book_asof_ts_ms INTEGER,
        pstar_asof_ts_ms INTEGER,
        max_feature_ts_ms INTEGER NOT NULL,
        policy_json TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS orders (
        ts_ms INTEGER NOT NULL,
        event_id TEXT NOT NULL,
        order_id TEXT NOT NULL,
        client_order_id TEXT,
        token_id TEXT NOT NULL,
        side TEXT NOT NULL,
        price REAL,
        qty REAL,
        post_only INTEGER NOT NULL,
        tif TEXT,
        status TEXT NOT NULL,
        reason TEXT,
        quote_group_id TEXT,
        idempotency_key TEXT,
        fsm_state TEXT,
        payload_json TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS fills (
        ts_ms INTEGER NOT NULL,
        event_id TEXT PRIMARY KEY,
        order_id TEXT NOT NULL,
        token_id TEXT NOT NULL,
        side TEXT NOT NULL,
        fill_price REAL NOT NULL,
        fill_qty REAL NOT NULL,
        fee REAL,
        liquidity TEXT,
        payload_json TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS exec_latency (
        ts_ms INTEGER NOT NULL,
        event_id TEXT PRIMARY KEY,
        decision_id TEXT,
        token_id TEXT,
        signal_age_ms REAL,
        send_ack_ms REAL,
        ack_fill_ms REAL,
        ws_lag_ms REAL,
        payload_json TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS alerts (
        ts_ms INTEGER NOT NULL,
        severity TEXT NOT NULL,
        code TEXT NOT NULL,
        message TEXT NOT NULL,
        payload_json TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS logs (
        ts_ms INTEGER NOT NULL,
        level TEXT NOT NULL,
        msg TEXT NOT NULL,
        payload_json TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS inventory (
        ts_ms INTEGER NOT NULL,
        token_id TEXT NOT NULL,
        yes_qty REAL NOT NULL,
        no_qty REAL NOT NULL,
        usdc REAL,
        source TEXT NOT NULL,
        payload_json TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS system_state (
        as_of_ts INTEGER PRIMARY KEY,
        is_frozen INTEGER NOT NULL,
        reasons TEXT NOT NULL,
        mode TEXT NOT NULL,
        payload_json TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS latency_stats (
        ts_ms INTEGER PRIMARY KEY,
        p50_send_ack_ms REAL,
        p95_send_ack_ms REAL,
        p50_ack_fill_ms REAL,
        p95_ack_fill_ms REAL,
        ws_lag_ms REAL,
        p50_signal_age_ms REAL,
        p95_signal_age_ms REAL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS pstar_stats (
        ts_ms INTEGER PRIMARY KEY,
        symbol TEXT NOT NULL,
        p_spot REAL,
        age_spot_ms INTEGER,
        p_perp REAL,
        age_perp_ms INTEGER,
        disagreement_bps REAL,
        confidence REAL,
        valid INTEGER NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS microstructure_stats (
        ts_ms INTEGER NOT NULL,
        event_id TEXT PRIMARY KEY,
        token_id TEXT NOT NULL,
        book_health TEXT NOT NULL,
        spread_bps REAL,
        depth_at_qty_buy REAL,
        depth_at_qty_sell REAL,
        slippage_bps_buy REAL,
        slippage_bps_sell REAL,
        effective_spread_bps_buy REAL,
        effective_spread_bps_sell REAL,
        post_only_reject_rate REAL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS reconciliation_stats (
        ts_ms INTEGER NOT NULL,
        event_id TEXT PRIMARY KEY,
        broker_open_orders INTEGER NOT NULL,
        broker_inventory REAL,
        onchain_inventory REAL,
        mismatch_count INTEGER NOT NULL,
        unresolved_mismatch_count INTEGER NOT NULL,
        payload_json TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS recovery_events (
        ts_ms INTEGER NOT NULL,
        event_id TEXT PRIMARY KEY,
        recovery_action TEXT NOT NULL,
        token_id TEXT,
        side TEXT,
        order_id TEXT,
        adopted_order_count INTEGER,
        payload_json TEXT NOT NULL
    )
    """,
]


class SQLiteStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._cx = sqlite3.connect(self.path.as_posix(), check_same_thread=False)
        self._cx.execute("PRAGMA journal_mode=WAL")
        self._cx.execute("PRAGMA synchronous=NORMAL")
        self._lock = threading.Lock()
        self._init_schema()

    def close(self) -> None:
        with self._lock:
            self._cx.commit()
            self._cx.close()

    def insert(self, table: str, row: Dict[str, Any]) -> None:
        keys = sorted(row.keys())
        cols = ",".join(keys)
        placeholders = ",".join(["?"] * len(keys))
        values = [_coerce_sql(row[key]) for key in keys]
        sql = f"INSERT INTO {table} ({cols}) VALUES ({placeholders})"
        with self._lock:
            self._cx.execute(sql, values)
            self._cx.commit()

    def insert_many(self, table: str, rows: Iterable[Dict[str, Any]]) -> None:
        rows_list = [dict(row) for row in rows]
        if not rows_list:
            return
        keys = sorted(rows_list[0].keys())
        cols = ",".join(keys)
        placeholders = ",".join(["?"] * len(keys))
        sql = f"INSERT INTO {table} ({cols}) VALUES ({placeholders})"
        values = [[_coerce_sql(row.get(key)) for key in keys] for row in rows_list]
        with self._lock:
            self._cx.executemany(sql, values)
            self._cx.commit()

    def query(self, sql: str, params: Sequence[Any] | None = None) -> List[Tuple[Any, ...]]:
        with self._lock:
            cur = self._cx.execute(sql, params or [])
            return cur.fetchall()

    def export_table_jsonl(self, table: str, out_path: str | Path, order_by: str = "ts_ms") -> None:
        out = Path(out_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        with self._lock:
            cur = self._cx.execute(f"SELECT * FROM {table} ORDER BY {order_by}")
            columns = [d[0] for d in cur.description]
            rows = cur.fetchall()
        lines: List[str] = []
        for row in rows:
            payload = {key: row[idx] for idx, key in enumerate(columns)}
            lines.append(json.dumps(payload, separators=(",", ":"), ensure_ascii=True, sort_keys=True))
        out.write_text("\n".join(lines), encoding="utf-8")

    def upsert_system_state(
        self,
        as_of_ts: int,
        is_frozen: bool,
        reasons: str,
        mode: str,
        payload: Optional[Dict[str, Any]] = None,
    ) -> None:
        payload_json = _as_json(payload or {})
        with self._lock:
            self._cx.execute(
                """
                INSERT INTO system_state (as_of_ts, is_frozen, reasons, mode, payload_json)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(as_of_ts) DO UPDATE SET
                    is_frozen=excluded.is_frozen,
                    reasons=excluded.reasons,
                    mode=excluded.mode,
                    payload_json=excluded.payload_json
                """,
                (int(as_of_ts), 1 if is_frozen else 0, str(reasons), str(mode), payload_json),
            )
            self._cx.commit()

    def append_log(self, ts_ms: int, level: str, msg: str, payload: Optional[Dict[str, Any]] = None) -> None:
        self.insert(
            "logs",
            {
                "ts_ms": int(ts_ms),
                "level": str(level),
                "msg": str(msg),
                "payload_json": _as_json(payload or {}),
            },
        )

    def append_alert(
        self,
        ts_ms: int,
        severity: str,
        code: str,
        message: str,
        payload: Optional[Dict[str, Any]] = None,
    ) -> None:
        self.insert(
            "alerts",
            {
                "ts_ms": int(ts_ms),
                "severity": str(severity),
                "code": str(code),
                "message": str(message),
                "payload_json": _as_json(payload or {}),
            },
        )

    def _init_schema(self) -> None:
        with self._lock:
            for stmt in _SCHEMA:
                self._cx.execute(stmt)
            self._ensure_columns(
                "orders",
                {
                    "quote_group_id": "TEXT",
                    "idempotency_key": "TEXT",
                },
            )
            self._ensure_columns(
                "latency_stats",
                {
                    "p50_signal_age_ms": "REAL",
                    "p95_signal_age_ms": "REAL",
                },
            )
            self._cx.commit()

    def _ensure_columns(self, table: str, expected: Dict[str, str]) -> None:
        existing = {
            str(row[1])
            for row in self._cx.execute(f"PRAGMA table_info({table})").fetchall()
            if row and len(row) > 1
        }
        for column, sql_type in expected.items():
            if column in existing:
                continue
            self._cx.execute(f"ALTER TABLE {table} ADD COLUMN {column} {sql_type}")


def _as_json(payload: Dict[str, Any]) -> str:
    return json.dumps(payload, separators=(",", ":"), ensure_ascii=True, sort_keys=True)


def _coerce_sql(value: Any) -> Any:
    if isinstance(value, (dict, list, tuple, set)):
        return json.dumps(value, separators=(",", ":"), ensure_ascii=True, sort_keys=True)
    return value
