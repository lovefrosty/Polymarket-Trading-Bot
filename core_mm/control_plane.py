from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import sqlite3
import time
import uuid
from typing import Any, Dict, List, Optional, Sequence


PAPER_CONFIG_PATCH_FIELDS = {
    "quote_spread_multiplier",
    "cycle_secs",
    "refresh_market_secs",
    "trade_size",
    "max_size",
    "min_size",
    "fallback_size",
    "min_order_size",
    "within_pct",
    "market_dwell_secs",
    "post_fill_reentry_cooldown_secs",
    "safe_risk_profile",
    "strategy_allocated_equity",
    "use_allocated_equity_for_risk",
    "risk_based_share_sizing",
    "hard_position_cap",
    "per_trade_loss_pct",
    "per_event_loss_pct",
    "per_day_loss_pct",
    "max_order_notional_pct",
    "max_market_exposure_pct",
    "max_event_exposure_pct",
    "stale_duration_scale",
    "maker_exit_grace_secs",
    "cross_escalation_drawdown_pct",
    "stop_open_before_expiry_secs",
    "force_flat_before_expiry_secs",
    "reentry_cooldown_scale",
    "pre_kill_warning_fraction",
    "skew_threshold_fraction",
    "hedge_threshold_fraction",
    "hedge_requires_stale_inventory",
    "hedge_quality_must_beat_inventory_market",
    "hedge_min_quality_score",
    "hedge_max_temp_gross_increase_fraction",
    "hedge_failure_cooldown_scale",
    "hedge_covariance_enabled",
    "hedge_covariance_window_secs",
    "hedge_covariance_min_samples",
    "hedge_covariance_min_correlation",
    "hedge_covariance_min_abs_beta",
    "hedge_covariance_beta_clip",
    "hedge_covariance_beta_shrinkage",
    "hedge_covariance_max_sample_age_ms",
    "hedge_covariance_max_update_gap_ms",
    "hedge_covariance_boundary_buffer",
    "hedge_covariance_boundary_max_fraction",
    "hedge_covariance_strong_correlation",
    "hedge_covariance_strong_min_samples",
    "hedge_covariance_stability_ratio_max",
    "hedge_covariance_gate_required",
    "observe_pause_interval_secs",
    "observe_pause_duration_secs",
    "negative_pnl_reduce_only_enabled",
    "negative_pnl_unwind_requires_worsening",
    "negative_pnl_unwind_requires_stale_or_worsening",
}

LIVE_ALLOWED_COMMANDS = {
    "pause_trading",
    "resume_trading",
    "cancel_all_quotes",
    "kill_switch_on",
}

PAPER_ALLOWED_COMMANDS = LIVE_ALLOWED_COMMANDS | {
    "kill_switch_off",
    "apply_config_patch",
    "restart_paper_run_safe_profile",
    "flatten_event_inventory",
    "flatten_market_inventory",
}


@dataclass(frozen=True)
class ControlCommand:
    command_id: str
    run_id: str
    runtime_root: str
    scope: str
    command_type: str
    payload: Dict[str, Any]
    requested_by: str
    requested_at_ms: int
    status: str
    expires_at_ms: Optional[int]
    result: Dict[str, Any]


def now_ms() -> int:
    return int(time.time() * 1000)


def allowed_commands_for_mode(mode: str) -> set[str]:
    if str(mode or "").upper() == "LIVE":
        return set(LIVE_ALLOWED_COMMANDS)
    return set(PAPER_ALLOWED_COMMANDS)


def validate_config_patch(mode: str, payload: Dict[str, Any]) -> List[str]:
    errors: List[str] = []
    if str(mode or "").upper() == "LIVE":
        return ["config_patch_not_allowed_in_live_v1"]
    patch = payload if isinstance(payload, dict) else {}
    for key in patch.keys():
        if str(key) not in PAPER_CONFIG_PATCH_FIELDS:
            errors.append(f"field_not_allowed:{key}")
    return errors


def validate_command(mode: str, command_type: str, payload: Dict[str, Any]) -> List[str]:
    errors: List[str] = []
    allowed = allowed_commands_for_mode(mode)
    command_text = str(command_type or "")
    if command_text not in allowed:
        errors.append(f"command_not_allowed:{command_text}")
    if command_text == "apply_config_patch":
        errors.extend(validate_config_patch(mode, payload))
    if command_text in {"flatten_event_inventory", "flatten_market_inventory"}:
        if command_text == "flatten_event_inventory" and not str((payload or {}).get("event_id") or ""):
            errors.append("missing_event_id")
        if command_text == "flatten_market_inventory" and not str((payload or {}).get("market_id") or ""):
            errors.append("missing_market_id")
    return errors


class ControlCommandStore:
    def __init__(self, db_path: Path | str) -> None:
        self.db_path = Path(db_path)
        self.ensure_schema()

    def _connect(self) -> sqlite3.Connection:
        cx = sqlite3.connect(self.db_path.as_posix())
        cx.execute("PRAGMA journal_mode=WAL")
        cx.execute("PRAGMA synchronous=NORMAL")
        cx.execute("PRAGMA busy_timeout=5000")
        return cx

    def ensure_schema(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        cx = self._connect()
        try:
            with cx:
                cx.execute(
                    """
                    CREATE TABLE IF NOT EXISTS control_commands (
                        command_id TEXT PRIMARY KEY,
                        run_id TEXT NOT NULL,
                        runtime_root TEXT NOT NULL,
                        scope TEXT NOT NULL,
                        command_type TEXT NOT NULL,
                        payload_json TEXT NOT NULL,
                        requested_by TEXT NOT NULL,
                        requested_at_ms INTEGER NOT NULL,
                        status TEXT NOT NULL,
                        expires_at_ms INTEGER,
                        result_json TEXT
                    )
                    """
                )
                cx.execute(
                    """
                    CREATE TABLE IF NOT EXISTS control_events (
                        event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                        command_id TEXT NOT NULL,
                        ts_ms INTEGER NOT NULL,
                        event_type TEXT NOT NULL,
                        status TEXT NOT NULL,
                        payload_json TEXT NOT NULL
                    )
                    """
                )
        finally:
            cx.close()

    def submit_command(
        self,
        *,
        run_id: str,
        runtime_root: str,
        scope: str,
        command_type: str,
        payload: Optional[Dict[str, Any]] = None,
        requested_by: str = "dashboard",
        requested_at_ms: Optional[int] = None,
        expires_in_ms: int = 120_000,
    ) -> str:
        command_id = f"cmd-{uuid.uuid4().hex[:12]}"
        ts_ms = int(requested_at_ms if requested_at_ms is not None else now_ms())
        expires_at_ms = ts_ms + max(1_000, int(expires_in_ms)) if expires_in_ms > 0 else None
        row = (
            command_id,
            str(run_id or ""),
            str(runtime_root or ""),
            str(scope or "global"),
            str(command_type or ""),
            json.dumps(payload or {}, sort_keys=True),
            str(requested_by or "dashboard"),
            ts_ms,
            "pending",
            expires_at_ms,
            json.dumps({}, sort_keys=True),
        )
        cx = self._connect()
        try:
            with cx:
                cx.execute(
                    """
                    INSERT INTO control_commands (
                        command_id, run_id, runtime_root, scope, command_type, payload_json,
                        requested_by, requested_at_ms, status, expires_at_ms, result_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    row,
                )
                cx.execute(
                    """
                    INSERT INTO control_events (command_id, ts_ms, event_type, status, payload_json)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (command_id, ts_ms, "requested", "pending", json.dumps(payload or {}, sort_keys=True)),
                )
        finally:
            cx.close()
        return command_id

    def expire_stale_commands(self, *, runtime_root: str, active_run_id: str, ts_ms: Optional[int] = None) -> int:
        active_ts_ms = int(ts_ms if ts_ms is not None else now_ms())
        cx = self._connect()
        try:
            rows = cx.execute(
                """
                SELECT command_id
                FROM control_commands
                WHERE status = 'pending'
                  AND runtime_root = ?
                  AND run_id = ?
                  AND expires_at_ms IS NOT NULL
                  AND expires_at_ms < ?
                """,
                (str(runtime_root), str(active_run_id), active_ts_ms),
            ).fetchall()
            with cx:
                for (command_id,) in rows:
                    result = {"reason": "expired_before_apply"}
                    cx.execute(
                        "UPDATE control_commands SET status = ?, result_json = ? WHERE command_id = ?",
                        ("expired", json.dumps(result, sort_keys=True), str(command_id)),
                    )
                    cx.execute(
                        """
                        INSERT INTO control_events (command_id, ts_ms, event_type, status, payload_json)
                        VALUES (?, ?, ?, ?, ?)
                        """,
                        (str(command_id), active_ts_ms, "expired", "expired", json.dumps(result, sort_keys=True)),
                    )
            return len(rows)
        finally:
            cx.close()

    def fetch_pending_commands(
        self,
        *,
        runtime_root: str,
        active_run_id: str,
        limit: int = 20,
    ) -> List[ControlCommand]:
        cx = self._connect()
        try:
            rows = cx.execute(
                """
                SELECT command_id, run_id, runtime_root, scope, command_type, payload_json,
                       requested_by, requested_at_ms, status, expires_at_ms, result_json
                FROM control_commands
                WHERE status = 'pending'
                  AND runtime_root = ?
                  AND run_id = ?
                ORDER BY requested_at_ms ASC
                LIMIT ?
                """,
                (str(runtime_root), str(active_run_id), int(limit)),
            ).fetchall()
        finally:
            cx.close()
        return [_row_to_command(row) for row in rows]

    def mark_command(
        self,
        *,
        command_id: str,
        status: str,
        event_type: str,
        result: Optional[Dict[str, Any]] = None,
        ts_ms: Optional[int] = None,
    ) -> None:
        active_ts_ms = int(ts_ms if ts_ms is not None else now_ms())
        result_json = json.dumps(result or {}, sort_keys=True)
        cx = self._connect()
        try:
            with cx:
                cx.execute(
                    "UPDATE control_commands SET status = ?, result_json = ? WHERE command_id = ?",
                    (str(status), result_json, str(command_id)),
                )
                cx.execute(
                    """
                    INSERT INTO control_events (command_id, ts_ms, event_type, status, payload_json)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (str(command_id), active_ts_ms, str(event_type), str(status), result_json),
                )
        finally:
            cx.close()

    def list_commands(
        self,
        *,
        runtime_root: Optional[str] = None,
        limit: int = 50,
        statuses: Optional[Sequence[str]] = None,
    ) -> List[ControlCommand]:
        where: List[str] = []
        params: List[Any] = []
        if runtime_root is not None:
            where.append("runtime_root = ?")
            params.append(str(runtime_root))
        if statuses:
            where.append("status IN (%s)" % ", ".join("?" for _ in statuses))
            params.extend([str(item) for item in statuses])
        where_sql = ("WHERE " + " AND ".join(where)) if where else ""
        sql = f"""
            SELECT command_id, run_id, runtime_root, scope, command_type, payload_json,
                   requested_by, requested_at_ms, status, expires_at_ms, result_json
            FROM control_commands
            {where_sql}
            ORDER BY requested_at_ms DESC
            LIMIT ?
        """
        params.append(int(limit))
        cx = self._connect()
        try:
            rows = cx.execute(sql, params).fetchall()
        finally:
            cx.close()
        return [_row_to_command(row) for row in rows]


def _row_to_command(row: Sequence[Any]) -> ControlCommand:
    return ControlCommand(
        command_id=str(row[0] or ""),
        run_id=str(row[1] or ""),
        runtime_root=str(row[2] or ""),
        scope=str(row[3] or ""),
        command_type=str(row[4] or ""),
        payload=_safe_json(row[5]),
        requested_by=str(row[6] or ""),
        requested_at_ms=int(row[7] or 0),
        status=str(row[8] or ""),
        expires_at_ms=int(row[9]) if row[9] is not None else None,
        result=_safe_json(row[10]),
    )


def _safe_json(raw: Any) -> Dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if raw is None:
        return {}
    try:
        payload = json.loads(str(raw))
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}
