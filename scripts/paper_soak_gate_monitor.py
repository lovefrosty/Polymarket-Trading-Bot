from __future__ import annotations

import json
import sqlite3
import sys
import time
from pathlib import Path
from typing import Any, Dict, Optional


def compute_soak_gate_status(
    *,
    now_ms: int,
    counts: Dict[str, int],
    ages_ms: Dict[str, Optional[int]],
    reference_ages_ms: Dict[str, Optional[int]],
    last_counts: Optional[Dict[str, int]],
    clean_window_started_ts_ms: Optional[int],
    clean_window_target_ms: int = 6 * 60 * 60 * 1000,
    freshness_threshold_ms: int = 5_000,
    reference_threshold_ms: int = 10_000,
) -> Dict[str, Any]:
    deltas = {
        key: int(counts.get(key, 0)) - int((last_counts or counts).get(key, 0))
        for key in counts
    }
    db_stale = any(
        ages_ms.get(key) is None or int(ages_ms.get(key) or 0) >= int(freshness_threshold_ms)
        for key in ("book_age_ms", "decision_age_ms", "log_age_ms")
    )
    refs_healthy = (
        reference_ages_ms.get("spot") is not None
        and int(reference_ages_ms.get("spot") or 0) < int(reference_threshold_ms)
        and reference_ages_ms.get("perp") is not None
        and int(reference_ages_ms.get("perp") or 0) < int(reference_threshold_ms)
    )
    no_new_failures = int(deltas.get("rollover_abort_discovery_error", 0)) == 0 and int(
        deltas.get("rollover_health_freeze", 0)
    ) == 0
    forward_progress = int(deltas.get("orders", 0)) > 0 and int(deltas.get("quote", 0)) > 0
    status: Dict[str, Any] = {
        "ts_ms": int(now_ms),
        "status": "not_clean",
        "commit_blocked": True,
        "blocking_reason": None,
        "clean_window_started_ts_ms": clean_window_started_ts_ms,
        "clean_window_elapsed_ms": None,
        "clean_window_target_ms": int(clean_window_target_ms),
        "counts": {k: int(v) for k, v in counts.items()},
        "deltas_since_last_sample": deltas,
        "ages_ms": dict(ages_ms),
        "reference_ages_ms": dict(reference_ages_ms),
    }
    if db_stale:
        status["status"] = "failed_stale_runtime"
        status["blocking_reason"] = "DB_TRUTH_STALE_OVERRIDES_MONITOR"
        status["clean_window_started_ts_ms"] = None
        return status
    if refs_healthy and no_new_failures and forward_progress:
        started = int(clean_window_started_ts_ms or now_ms)
        elapsed = int(now_ms) - started
        status["clean_window_started_ts_ms"] = started
        status["clean_window_elapsed_ms"] = elapsed
        if elapsed >= int(clean_window_target_ms):
            status["status"] = "clean_window_met"
            status["commit_blocked"] = False
        else:
            status["status"] = "clean_window_active"
            status["blocking_reason"] = "WAITING_FOR_6H_CLEAN_WINDOW"
        return status
    reasons = []
    if not refs_healthy:
        reasons.append("REFERENCE_BROKEN")
    if not no_new_failures:
        reasons.append("NEW_ROLLOVER_FAILURES")
    if not forward_progress:
        reasons.append("NO_ORDER_FLOW_PROGRESS")
    status["blocking_reason"] = ",".join(reasons) or "UNKNOWN"
    status["clean_window_started_ts_ms"] = None
    return status


def _scalar(cur: sqlite3.Cursor, q: str) -> Optional[int]:
    row = cur.execute(q).fetchone()
    return row[0] if row else None


def _latest_ref_ages_ms(logs_dir: Path) -> Dict[str, Optional[int]]:
    files = sorted(logs_dir.glob("reference_*.jsonl"))
    now = int(time.time() * 1000)
    if not files:
        return {"spot": None, "perp": None}
    last: Dict[str, int] = {}
    with files[-1].open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except Exception:
                continue
            raw = row.get("raw", {}) if isinstance(row, dict) else {}
            src = raw.get("source") or row.get("source")
            ts = row.get("t_event_ms") or raw.get("t_event_ms")
            if src in ("spot", "perp") and isinstance(ts, int):
                last[str(src)] = int(ts)
    return {k: (None if k not in last else now - last[k]) for k in ("spot", "perp")}


def _load_state(path: Path) -> Dict[str, Any]:
    if path.exists():
        try:
            return json.loads(path.read_text())
        except Exception:
            pass
    return {"clean_window_started_ts_ms": None, "last_counts": None}


def _write_status_md(path: Path, status: Dict[str, Any]) -> None:
    ages = status.get("ages_ms", {})
    ref_ages = status.get("reference_ages_ms", {})
    counts = status.get("counts", {})
    deltas = status.get("deltas_since_last_sample", {})
    lines = [
        "# PAPER Soak Gate",
        "",
        f"- status: `{status.get('status')}`",
        f"- commit_blocked: `{status.get('commit_blocked')}`",
        f"- blocking_reason: `{status.get('blocking_reason')}`",
        f"- clean_window_elapsed_ms: `{status.get('clean_window_elapsed_ms')}`",
        f"- book_age_ms: `{ages.get('book_age_ms')}`",
        f"- decision_age_ms: `{ages.get('decision_age_ms')}`",
        f"- log_age_ms: `{ages.get('log_age_ms')}`",
        f"- spot_age_ms: `{ref_ages.get('spot')}`",
        f"- perp_age_ms: `{ref_ages.get('perp')}`",
    ]
    for key in (
        "orders",
        "fills",
        "quote",
        "skip",
        "freeze",
        "rollover_intent",
        "rollover_commit",
        "rollover_abort_discovery_error",
        "rollover_health_freeze",
    ):
        lines.append(f"- {key}: `{counts.get(key)}` (delta `{deltas.get(key)}`)")
    path.write_text("\n".join(lines) + "\n")


def main(argv: list[str]) -> int:
    root = Path(argv[1]).expanduser().resolve() if len(argv) > 1 else Path.cwd()
    sample_secs = int(argv[2]) if len(argv) > 2 else 300
    db_path = root / "runtime.db"
    logs_dir = root / "logs"
    meta_dir = root / "meta"
    meta_dir.mkdir(parents=True, exist_ok=True)
    status_json = meta_dir / "soak_gate_status.json"
    status_md = meta_dir / "soak_gate_status.md"
    state_json = meta_dir / "soak_gate_state.json"
    while True:
        now_ms = int(time.time() * 1000)
        state = _load_state(state_json)
        try:
            con = sqlite3.connect(str(db_path))
            cur = con.cursor()
            counts = {
                "orders": int(_scalar(cur, "select count(*) from orders") or 0),
                "fills": int(_scalar(cur, "select count(*) from fills") or 0),
                "quote": int(_scalar(cur, "select count(*) from decisions where action='QUOTE'") or 0),
                "skip": int(_scalar(cur, "select count(*) from decisions where action='SKIP'") or 0),
                "freeze": int(_scalar(cur, "select count(*) from decisions where action='FREEZE'") or 0),
                "rollover_intent": int(_scalar(cur, "select count(*) from logs where msg='rollover_intent'") or 0),
                "rollover_commit": int(_scalar(cur, "select count(*) from logs where msg='rollover_commit'") or 0),
                "rollover_abort_discovery_error": int(_scalar(cur, "select count(*) from logs where msg='rollover_abort_discovery_error'") or 0),
                "rollover_health_freeze": int(_scalar(cur, "select count(*) from logs where msg='rollover_health_freeze'") or 0),
            }
            ages_ms = {
                "book_age_ms": _scalar(cur, "select cast((strftime('%s','now')*1000 - max(ts_ms)) as integer) from market_data_book"),
                "decision_age_ms": _scalar(cur, "select cast((strftime('%s','now')*1000 - max(ts_ms)) as integer) from decisions"),
                "log_age_ms": _scalar(cur, "select cast((strftime('%s','now')*1000 - max(ts_ms)) as integer) from logs"),
            }
            con.close()
            status = compute_soak_gate_status(
                now_ms=now_ms,
                counts=counts,
                ages_ms=ages_ms,
                reference_ages_ms=_latest_ref_ages_ms(logs_dir),
                last_counts=state.get("last_counts"),
                clean_window_started_ts_ms=state.get("clean_window_started_ts_ms"),
            )
            state["last_counts"] = status["counts"]
            state["clean_window_started_ts_ms"] = status.get("clean_window_started_ts_ms")
            status_json.write_text(json.dumps({**status, "run_root": str(root)}, indent=2, sort_keys=True))
            _write_status_md(status_md, status)
            state_json.write_text(json.dumps(state, indent=2, sort_keys=True))
        except Exception as exc:
            error_status = {
                "ts_ms": now_ms,
                "run_root": str(root),
                "status": "monitor_error",
                "commit_blocked": True,
                "blocking_reason": str(exc),
            }
            status_json.write_text(json.dumps(error_status, indent=2, sort_keys=True))
            status_md.write_text(f"# PAPER Soak Gate\n\n- status: `monitor_error`\n- error: `{exc}`\n")
        time.sleep(sample_secs)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
