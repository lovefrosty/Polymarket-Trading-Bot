from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import sys
import time
from typing import Any, Dict, Iterable, List, Optional, Tuple

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from config.settings import load_settings


def _load_constitution(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    raw = path.read_text(encoding="utf-8")
    try:
        import yaml  # type: ignore

        parsed = yaml.safe_load(raw)
        if isinstance(parsed, dict):
            return parsed
    except Exception:
        pass
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else {}
    except json.JSONDecodeError:
        return {}


def _quantile(values: List[float], q: float) -> Optional[float]:
    if not values:
        return None
    ordered = sorted(values)
    idx = int(max(0, min(len(ordered) - 1, round((len(ordered) - 1) * q))))
    return float(ordered[idx])


def _window_start(ts_ms: int) -> int:
    return int(ts_ms // 3_600_000) * 3_600_000


def _rolling_hour_groups(rows: Iterable[Tuple[int, Any]], value_idx: int = 1) -> Dict[int, List[float]]:
    groups: Dict[int, List[float]] = {}
    for row in rows:
        ts_ms = int(row[0])
        val = row[value_idx]
        if val is None:
            continue
        try:
            fv = float(val)
        except (TypeError, ValueError):
            continue
        bucket = _window_start(ts_ms)
        groups.setdefault(bucket, []).append(fv)
    return groups


def _has_column(cx: sqlite3.Connection, table: str, column: str) -> bool:
    cols = cx.execute(f"PRAGMA table_info({table})").fetchall()
    return any(str(row[1]) == column for row in cols)


def _has_table(cx: sqlite3.Connection, table: str) -> bool:
    row = cx.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1",
        [table],
    ).fetchone()
    return bool(row)


def _iso_window(bucket_ms: int) -> str:
    dt = datetime.fromtimestamp(bucket_ms / 1000.0, tz=timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:00:00Z")


def _round(value: Optional[float]) -> Optional[float]:
    if value is None:
        return None
    return round(float(value), 6)


def _gate_entry(code: str, metric: str, threshold: Any, observed: Any, window: str) -> Dict[str, Any]:
    return {
        "code": str(code),
        "metric": str(metric),
        "threshold": threshold,
        "observed": observed,
        "window": str(window),
    }


def _missing_table_gate(table: str, impacts: List[str], window: str) -> Dict[str, Any]:
    return _gate_entry(
        code="MISSING_TABLE",
        metric=f"table:{table}",
        threshold="exists",
        observed={"table": str(table), "impacts": sorted(set(str(item) for item in impacts))},
        window=window,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Check OBSERVE->PAPER promotion gates")
    parser.add_argument("--db-path", default="runtime.db")
    parser.add_argument("--lookback-hours", type=int, default=48)
    parser.add_argument("--constitution", default="config/constitution.yaml")
    args = parser.parse_args()

    settings = load_settings()
    constitution = _load_constitution(Path(args.constitution))
    policy = constitution.get("policy", {}) if isinstance(constitution, dict) else {}

    ws_lag_max_ms = float(policy.get("ws_lag_max_ms", settings.ws_lag_max_ms))
    pstar_max_age_ms = int(policy.get("pstar_max_age_ms", settings.pstar_max_age_ms))
    signal_age_max_ms = int(policy.get("signal_age_max_ms", settings.signal_age_max_ms))
    now_ms = int(time.time() * 1000)
    lookback_start_ms = now_ms - int(args.lookback_hours) * 3_600_000

    db_path = Path(args.db_path)
    if not db_path.exists():
        print(json.dumps({"status": "FAIL", "failed_gates": [_gate_entry("DB_MISSING", "db_path", "exists", str(db_path), "global")]}, sort_keys=True, separators=(",", ":")))
        raise SystemExit(1)

    failed: List[Dict[str, Any]] = []
    with sqlite3.connect(db_path.as_posix()) as cx:
        has_decision_ticks = _has_table(cx, "decision_ticks")
        has_alerts = _has_table(cx, "alerts")
        has_latency_stats = _has_table(cx, "latency_stats")
        has_book_health_stats = _has_table(cx, "book_health_stats")
        has_reconciliation_stats = _has_table(cx, "reconciliation_stats")

        missing_table_impacts = {
            "decision_ticks": ["B_CAUSALITY_ZERO", "A_PSTAR_VALID_RATIO", "E_WS_LAG_P95", "E_PSTAR_AGE_P95", "E_SIGNAL_AGE_P95"],
            "alerts": ["DB_WRITE_FAILURES", "R_RECON_CRITICAL_ALERTS_ZERO", "R_RECON_FROZEN_EDGE_ZERO"],
            "latency_stats": ["DASHBOARD_FRESHNESS"],
            "book_health_stats": ["DASHBOARD_FRESHNESS"],
            "reconciliation_stats": ["R_RECON_STATS_PRESENT", "R_RECON_UNRESOLVED_ZERO", "R_RECON_FREEZE_ZERO"],
        }
        for table, impacts in sorted(missing_table_impacts.items()):
            if _has_table(cx, table):
                continue
            failed.append(
                _missing_table_gate(
                    table=table,
                    impacts=impacts,
                    window=f"last_{int(args.lookback_hours)}h",
                )
            )

        # B: strict causality zero violations.
        b_count = 0
        if has_decision_ticks:
            b_violations = cx.execute(
                """
                SELECT COUNT(*)
                FROM decision_ticks
                WHERE ts_ms >= ?
                  AND (
                    max_feature_ts_ms >= decision_ts_ms
                    OR (book_asof_ts_ms IS NOT NULL AND book_asof_ts_ms >= decision_ts_ms)
                    OR (pstar_asof_ts_ms IS NOT NULL AND pstar_asof_ts_ms >= decision_ts_ms)
                  )
                """,
                [lookback_start_ms],
            ).fetchone()
            b_count = int(b_violations[0] if b_violations else 0)
            if b_count > 0:
                failed.append(_gate_entry("B_CAUSALITY_ZERO", "causality_violations", 0, b_count, f"last_{int(args.lookback_hours)}h"))

        # A: pstar_valid ratio rolling hourly.
        if has_decision_ticks:
            has_maintenance = _has_column(cx, "decision_ticks", "maintenance_flag")
            maint_filter = "AND (maintenance_flag IS NULL OR maintenance_flag = 0)" if has_maintenance else ""
            pstar_rows = cx.execute(
                f"""
                SELECT ts_ms, pstar_valid
                FROM decision_ticks
                WHERE ts_ms >= ?
                {maint_filter}
                ORDER BY ts_ms
                """,
                [lookback_start_ms],
            ).fetchall()
            pstar_groups = _rolling_hour_groups(pstar_rows)
            if not pstar_groups:
                failed.append(_gate_entry("A_PSTAR_VALID_RATIO", "pstar_valid_ratio", ">=0.98", None, f"last_{int(args.lookback_hours)}h"))
            else:
                for bucket in sorted(pstar_groups.keys()):
                    vals = pstar_groups[bucket]
                    if not vals:
                        continue
                    valid_ratio = sum(1.0 for v in vals if int(v) == 1) / float(len(vals))
                    if valid_ratio < 0.98:
                        failed.append(
                            _gate_entry(
                                "A_PSTAR_VALID_RATIO",
                                "pstar_valid_ratio",
                                ">=0.98",
                                _round(valid_ratio),
                                _iso_window(bucket),
                            )
                        )

        # E: p95 by rolling hourly windows.
        if has_decision_ticks:
            latency_rows = cx.execute(
                """
                SELECT ts_ms, ws_lag_ms, pstar_age_ms, signal_age_ms
                FROM decision_ticks
                WHERE ts_ms >= ?
                ORDER BY ts_ms
                """,
                [lookback_start_ms],
            ).fetchall()
            ws_groups: Dict[int, List[float]] = {}
            pstar_age_groups: Dict[int, List[float]] = {}
            signal_age_groups: Dict[int, List[float]] = {}
            for ts_ms, ws_lag, p_age, s_age in latency_rows:
                bucket = _window_start(int(ts_ms))
                if ws_lag is not None:
                    ws_groups.setdefault(bucket, []).append(float(ws_lag))
                if p_age is not None:
                    pstar_age_groups.setdefault(bucket, []).append(float(p_age))
                if s_age is not None:
                    signal_age_groups.setdefault(bucket, []).append(float(s_age))

            for bucket in sorted(ws_groups.keys()):
                p95 = _quantile(ws_groups[bucket], 0.95)
                if p95 is not None and p95 > ws_lag_max_ms:
                    failed.append(_gate_entry("E_WS_LAG_P95", "ws_lag_ms_p95", ws_lag_max_ms, _round(p95), _iso_window(bucket)))
            for bucket in sorted(pstar_age_groups.keys()):
                p95 = _quantile(pstar_age_groups[bucket], 0.95)
                if p95 is not None and p95 > float(pstar_max_age_ms):
                    failed.append(_gate_entry("E_PSTAR_AGE_P95", "pstar_age_ms_p95", pstar_max_age_ms, _round(p95), _iso_window(bucket)))
            for bucket in sorted(signal_age_groups.keys()):
                p95 = _quantile(signal_age_groups[bucket], 0.95)
                if p95 is not None and p95 > float(signal_age_max_ms):
                    failed.append(_gate_entry("E_SIGNAL_AGE_P95", "signal_age_ms_p95", signal_age_max_ms, _round(p95), _iso_window(bucket)))

        # DB write failures if tracked.
        if has_alerts:
            tracked_failures = cx.execute(
                """
                SELECT COUNT(*)
                FROM alerts
                WHERE ts_ms >= ?
                  AND (code LIKE '%SQLITE_WRITE_FAIL%' OR code LIKE '%DB_WRITE_FAIL%')
                """,
                [lookback_start_ms],
            ).fetchone()
            db_fail_count = int(tracked_failures[0] if tracked_failures else 0)
            if db_fail_count > 0:
                failed.append(_gate_entry("DB_WRITE_FAILURES", "db_write_failure_count", 0, db_fail_count, f"last_{int(args.lookback_hours)}h"))

        # Dashboard freshness <=2s when telemetry exists.
        latest_tick = (
            cx.execute("SELECT MAX(ts_ms) FROM decision_ticks WHERE ts_ms >= ?", [lookback_start_ms]).fetchone()
            if has_decision_ticks
            else (None,)
        )
        latest_stats = (
            cx.execute("SELECT MAX(ts_ms) FROM latency_stats WHERE ts_ms >= ?", [lookback_start_ms]).fetchone()
            if has_latency_stats
            else (None,)
        )
        latest_book = (
            cx.execute("SELECT MAX(ts_ms) FROM book_health_stats WHERE ts_ms >= ?", [lookback_start_ms]).fetchone()
            if has_book_health_stats
            else (None,)
        )
        latest_values = [row[0] for row in [latest_tick, latest_stats, latest_book] if row and row[0] is not None]
        if latest_values:
            latest_ms = int(max(int(v) for v in latest_values))
            freshness_ms = int(max(0, now_ms - latest_ms))
            if freshness_ms > 2000:
                failed.append(_gate_entry("DASHBOARD_FRESHNESS", "freshness_ms", "<=2000", freshness_ms, "latest"))

        # Reconciliation readiness gates.
        if not has_reconciliation_stats:
            failed.append(
                _gate_entry(
                    "R_RECON_STATS_PRESENT",
                    "reconciliation_stats_table",
                    "exists",
                    "missing",
                    f"last_{int(args.lookback_hours)}h",
                )
            )
        else:
            recon_rows = cx.execute(
                """
                SELECT ts_ms, unresolved_mismatch_count, freeze_state
                FROM reconciliation_stats
                WHERE ts_ms >= ?
                ORDER BY ts_ms
                """,
                [lookback_start_ms],
            ).fetchall()
            if not recon_rows:
                failed.append(
                    _gate_entry(
                        "R_RECON_STATS_PRESENT",
                        "reconciliation_samples",
                        ">0",
                        0,
                        f"last_{int(args.lookback_hours)}h",
                    )
                )
            else:
                unresolved_by_bucket: Dict[int, List[float]] = {}
                freeze_count = 0
                for ts_ms, unresolved, freeze_state in recon_rows:
                    bucket = _window_start(int(ts_ms))
                    unresolved_by_bucket.setdefault(bucket, []).append(float(unresolved or 0))
                    freeze_count += 1 if int(freeze_state or 0) == 1 else 0
                for bucket in sorted(unresolved_by_bucket.keys()):
                    observed = max(unresolved_by_bucket[bucket]) if unresolved_by_bucket[bucket] else 0.0
                    if observed > 0.0:
                        failed.append(
                            _gate_entry(
                                "R_RECON_UNRESOLVED_ZERO",
                                "unresolved_mismatch_count_max",
                                0,
                                _round(observed),
                                _iso_window(bucket),
                            )
                        )
                if freeze_count > 0:
                    failed.append(
                        _gate_entry(
                            "R_RECON_FREEZE_ZERO",
                            "reconciliation_freeze_count",
                            0,
                            int(freeze_count),
                            f"last_{int(args.lookback_hours)}h",
                        )
                    )

        if has_alerts:
            frozen_edge = cx.execute(
                """
                SELECT COUNT(*)
                FROM alerts
                WHERE ts_ms >= ?
                  AND code = 'RECONCILIATION_FROZEN_EDGE'
                """,
                [lookback_start_ms],
            ).fetchone()
            frozen_edge_count = int(frozen_edge[0] if frozen_edge else 0)
            if frozen_edge_count > 0:
                failed.append(
                    _gate_entry(
                        "R_RECON_FROZEN_EDGE_ZERO",
                        "reconciliation_frozen_edge_count",
                        0,
                        frozen_edge_count,
                        f"last_{int(args.lookback_hours)}h",
                    )
                )
            recon_critical = cx.execute(
                """
                SELECT COUNT(*)
                FROM alerts
                WHERE ts_ms >= ?
                  AND code = 'RECONCILIATION_MISMATCH_CRITICAL'
                """,
                [lookback_start_ms],
            ).fetchone()
            recon_critical_count = int(recon_critical[0] if recon_critical else 0)
            if recon_critical_count > 0:
                failed.append(
                    _gate_entry(
                        "R_RECON_CRITICAL_ALERTS_ZERO",
                        "reconciliation_critical_alerts",
                        0,
                        recon_critical_count,
                        f"last_{int(args.lookback_hours)}h",
                    )
                )

    result = {
        "status": "PASS" if not failed else "FAIL",
        "failed_gates": failed,
    }
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    raise SystemExit(0 if not failed else 1)


if __name__ == "__main__":
    main()
