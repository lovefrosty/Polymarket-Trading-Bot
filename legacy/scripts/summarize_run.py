from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import sqlite3
from typing import Any, Dict, Iterable, List, Optional


def _as_float(value: Any) -> Optional[float]:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _as_int(value: Any) -> Optional[int]:
    try:
        if value is None:
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def _table_exists(cx: sqlite3.Connection, table: str) -> bool:
    row = cx.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    ).fetchone()
    return row is not None


def _quantile(values: Iterable[float], q: float) -> Optional[float]:
    ordered = sorted(float(v) for v in values)
    if not ordered:
        return None
    idx = int(max(0, min(len(ordered) - 1, round((len(ordered) - 1) * q))))
    return float(ordered[idx])


def _event_counts_from_jsonl(run_dir: Optional[Path]) -> Dict[str, int]:
    if run_dir is None or not run_dir.exists():
        return {}
    counts: Counter[str] = Counter()
    for path in sorted(run_dir.glob("*.jsonl")):
        try:
            with path.open("r", encoding="utf-8") as handle:
                for line in handle:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        payload = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if not isinstance(payload, dict):
                        continue
                    channel = str(payload.get("channel") or "unknown")
                    event_type = str(payload.get("event_type") or "unknown")
                    counts[f"{channel}:{event_type}"] += 1
        except OSError:
            continue
    return {key: int(counts[key]) for key in sorted(counts.keys())}


def _event_counts_from_sqlite(cx: sqlite3.Connection) -> Dict[str, int]:
    counts: Counter[str] = Counter()
    if _table_exists(cx, "rollover_status"):
        rows = cx.execute(
            """
            SELECT event_type, COUNT(*)
            FROM rollover_status
            GROUP BY event_type
            ORDER BY event_type
            """
        ).fetchall()
        for event_type, count in rows:
            counts[f"system:{str(event_type)}"] += int(count or 0)
    if _table_exists(cx, "alerts"):
        rows = cx.execute(
            """
            SELECT severity, COUNT(*)
            FROM alerts
            GROUP BY severity
            ORDER BY severity
            """
        ).fetchall()
        for severity, count in rows:
            counts[f"alerts:{str(severity)}"] += int(count or 0)
    return {key: int(counts[key]) for key in sorted(counts.keys())}


def _latency_summary(cx: sqlite3.Connection) -> Dict[str, Optional[float]]:
    if not _table_exists(cx, "latency_stats"):
        return {
            "ws_lag_ms_p50": None,
            "ws_lag_ms_p95": None,
            "signal_age_ms_p50": None,
            "signal_age_ms_p95": None,
        }
    rows = cx.execute(
        """
        SELECT ws_lag_ms, p95_signal_age_ms
        FROM latency_stats
        ORDER BY ts_ms ASC
        """
    ).fetchall()
    ws = [float(value) for value, _ in rows if _as_float(value) is not None]
    signal = [float(value) for _, value in rows if _as_float(value) is not None]
    return {
        "ws_lag_ms_p50": _quantile(ws, 0.5),
        "ws_lag_ms_p95": _quantile(ws, 0.95),
        "signal_age_ms_p50": _quantile(signal, 0.5),
        "signal_age_ms_p95": _quantile(signal, 0.95),
    }


def _rollover_summary(cx: sqlite3.Connection) -> Dict[str, Any]:
    if not _table_exists(cx, "rollover_status"):
        return {"commit_count": 0, "abort_count": 0, "abort_by_reason": {}}
    rows = cx.execute(
        """
        SELECT event_type, payload_json
        FROM rollover_status
        ORDER BY ts_ms ASC
        """
    ).fetchall()
    commit_count = 0
    abort_count = 0
    abort_by_reason: Counter[str] = Counter()
    for event_type, payload_json in rows:
        event = str(event_type or "")
        if event == "COMMIT":
            commit_count += 1
            continue
        if event != "ABORT":
            continue
        abort_count += 1
        payload: Dict[str, Any] = {}
        if isinstance(payload_json, str) and payload_json.strip():
            try:
                parsed = json.loads(payload_json)
                if isinstance(parsed, dict):
                    payload = parsed
            except json.JSONDecodeError:
                payload = {}
        reason = str(payload.get("abort_reason") or "UNKNOWN")
        abort_by_reason[reason] += 1
    return {
        "commit_count": int(commit_count),
        "abort_count": int(abort_count),
        "abort_by_reason": {key: int(abort_by_reason[key]) for key in sorted(abort_by_reason.keys())},
    }


def _none_found_summary(cx: sqlite3.Connection) -> Dict[str, int]:
    if not _table_exists(cx, "discovery_requests"):
        return {"streak_count": 0, "max_streak_duration_ms": 0, "max_retry_index": 0}
    rows = cx.execute(
        """
        SELECT ts_ms, status, retry_index
        FROM discovery_requests
        ORDER BY ts_ms ASC
        """
    ).fetchall()
    streak_count = 0
    max_streak_duration_ms = 0
    max_retry_index = 0
    current_start: Optional[int] = None
    current_last: Optional[int] = None
    for ts_ms, status, retry_index in rows:
        ts = int(_as_int(ts_ms) or 0)
        retry = int(_as_int(retry_index) or 0)
        max_retry_index = max(max_retry_index, retry)
        if str(status or "").upper() == "NONE_FOUND":
            if current_start is None:
                streak_count += 1
                current_start = ts
            current_last = ts
            continue
        if current_start is not None and current_last is not None:
            max_streak_duration_ms = max(max_streak_duration_ms, int(max(0, current_last - current_start)))
        current_start = None
        current_last = None
    if current_start is not None and current_last is not None:
        max_streak_duration_ms = max(max_streak_duration_ms, int(max(0, current_last - current_start)))
    return {
        "streak_count": int(streak_count),
        "max_streak_duration_ms": int(max_streak_duration_ms),
        "max_retry_index": int(max_retry_index),
    }


def _unknown_ignored_summary(cx: sqlite3.Connection) -> Dict[str, Optional[float]]:
    if not _table_exists(cx, "rollover_metrics"):
        return {
            "unknown_rate_per_min_last": None,
            "ignored_old_rate_per_min_last": None,
            "active_rate_per_min_last": None,
        }
    rows = cx.execute(
        """
        SELECT metric_name, metric_value
        FROM rollover_metrics
        WHERE metric_name IN ('unknown_msg_count', 'ignored_old_rate_per_min', 'active_rate_per_min')
        ORDER BY ts_ms DESC
        """
    ).fetchall()
    seen: Dict[str, Optional[float]] = {}
    for metric_name, metric_value in rows:
        key = str(metric_name or "")
        if key in seen:
            continue
        seen[key] = _as_float(metric_value)
    return {
        "unknown_rate_per_min_last": seen.get("unknown_msg_count"),
        "ignored_old_rate_per_min_last": seen.get("ignored_old_rate_per_min"),
        "active_rate_per_min_last": seen.get("active_rate_per_min"),
    }


def _build_run_summary(cx: sqlite3.Connection, run_dir: Optional[Path]) -> Dict[str, Any]:
    event_counts = _event_counts_from_jsonl(run_dir)
    if not event_counts:
        event_counts = _event_counts_from_sqlite(cx)
    return {
        "event_counts": event_counts,
        "latency": _latency_summary(cx),
        "rollover": _rollover_summary(cx),
        "none_found": _none_found_summary(cx),
        "unknown_ignored": _unknown_ignored_summary(cx),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Build deterministic run summary from runtime SQLite and optional JSONL")
    parser.add_argument("--db-path", default="runtime.db")
    parser.add_argument("--run-dir", default=None)
    parser.add_argument("--output", default="run_summary.json")
    args = parser.parse_args()

    db_path = Path(args.db_path)
    run_dir = Path(args.run_dir) if args.run_dir else None
    output = Path(args.output)

    cx = sqlite3.connect(db_path.as_posix())
    try:
        summary = _build_run_summary(cx, run_dir=run_dir)
    finally:
        cx.close()

    output.write_text(json.dumps(summary, separators=(",", ":"), ensure_ascii=True, sort_keys=True), encoding="utf-8")
    print(json.dumps({"output": output.as_posix()}, separators=(",", ":"), ensure_ascii=True, sort_keys=True))


if __name__ == "__main__":
    main()
