from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import sqlite3
import threading
import time
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple
import weakref


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
        pstar_recv_ts_ms INTEGER,
        confidence REAL NOT NULL,
        valid INTEGER NOT NULL,
        invalid_reason TEXT,
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
        run_id TEXT,
        mode TEXT,
        market_slug TEXT,
        condition_id TEXT,
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
        reason_code TEXT,
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
        run_id TEXT,
        mode TEXT,
        market_slug TEXT,
        condition_id TEXT,
        order_id TEXT NOT NULL,
        token_id TEXT NOT NULL,
        side TEXT NOT NULL,
        fill_price REAL NOT NULL,
        fill_qty REAL NOT NULL,
        fee REAL,
        liquidity TEXT,
        reason_code TEXT,
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
    CREATE TABLE IF NOT EXISTS execution_quality (
        ts_ms INTEGER NOT NULL,
        event_id TEXT PRIMARY KEY,
        run_id TEXT,
        mode TEXT,
        token_id TEXT NOT NULL,
        order_id TEXT NOT NULL,
        side TEXT NOT NULL,
        fill_ts_ms INTEGER NOT NULL,
        fill_price REAL NOT NULL,
        fill_qty REAL NOT NULL,
        fee_bps REAL,
        mid_at_send REAL,
        mid_at_ack REAL,
        mid_at_fill REAL,
        mid_1s REAL,
        mid_5s REAL,
        mid_30s REAL,
        realized_spread_bps REAL,
        markout_1s_bps REAL,
        markout_5s_bps REAL,
        markout_30s_bps REAL,
        net_edge_bps REAL,
        payload_json TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS execution_quality_stats (
        ts_ms INTEGER NOT NULL,
        event_id TEXT PRIMARY KEY,
        token_id TEXT NOT NULL,
        sample_count INTEGER NOT NULL,
        p50_realized_spread_bps REAL,
        p95_realized_spread_bps REAL,
        p50_markout_1s_bps REAL,
        p95_markout_1s_bps REAL,
        p50_markout_5s_bps REAL,
        p95_markout_5s_bps REAL,
        p50_markout_30s_bps REAL,
        p95_markout_30s_bps REAL,
        p50_net_edge_bps REAL,
        p95_net_edge_bps REAL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS queue_quality_stats (
        ts_ms INTEGER NOT NULL,
        event_id TEXT PRIMARY KEY,
        token_id TEXT NOT NULL,
        post_only_reject_rate REAL,
        cancel_to_fill_ratio REAL,
        time_to_first_fill_p50_s REAL,
        time_to_first_fill_p95_s REAL,
        partial_fill_count INTEGER,
        orders_per_min REAL,
        cancels_per_min REAL,
        fills_per_min REAL,
        payload_json TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS liveness_stats (
        ts_ms INTEGER NOT NULL,
        event_id TEXT PRIMARY KEY,
        mode TEXT NOT NULL,
        clock_drift_ms REAL,
        sequence_gap_rate_per_min REAL,
        sequence_gap_count_1m INTEGER,
        ws_starvation_token_count INTEGER,
        max_ws_starvation_ms REAL,
        active_market_lag_ms REAL,
        freeze_state INTEGER,
        reason_codes TEXT NOT NULL,
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
    CREATE TABLE IF NOT EXISTS evidence_rows (
        ts_ms INTEGER NOT NULL,
        event_id TEXT PRIMARY KEY,
        source TEXT NOT NULL,
        entity TEXT NOT NULL,
        event_type TEXT NOT NULL,
        reason_code TEXT,
        payload_json TEXT NOT NULL,
        severity TEXT NOT NULL
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
        p95_signal_age_ms REAL,
        p50_ws_lag_ms REAL,
        p95_ws_lag_ms REAL,
        p50_pstar_age_ms REAL,
        p95_pstar_age_ms REAL
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
        run_id TEXT,
        mode TEXT,
        broker_open_orders INTEGER NOT NULL,
        broker_inventory REAL,
        onchain_inventory REAL,
        derived_inventory REAL,
        inventory_delta_qty REAL,
        inventory_delta_usdc REAL,
        tolerance_qty REAL,
        tolerance_usdc REAL,
        outside_tolerance INTEGER,
        mismatch_count INTEGER NOT NULL,
        unresolved_mismatch_count INTEGER NOT NULL,
        consecutive_mismatch_cycles INTEGER,
        consecutive_onchain_disagree_cycles INTEGER,
        freeze_state INTEGER,
        freeze_reason TEXT,
        payload_json TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS recovery_events (
        ts_ms INTEGER NOT NULL,
        event_id TEXT PRIMARY KEY,
        run_id TEXT,
        mode TEXT,
        recovery_action TEXT NOT NULL,
        token_id TEXT,
        side TEXT,
        order_id TEXT,
        price REAL,
        size REAL,
        adopted_order_count INTEGER,
        payload_json TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS open_orders_snapshot (
        ts_ms INTEGER NOT NULL,
        event_id TEXT PRIMARY KEY,
        run_id TEXT,
        mode TEXT,
        token_id TEXT,
        side TEXT,
        order_id TEXT NOT NULL,
        price REAL,
        size REAL,
        status TEXT,
        client_order_id TEXT,
        quote_group_id TEXT,
        payload_json TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS seen_fill_events (
        fill_event_key TEXT PRIMARY KEY,
        first_seen_ts_ms INTEGER NOT NULL,
        source TEXT NOT NULL,
        payload_json TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS decision_ticks (
        ts_ms INTEGER NOT NULL,
        event_id TEXT PRIMARY KEY,
        decision_ts_ms INTEGER NOT NULL,
        token_id TEXT NOT NULL,
        decision_id TEXT,
        book_asof_ts_ms INTEGER,
        book_recv_ts_ms INTEGER,
        book_seq INTEGER,
        book_level_count INTEGER,
        book_health_state TEXT,
        pstar_value REAL,
        pstar_asof_ts_ms INTEGER,
        pstar_recv_ts_ms INTEGER,
        pstar_sourceset TEXT,
        pstar_confidence REAL,
        pstar_valid INTEGER NOT NULL,
        invalid_reason TEXT,
        max_feature_ts_ms INTEGER NOT NULL,
        ws_lag_ms REAL,
        pstar_age_ms REAL,
        signal_age_ms REAL,
        allow_action INTEGER NOT NULL,
        block_reason_codes TEXT NOT NULL,
        payload_json TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS decision_trace (
        ts_ms INTEGER NOT NULL,
        event_id TEXT PRIMARY KEY,
        decision_id TEXT NOT NULL,
        token_id TEXT NOT NULL,
        market_slug TEXT,
        action TEXT NOT NULL,
        allow_action INTEGER NOT NULL,
        input_asof_ts_ms INTEGER NOT NULL,
        gate_reason_codes TEXT NOT NULL,
        payload_json TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS book_health_stats (
        ts_ms INTEGER NOT NULL,
        event_id TEXT PRIMARY KEY,
        token_id TEXT NOT NULL,
        book_asof_ts_ms INTEGER,
        book_recv_ts_ms INTEGER,
        book_seq INTEGER,
        book_level_count INTEGER,
        book_health_state TEXT NOT NULL,
        book_age_p50_ms REAL,
        book_age_p95_ms REAL,
        ws_recv_rate_msgs_min REAL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS rollover_metrics (
        ts_ms INTEGER NOT NULL,
        event_id TEXT PRIMARY KEY,
        metric_name TEXT NOT NULL,
        metric_value REAL,
        market_slug TEXT,
        selection_key TEXT,
        payload_json TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS rollover_status (
        ts_ms INTEGER NOT NULL,
        event_id TEXT PRIMARY KEY,
        event_type TEXT NOT NULL,
        market_slug TEXT,
        selection_key TEXT,
        end_ts_source TEXT,
        readiness_ok INTEGER,
        readiness_reason_codes TEXT,
        confirm_wait_ms REAL,
        commit_block_ms REAL,
        unsubscribe_ms REAL,
        unknown_msg_count INTEGER,
        ignored_old_rate_per_min REAL,
        payload_json TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS discovery_requests (
        ts_ms INTEGER NOT NULL,
        event_id TEXT PRIMARY KEY,
        requested_symbol TEXT NOT NULL,
        requested_horizon TEXT NOT NULL,
        mode TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'UNKNOWN',
        now_ms INTEGER NOT NULL,
        selected_slug TEXT,
        end_ts_ms INTEGER,
        end_ts_source TEXT,
        reason_code TEXT,
        retry_index INTEGER,
        next_retry_ts_ms INTEGER,
        counts_json TEXT NOT NULL,
        payload_json TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS ws_subscribe_attempts (
        run_id TEXT NOT NULL,
        ts_ms INTEGER NOT NULL,
        attempt_id INTEGER NOT NULL,
        action TEXT,
        active_sub_id_before INTEGER,
        pending_sub_id INTEGER,
        asset_ids_json TEXT,
        payload_json TEXT,
        ack_status TEXT,
        ack_ts_ms INTEGER,
        ack_error TEXT,
        preclass_pending_hits INTEGER,
        preclass_active_hits INTEGER,
        preclass_unknown_schema INTEGER,
        preclass_missing_asset INTEGER,
        preclass_missing_sub INTEGER,
        confirm_required_updates INTEGER,
        confirm_counts_by_asset_json TEXT,
        confirm_preclass_hits_by_asset_json TEXT,
        first_pending_recv_ts_ms INTEGER,
        last_pending_recv_ts_ms INTEGER,
        confirm_wait_ms REAL,
        result TEXT
    )
    """,
]


class SQLiteStore:
    _BATCH_TABLES = {"market_data_book", "market_trades"}
    _BATCH_MAX_ROWS = 64
    _BATCH_MAX_AGE_MS = 100

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._cx = sqlite3.connect(self.path.as_posix(), check_same_thread=False)
        self._cx.execute("PRAGMA journal_mode=WAL")
        self._cx.execute("PRAGMA synchronous=NORMAL")
        self._lock = threading.Lock()
        self._pending_batch_rows: int = 0
        self._pending_batch_started_ns: Optional[int] = None
        self._closed = False
        self._finalizer = weakref.finalize(self, SQLiteStore._close_connection, self._cx)
        self._init_schema()

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._flush_batch_locked(force=True)
            self._finalizer()
            self._closed = True

    def __enter__(self) -> "SQLiteStore":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:
            pass

    @staticmethod
    def _close_connection(cx: sqlite3.Connection) -> None:
        try:
            cx.commit()
        except Exception:
            pass
        try:
            cx.close()
        except Exception:
            pass

    def _is_batched_table(self, table: str) -> bool:
        return table in self._BATCH_TABLES

    def _note_batched_rows_locked(self, count: int) -> None:
        if count <= 0:
            return
        if self._pending_batch_rows <= 0:
            self._pending_batch_started_ns = time.monotonic_ns()
        self._pending_batch_rows += count

    def _batch_age_ms_locked(self) -> float:
        if self._pending_batch_rows <= 0 or self._pending_batch_started_ns is None:
            return 0.0
        return max(0.0, (time.monotonic_ns() - self._pending_batch_started_ns) / 1_000_000.0)

    def _reset_batch_state_locked(self) -> None:
        self._pending_batch_rows = 0
        self._pending_batch_started_ns = None

    def _flush_batch_locked(self, force: bool = False) -> None:
        if self._pending_batch_rows <= 0:
            return
        if not force:
            if self._pending_batch_rows < self._BATCH_MAX_ROWS and self._batch_age_ms_locked() < self._BATCH_MAX_AGE_MS:
                return
        self._cx.commit()
        self._reset_batch_state_locked()

    def insert(self, table: str, row: Dict[str, Any]) -> None:
        keys = sorted(row.keys())
        cols = ",".join(keys)
        placeholders = ",".join(["?"] * len(keys))
        values = [_coerce_sql(row[key]) for key in keys]
        sql = f"INSERT INTO {table} ({cols}) VALUES ({placeholders})"
        with self._lock:
            self._cx.execute(sql, values)
            self._insert_evidence_from_row_locked(table, row)
            if self._is_batched_table(table):
                self._note_batched_rows_locked(1)
                self._flush_batch_locked(force=False)
            else:
                self._cx.commit()
                self._reset_batch_state_locked()

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
            for row in rows_list:
                self._insert_evidence_from_row_locked(table, row)
            if self._is_batched_table(table):
                self._note_batched_rows_locked(len(rows_list))
                self._flush_batch_locked(force=False)
            else:
                self._cx.commit()
                self._reset_batch_state_locked()

    def _insert_evidence_from_row_locked(self, table: str, row: Dict[str, Any]) -> None:
        if table == "evidence_rows":
            return
        if table not in {"decisions", "orders", "fills"}:
            return

        ts_ms = _maybe_int(row.get("ts_ms"))
        if ts_ms is None:
            return

        severity = "info"
        reason_code = None
        entity = ""
        event_type = table
        payload: Dict[str, Any] = {}

        if table == "decisions":
            event_type = str(row.get("action") or "decision")
            entity = ":".join(
                [
                    str(row.get("market") or ""),
                    str(row.get("token_id") or ""),
                    str(row.get("decision_id") or ""),
                ]
            )
            reason_code = str(row.get("reason_codes") or "") or None
            if str(event_type).upper() == "FREEZE":
                severity = "error"
            payload = {"decision_id": row.get("decision_id"), "p_hat": row.get("p_hat")}
        elif table == "orders":
            event_type = str(row.get("status") or "order")
            entity = ":".join([str(row.get("token_id") or ""), str(row.get("order_id") or "")])
            reason_code = str(row.get("reason") or "") or None
            status = str(row.get("status") or "").lower()
            if "reject" in status:
                severity = "error"
            elif "cancel" in status:
                severity = "warn"
            payload = {"event_id": row.get("event_id"), "fsm_state": row.get("fsm_state")}
        elif table == "fills":
            event_type = "fill"
            entity = ":".join([str(row.get("token_id") or ""), str(row.get("order_id") or "")])
            payload = {"event_id": row.get("event_id"), "fill_price": row.get("fill_price"), "fill_qty": row.get("fill_qty")}

        self._cx.execute(
            """
            INSERT INTO evidence_rows (ts_ms, event_id, source, entity, event_type, reason_code, payload_json, severity)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                int(ts_ms),
                uuid4_hex(),
                "runtime",
                entity,
                event_type,
                reason_code,
                _as_json(payload),
                severity,
            ),
        )

    def query(self, sql: str, params: Sequence[Any] | None = None) -> List[Tuple[Any, ...]]:
        with self._lock:
            cur = self._cx.execute(sql, params or [])
            return cur.fetchall()

    def execute(self, sql: str, params: Sequence[Any] | None = None) -> None:
        with self._lock:
            self._flush_batch_locked(force=True)
            self._cx.execute(sql, params or [])
            self._cx.commit()

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
            self._flush_batch_locked(force=True)
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
        self.append_evidence_row(
            ts_ms=int(ts_ms),
            source="runtime",
            entity=str(level).upper(),
            event_type=str(msg),
            reason_code=None,
            payload=payload or {},
            severity="error" if str(level).upper() == "ERROR" else ("warn" if str(level).upper() == "WARN" else "info"),
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
        self.append_evidence_row(
            ts_ms=int(ts_ms),
            source="runtime",
            entity=str(code),
            event_type=str(code),
            reason_code=str(code),
            payload=payload or {},
            severity=str(severity).lower(),
        )

    def append_evidence_row(
        self,
        ts_ms: int,
        source: str,
        entity: str,
        event_type: str,
        reason_code: Optional[str],
        payload: Optional[Dict[str, Any]] = None,
        severity: str = "info",
    ) -> None:
        self.insert(
            "evidence_rows",
            {
                "ts_ms": int(ts_ms),
                "event_id": uuid4_hex(),
                "source": str(source),
                "entity": str(entity),
                "event_type": str(event_type),
                "reason_code": str(reason_code) if reason_code is not None else None,
                "payload_json": _as_json(payload or {}),
                "severity": str(severity).lower(),
            },
        )

    def append_rollover_metric(
        self,
        ts_ms: int,
        metric_name: str,
        metric_value: Optional[float],
        market_slug: Optional[str] = None,
        selection_key: Optional[str] = None,
        payload: Optional[Dict[str, Any]] = None,
    ) -> None:
        self.insert(
            "rollover_metrics",
            {
                "ts_ms": int(ts_ms),
                "event_id": uuid4_hex(),
                "metric_name": str(metric_name),
                "metric_value": _maybe_float(metric_value),
                "market_slug": str(market_slug) if market_slug is not None else None,
                "selection_key": str(selection_key) if selection_key is not None else None,
                "payload_json": _as_json(payload or {}),
            },
        )

    def append_rollover_status(
        self,
        ts_ms: int,
        event_type: str,
        market_slug: Optional[str],
        selection_key: Optional[str],
        end_ts_source: Optional[str],
        readiness_ok: Optional[bool],
        readiness_reason_codes: Optional[Sequence[str]],
        confirm_wait_ms: Optional[float],
        commit_block_ms: Optional[float],
        unsubscribe_ms: Optional[float],
        unknown_msg_count: Optional[int],
        ignored_old_rate_per_min: Optional[float],
        payload: Optional[Dict[str, Any]] = None,
    ) -> None:
        self.insert(
            "rollover_status",
            {
                "ts_ms": int(ts_ms),
                "event_id": uuid4_hex(),
                "event_type": str(event_type),
                "market_slug": str(market_slug) if market_slug is not None else None,
                "selection_key": str(selection_key) if selection_key is not None else None,
                "end_ts_source": str(end_ts_source) if end_ts_source is not None else None,
                "readiness_ok": None if readiness_ok is None else (1 if readiness_ok else 0),
                "readiness_reason_codes": ",".join(sorted(set(readiness_reason_codes or []))),
                "confirm_wait_ms": _maybe_float(confirm_wait_ms),
                "commit_block_ms": _maybe_float(commit_block_ms),
                "unsubscribe_ms": _maybe_float(unsubscribe_ms),
                "unknown_msg_count": None if unknown_msg_count is None else int(unknown_msg_count),
                "ignored_old_rate_per_min": _maybe_float(ignored_old_rate_per_min),
                "payload_json": _as_json(payload or {}),
            },
        )

    def append_discovery_request(
        self,
        ts_ms: int,
        requested_symbol: str,
        requested_horizon: str,
        mode: str,
        status: str,
        now_ms: int,
        selected_slug: Optional[str],
        end_ts_ms: Optional[int],
        end_ts_source: Optional[str],
        reason_code: Optional[str],
        retry_index: Optional[int] = None,
        next_retry_ts_ms: Optional[int] = None,
        counts: Optional[Dict[str, Any]] = None,
        payload: Optional[Dict[str, Any]] = None,
    ) -> None:
        self.insert(
            "discovery_requests",
            {
                "ts_ms": int(ts_ms),
                "event_id": uuid4_hex(),
                "requested_symbol": str(requested_symbol),
                "requested_horizon": str(requested_horizon),
                "mode": str(mode),
                "status": str(status),
                "now_ms": int(now_ms),
                "selected_slug": str(selected_slug) if selected_slug is not None else None,
                "end_ts_ms": _maybe_int(end_ts_ms),
                "end_ts_source": str(end_ts_source) if end_ts_source is not None else None,
                "reason_code": str(reason_code) if reason_code is not None else None,
                "retry_index": _maybe_int(retry_index),
                "next_retry_ts_ms": _maybe_int(next_retry_ts_ms),
                "counts_json": _as_json(counts or {}),
                "payload_json": _as_json(payload or {}),
            },
        )

    def append_ws_subscribe_attempt(
        self,
        run_id: str,
        ts_ms: int,
        attempt_id: int,
        action: str,
        active_sub_id_before: Optional[int],
        pending_sub_id: Optional[int],
        asset_ids_json: str,
        payload_json: str,
        ack_status: Optional[str],
        ack_ts_ms: Optional[int],
        ack_error: Optional[str],
        preclass_pending_hits: Optional[int],
        preclass_active_hits: Optional[int],
        preclass_unknown_schema: Optional[int],
        preclass_missing_asset: Optional[int],
        preclass_missing_sub: Optional[int],
        confirm_required_updates: Optional[int],
        confirm_counts_by_asset_json: str,
        confirm_preclass_hits_by_asset_json: str,
        first_pending_recv_ts_ms: Optional[int],
        last_pending_recv_ts_ms: Optional[int],
        confirm_wait_ms: Optional[float],
        result: Optional[str],
    ) -> None:
        self.insert(
            "ws_subscribe_attempts",
            {
                "run_id": str(run_id),
                "ts_ms": int(ts_ms),
                "attempt_id": int(attempt_id),
                "action": str(action),
                "active_sub_id_before": _maybe_int(active_sub_id_before),
                "pending_sub_id": _maybe_int(pending_sub_id),
                "asset_ids_json": str(asset_ids_json),
                "payload_json": str(payload_json),
                "ack_status": str(ack_status) if ack_status is not None else None,
                "ack_ts_ms": _maybe_int(ack_ts_ms),
                "ack_error": str(ack_error) if ack_error is not None else None,
                "preclass_pending_hits": _maybe_int(preclass_pending_hits),
                "preclass_active_hits": _maybe_int(preclass_active_hits),
                "preclass_unknown_schema": _maybe_int(preclass_unknown_schema),
                "preclass_missing_asset": _maybe_int(preclass_missing_asset),
                "preclass_missing_sub": _maybe_int(preclass_missing_sub),
                "confirm_required_updates": _maybe_int(confirm_required_updates),
                "confirm_counts_by_asset_json": str(confirm_counts_by_asset_json),
                "confirm_preclass_hits_by_asset_json": str(confirm_preclass_hits_by_asset_json),
                "first_pending_recv_ts_ms": _maybe_int(first_pending_recv_ts_ms),
                "last_pending_recv_ts_ms": _maybe_int(last_pending_recv_ts_ms),
                "confirm_wait_ms": _maybe_float(confirm_wait_ms),
                "result": str(result) if result is not None else None,
            },
        )

    def append_decision_trace(
        self,
        ts_ms: int,
        decision_id: str,
        token_id: str,
        market_slug: Optional[str],
        action: str,
        allow_action: bool,
        input_asof_ts_ms: int,
        gate_reason_codes: Sequence[str],
        payload: Optional[Dict[str, Any]] = None,
    ) -> None:
        self.insert(
            "decision_trace",
            {
                "ts_ms": int(ts_ms),
                "event_id": uuid4_hex(),
                "decision_id": str(decision_id),
                "token_id": str(token_id),
                "market_slug": str(market_slug) if market_slug is not None else None,
                "action": str(action),
                "allow_action": 1 if bool(allow_action) else 0,
                "input_asof_ts_ms": int(input_asof_ts_ms),
                "gate_reason_codes": ",".join(sorted(set(str(code) for code in gate_reason_codes if code))),
                "payload_json": _as_json(payload or {}),
            },
        )

    def mark_fill_event_seen(
        self,
        fill_event_key: str,
        first_seen_ts_ms: int,
        source: str,
        payload: Optional[Dict[str, Any]] = None,
    ) -> bool:
        with self._lock:
            self._flush_batch_locked(force=True)
            try:
                self._cx.execute(
                    """
                    INSERT INTO seen_fill_events (
                        fill_event_key,
                        first_seen_ts_ms,
                        source,
                        payload_json
                    ) VALUES (?, ?, ?, ?)
                    """,
                    (
                        str(fill_event_key),
                        int(first_seen_ts_ms),
                        str(source),
                        _as_json(payload or {}),
                    ),
                )
                self._cx.commit()
                return True
            except sqlite3.IntegrityError:
                return False

    def _init_schema(self) -> None:
        with self._lock:
            for stmt in _SCHEMA:
                self._cx.execute(stmt)
            self._cx.execute("CREATE INDEX IF NOT EXISTS idx_ws_subscribe_attempts_ts ON ws_subscribe_attempts(ts_ms)")
            self._ensure_columns(
                "orders",
                {
                    "run_id": "TEXT",
                    "mode": "TEXT",
                    "market_slug": "TEXT",
                    "condition_id": "TEXT",
                    "reason_code": "TEXT",
                    "quote_group_id": "TEXT",
                    "idempotency_key": "TEXT",
                },
            )
            self._ensure_columns(
                "fills",
                {
                    "run_id": "TEXT",
                    "mode": "TEXT",
                    "market_slug": "TEXT",
                    "condition_id": "TEXT",
                    "reason_code": "TEXT",
                },
            )
            self._ensure_columns(
                "latency_stats",
                {
                    "p50_signal_age_ms": "REAL",
                    "p95_signal_age_ms": "REAL",
                    "p50_ws_lag_ms": "REAL",
                    "p95_ws_lag_ms": "REAL",
                    "p50_pstar_age_ms": "REAL",
                    "p95_pstar_age_ms": "REAL",
                },
            )
            self._ensure_columns(
                "pstar",
                {
                    "pstar_recv_ts_ms": "INTEGER",
                    "invalid_reason": "TEXT",
                },
            )
            self._ensure_columns(
                "reconciliation_stats",
                {
                    "run_id": "TEXT",
                    "mode": "TEXT",
                    "derived_inventory": "REAL",
                    "inventory_delta_qty": "REAL",
                    "inventory_delta_usdc": "REAL",
                    "tolerance_qty": "REAL",
                    "tolerance_usdc": "REAL",
                    "outside_tolerance": "INTEGER",
                    "consecutive_mismatch_cycles": "INTEGER",
                    "consecutive_onchain_disagree_cycles": "INTEGER",
                    "freeze_state": "INTEGER",
                    "freeze_reason": "TEXT",
                },
            )
            self._ensure_columns(
                "recovery_events",
                {
                    "run_id": "TEXT",
                    "mode": "TEXT",
                    "price": "REAL",
                    "size": "REAL",
                },
            )
            self._ensure_columns(
                "discovery_requests",
                {
                    "status": "TEXT",
                    "retry_index": "INTEGER",
                    "next_retry_ts_ms": "INTEGER",
                },
            )
            self._cx.execute(
                "CREATE INDEX IF NOT EXISTS idx_decision_ticks_ts ON decision_ticks(ts_ms)"
            )
            self._cx.execute(
                "CREATE INDEX IF NOT EXISTS idx_book_health_stats_ts_token ON book_health_stats(ts_ms, token_id)"
            )
            self._cx.execute(
                "CREATE INDEX IF NOT EXISTS idx_open_orders_snapshot_ts_token_side ON open_orders_snapshot(ts_ms, token_id, side)"
            )
            self._cx.execute(
                "CREATE INDEX IF NOT EXISTS idx_seen_fill_events_first_seen ON seen_fill_events(first_seen_ts_ms)"
            )
            self._cx.execute(
                "CREATE INDEX IF NOT EXISTS idx_rollover_metrics_name_ts ON rollover_metrics(metric_name, ts_ms)"
            )
            self._cx.execute(
                "CREATE INDEX IF NOT EXISTS idx_execution_quality_ts_token ON execution_quality(ts_ms, token_id)"
            )
            self._cx.execute(
                "CREATE INDEX IF NOT EXISTS idx_execution_quality_stats_ts_token ON execution_quality_stats(ts_ms, token_id)"
            )
            self._cx.execute(
                "CREATE INDEX IF NOT EXISTS idx_queue_quality_stats_ts_token ON queue_quality_stats(ts_ms, token_id)"
            )
            self._cx.execute(
                "CREATE INDEX IF NOT EXISTS idx_liveness_stats_ts ON liveness_stats(ts_ms)"
            )
            self._cx.execute(
                "CREATE INDEX IF NOT EXISTS idx_orders_event_id ON orders(event_id)"
            )
            self._cx.execute(
                "CREATE INDEX IF NOT EXISTS idx_rollover_status_ts ON rollover_status(ts_ms)"
            )
            self._cx.execute(
                "CREATE INDEX IF NOT EXISTS idx_discovery_requests_ts ON discovery_requests(ts_ms)"
            )
            self._cx.execute(
                "CREATE INDEX IF NOT EXISTS idx_decision_trace_ts ON decision_trace(ts_ms)"
            )
            self._cx.execute(
                "CREATE INDEX IF NOT EXISTS idx_evidence_rows_ts ON evidence_rows(ts_ms)"
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


def _maybe_float(value: Optional[float]) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _maybe_int(value: Optional[int]) -> Optional[int]:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def uuid4_hex() -> str:
    import uuid

    return uuid.uuid4().hex
