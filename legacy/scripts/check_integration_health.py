from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import sqlite3
import sys
import time
from typing import Any, Dict

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from scripts.check_promotion_gates import _load_constitution


def _table_exists(cx: sqlite3.Connection, table: str) -> bool:
    row = cx.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    ).fetchone()
    return row is not None


def _as_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _as_float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _build_report(
    cx: sqlite3.Connection,
    lookback_start_ms: int,
    now_ms: int,
    clock_drift_max_ms: float,
    ws_starvation_max_ms: float,
) -> Dict[str, Any]:
    report: Dict[str, Any] = {
        "window": {"start_ts_ms": int(lookback_start_ms), "end_ts_ms": int(now_ms)},
        "status": "PASS",
        "missing_tables": [],
        "liveness": {},
        "reconciliation": {},
        "subscription_partition": {},
        "rollover": {},
        "unknown_rate_health": {},
        "pstar_state_counts": {},
        "pstar_valid_dwell_pct": 0.0,
        "a_invalid_alert_count": 0,
    }

    required = ["liveness_stats", "reconciliation_stats", "alerts", "rollover_metrics"]
    missing = [name for name in required if not _table_exists(cx, name)]
    report["missing_tables"] = missing

    if "liveness_stats" not in missing:
        rows = cx.execute(
            """
            SELECT freeze_state, clock_drift_ms, sequence_gap_count_1m, ws_starvation_token_count, max_ws_starvation_ms
            FROM liveness_stats
            WHERE ts_ms >= ?
            ORDER BY ts_ms
            """,
            (lookback_start_ms,),
        ).fetchall()
        total = len(rows)
        green = 0
        gap_count_sum = 0
        starvation_incidents = 0
        drift_incidents = 0
        for freeze_state, drift, gap_count, starvation_tokens, starvation_ms in rows:
            gap_count_sum += _as_int(gap_count)
            if _as_int(starvation_tokens) > 0 or _as_float(starvation_ms) > float(ws_starvation_max_ms):
                starvation_incidents += 1
            if _as_float(drift) > float(clock_drift_max_ms):
                drift_incidents += 1
            healthy = (
                _as_int(freeze_state) == 0
                and _as_float(drift) <= float(clock_drift_max_ms)
                and _as_int(gap_count) <= 0
                and _as_float(starvation_ms) <= float(ws_starvation_max_ms)
                and _as_int(starvation_tokens) <= 0
            )
            if healthy:
                green += 1
        green_pct = (100.0 * float(green) / float(total)) if total > 0 else 0.0
        report["liveness"] = {
            "sample_count": int(total),
            "green_pct": round(green_pct, 4),
            "sequence_gap_count": int(gap_count_sum),
            "starvation_incident_count": int(starvation_incidents),
            "clock_drift_incident_count": int(drift_incidents),
        }

    if "reconciliation_stats" not in missing:
        rec = cx.execute(
            """
            SELECT
                COUNT(*) AS n,
                SUM(CASE WHEN outside_tolerance=1 THEN 1 ELSE 0 END) AS outside_tol_rows,
                MAX(unresolved_mismatch_count) AS max_unresolved,
                MAX(CASE WHEN freeze_state=1 THEN 1 ELSE 0 END) AS any_freeze
            FROM reconciliation_stats
            WHERE ts_ms >= ?
            """,
            (lookback_start_ms,),
        ).fetchone()
        report["reconciliation"] = {
            "sample_count": _as_int(rec[0] if rec else 0),
            "outside_tolerance_rows": _as_int(rec[1] if rec else 0),
            "max_unresolved_mismatch_count": _as_int(rec[2] if rec else 0),
            "any_reconciliation_freeze": bool(_as_int(rec[3] if rec else 0)),
        }

    if "alerts" not in missing:
        edges = cx.execute(
            """
            SELECT code, COUNT(*)
            FROM alerts
            WHERE ts_ms >= ?
              AND code IN (
                'RECONCILIATION_FROZEN_EDGE',
                'RECONCILIATION_UNFROZEN_EDGE',
                'LIVENESS_FROZEN_EDGE',
                'LIVENESS_UNFROZEN_EDGE',
                'RECON_UNKNOWN_ORDER_QUARANTINE'
              )
            GROUP BY code
            ORDER BY code
            """,
            (lookback_start_ms,),
        ).fetchall()
        edge_map = {str(code): _as_int(count) for code, count in edges}
    else:
        edge_map = {}

    if "rollover_metrics" not in missing:
        rates = cx.execute(
            """
            SELECT metric_name, metric_value
            FROM rollover_metrics
            WHERE ts_ms >= ?
              AND metric_name IN ('ignored_old_rate_per_min', 'active_rate_per_min', 'unknown_msg_count')
            ORDER BY ts_ms DESC
            LIMIT 200
            """,
            (lookback_start_ms,),
        ).fetchall()
        latest_by_metric: Dict[str, float] = {}
        for metric_name, metric_value in rates:
            key = str(metric_name)
            if key in latest_by_metric:
                continue
            latest_by_metric[key] = _as_float(metric_value)
        active = float(latest_by_metric.get("active_rate_per_min", 0.0))
        ignored = float(latest_by_metric.get("ignored_old_rate_per_min", 0.0))
        partition_ratio = (ignored / max(1.0, active)) if active > 0 else 0.0
        report["subscription_partition"] = {
            "latest_active_rate_per_min": round(active, 6),
            "latest_ignored_old_rate_per_min": round(ignored, 6),
            "ignored_vs_active_ratio": round(partition_ratio, 6),
            "latest_unknown_msg_count": _as_int(latest_by_metric.get("unknown_msg_count", 0.0)),
        }
        unknown_rate = float(latest_by_metric.get("unknown_msg_count", 0.0))
        unknown_vs_active = unknown_rate / max(1.0, active)
        unknown_status = "OK"
        if unknown_vs_active >= 1.0:
            unknown_status = "CRITICAL"
        elif unknown_vs_active >= 0.25:
            unknown_status = "WARN"
        report["unknown_rate_health"] = {
            "status": unknown_status,
            "unknown_rate_per_min": round(unknown_rate, 6),
            "active_rate_per_min": round(active, 6),
            "unknown_vs_active_ratio": round(unknown_vs_active, 6),
        }

    if _table_exists(cx, "rollover_status"):
        rows = cx.execute(
            """
            SELECT event_type, payload_json
            FROM rollover_status
            WHERE ts_ms >= ?
            ORDER BY ts_ms ASC
            """,
            (lookback_start_ms,),
        ).fetchall()
        commit_count = 0
        confirm_timeout_count = 0
        abort_by_reason: Dict[str, int] = {}
        for event_type, payload_json in rows:
            event = str(event_type or "")
            if event == "COMMIT":
                commit_count += 1
                continue
            if event != "ABORT":
                continue
            payload: Dict[str, Any] = {}
            if isinstance(payload_json, str) and payload_json.strip():
                try:
                    parsed = json.loads(payload_json)
                    if isinstance(parsed, dict):
                        payload = parsed
                except json.JSONDecodeError:
                    payload = {}
            reason = str(payload.get("abort_reason") or "UNKNOWN")
            abort_by_reason[reason] = int(abort_by_reason.get(reason, 0)) + 1
            if reason == "CONFIRM_TIMEOUT":
                confirm_timeout_count += 1
        report["rollover"] = {
            "commit_count": int(commit_count),
            "confirm_timeout_count": int(confirm_timeout_count),
            "abort_by_reason": {key: abort_by_reason[key] for key in sorted(abort_by_reason.keys())},
        }

    if _table_exists(cx, "pstar"):
        rows = cx.execute(
            """
            SELECT valid, invalid_reason, diagnostics_json
            FROM pstar
            WHERE ts_ms >= ?
            ORDER BY ts_ms ASC
            """,
            (lookback_start_ms,),
        ).fetchall()
        states: Counter[str] = Counter()
        for valid, invalid_reason, diagnostics_json in rows:
            state = "UNAVAILABLE"
            diag = {}
            if isinstance(diagnostics_json, str) and diagnostics_json.strip():
                try:
                    parsed = json.loads(diagnostics_json)
                    if isinstance(parsed, dict):
                        diag = parsed
                except json.JSONDecodeError:
                    diag = {}
            state_from_diag = str(diag.get("state") or "").strip().upper()
            if state_from_diag:
                state = state_from_diag
            else:
                if _as_int(valid) == 1:
                    state = "VALID"
                else:
                    reason = str(invalid_reason or "").lower()
                    if "stale_source" in reason:
                        state = "STALE"
                    elif "disagreement" in reason:
                        state = "DIVERGED"
                    else:
                        state = "UNAVAILABLE"
            states[state] += 1
        total_states = int(sum(states.values()))
        valid_count = int(states.get("VALID", 0))
        valid_dwell_pct = (100.0 * float(valid_count) / float(total_states)) if total_states > 0 else 0.0
        report["pstar_state_counts"] = {key: int(states[key]) for key in sorted(states.keys())}
        report["pstar_valid_dwell_pct"] = round(valid_dwell_pct, 4)

    if _table_exists(cx, "alerts"):
        row = cx.execute(
            """
            SELECT COUNT(*)
            FROM alerts
            WHERE ts_ms >= ?
              AND (
                    code='A_PSTAR_INVALID'
                 OR message LIKE '%A_PSTAR_INVALID%'
                 OR payload_json LIKE '%A_PSTAR_INVALID%'
              )
            """,
            (lookback_start_ms,),
        ).fetchone()
        report["a_invalid_alert_count"] = _as_int(row[0] if row else 0)

    report["freeze_edges"] = edge_map
    unexplained = 0
    if report.get("reconciliation", {}).get("any_reconciliation_freeze") and _as_int(edge_map.get("RECONCILIATION_FROZEN_EDGE")) <= 0:
        unexplained += 1
    if report.get("liveness", {}).get("sample_count", 0) > 0 and _as_int(edge_map.get("LIVENESS_FROZEN_EDGE")) <= 0:
        # Only mark unexplained if liveness indicates unhealthy/frozen conditions.
        if report.get("liveness", {}).get("green_pct", 100.0) < 100.0:
            unexplained += 1
    report["unexplained_freeze_count"] = int(unexplained)
    if missing or unexplained > 0:
        report["status"] = "FAIL"
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Build deterministic integration health report from runtime.db")
    parser.add_argument("--db-path", default="runtime.db")
    parser.add_argument("--lookback-hours", type=int, default=2)
    parser.add_argument("--constitution", default="config/constitution.yaml")
    args = parser.parse_args()

    constitution = _load_constitution(Path(args.constitution))
    trading = constitution.get("trading", {}) if isinstance(constitution, dict) else {}
    clock_drift_max_ms = float(trading.get("clock_drift_max_ms", 250.0))
    ws_starvation_max_ms = float(trading.get("ws_starvation_max_ms", 5000.0))

    now_ms = int(time.time() * 1000)
    lookback_start_ms = int(now_ms - max(1, int(args.lookback_hours)) * 3_600_000)

    cx = sqlite3.connect(Path(args.db_path).as_posix())
    try:
        report = _build_report(
            cx=cx,
            lookback_start_ms=lookback_start_ms,
            now_ms=now_ms,
            clock_drift_max_ms=clock_drift_max_ms,
            ws_starvation_max_ms=ws_starvation_max_ms,
        )
    finally:
        cx.close()

    print(json.dumps(report, separators=(",", ":"), ensure_ascii=True, sort_keys=True))


if __name__ == "__main__":
    main()
