from __future__ import annotations

import argparse
from dataclasses import asdict
import json
from pathlib import Path
import sys
import time
from typing import Any, Dict, List

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core_mm.overnight_protocol import (
    OvernightProtocolConfig,
    OvernightProtocolState,
    build_linear_comment_payload,
    build_protocol_observations,
    decide_actions,
    next_state,
    record_actions,
)
from dashboard import data_access as da
from scripts.report_overnight_supervisor import build_report


def _load_state(path: Path) -> OvernightProtocolState:
    if not path.exists():
        return OvernightProtocolState()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return OvernightProtocolState()
    if not isinstance(payload, dict):
        return OvernightProtocolState()
    return OvernightProtocolState(
        consecutive_non_quoteable=int(payload.get("consecutive_non_quoteable") or 0),
        consecutive_pending_backlog=int(payload.get("consecutive_pending_backlog") or 0),
        consecutive_healthy_paused=int(payload.get("consecutive_healthy_paused") or 0),
        last_action_ts_ms={str(k): int(v) for k, v in dict(payload.get("last_action_ts_ms") or {}).items()},
    )


def _write_state(path: Path, state: OvernightProtocolState) -> None:
    path.write_text(json.dumps(asdict(state), indent=2, sort_keys=True), encoding="utf-8")


def _append_event(path: Path, row: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, sort_keys=True) + "\n")


def _one_iteration(runtime_root: Path, *, config: OvernightProtocolConfig, state_path: Path, event_log_path: Path) -> Dict[str, Any]:
    db_path = runtime_root / "runtime.db"
    now_ms = int(time.time() * 1000)
    state = _load_state(state_path)

    snapshot = da.get_runtime_status_snapshot(runtime_root=runtime_root, db_path=db_path)
    control = da.get_control_plane_snapshot(db_path=db_path)
    pnl_summary = da.get_paper_pnl_summary(db_path=db_path)
    alerts_df = da.get_runtime_alert_feed(db_path=db_path)
    commands_df = da.get_recent_control_commands(db_path=db_path, limit=25)
    timeline_df = da.get_fill_risk_timeline(limit=100, db_path=db_path)

    alerts = alerts_df.to_dict(orient="records") if not alerts_df.empty else []
    commands = commands_df.to_dict(orient="records") if not commands_df.empty else []
    timeline = timeline_df.to_dict(orient="records") if not timeline_df.empty else []

    next_protocol_state = next_state(snapshot=snapshot, control=control, commands=commands, state=state)
    protocol_observations = build_protocol_observations(
        snapshot=snapshot,
        control=control,
        pnl_summary=pnl_summary,
        fill_timeline_rows=timeline,
        config=config,
    )
    actions = decide_actions(
        snapshot=snapshot,
        control=control,
        pnl_summary=pnl_summary,
        recent_commands=commands,
        fill_timeline_rows=timeline,
        state=next_protocol_state,
        config=config,
        now_ms=now_ms,
    )

    queued_actions: List[Dict[str, Any]] = []
    for action in actions:
        command_id = da.queue_control_command(
            command_type=str(action.get("command_type") or ""),
            payload=dict(action.get("payload") or {}),
            scope=str(action.get("scope") or "global"),
            requested_by="overnight_protocol",
            db_path=db_path,
        )
        queued_actions.append({**action, "command_id": command_id})

    final_state = record_actions(next_protocol_state, queued_actions, now_ms=now_ms)
    _write_state(state_path, final_state)

    supervisor_report = build_report(runtime_root, protocol_observations=protocol_observations)
    linear_comment = build_linear_comment_payload(
        runtime_root=runtime_root.as_posix(),
        snapshot=snapshot,
        actions=queued_actions,
        alerts=alerts,
    )
    payload = {
        "ts_ms": now_ms,
        "runtime_root": runtime_root.as_posix(),
        "snapshot": {
            "mode": snapshot.get("mode"),
            "stage": snapshot.get("stage"),
            "market": snapshot.get("market"),
            "quoteable": snapshot.get("quoteable"),
            "book_health": snapshot.get("book_health"),
        },
        "control": control,
        "pnl_summary": pnl_summary,
        "alerts": alerts,
        "queued_actions": queued_actions,
        "protocol_observations": protocol_observations,
        "linear_comment": linear_comment,
        "protocol_state": asdict(final_state),
    }
    _append_event(event_log_path, payload)
    (runtime_root / "meta" / "overnight_protocol_latest.json").write_text(
        json.dumps(
            {
                "check_ts_ms": now_ms,
                "queued_actions": queued_actions,
                "protocol_observations": protocol_observations,
                "linear_comment": linear_comment,
                "protocol_state": asdict(final_state),
                "supervisor_report_path": str((runtime_root / "meta" / "overnight_supervisor_report.json").as_posix()),
                "latest_alert_count": len(alerts),
                "latest_report": supervisor_report,
            },
            indent=2,
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    return payload


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the overnight protocol monitor against a paper runtime")
    parser.add_argument("--runtime-root", required=True)
    parser.add_argument("--check-interval-secs", type=float, default=300.0)
    parser.add_argument("--duration-secs", type=float, default=0.0, help="0 runs forever")
    parser.add_argument("--once", action="store_true", default=False)
    parser.add_argument("--overnight-protocol-mode", choices=["live_safe", "stress_test"], default="live_safe")
    parser.add_argument("--kill-drawdown-pct", type=float, default=0.10)
    parser.add_argument("--stress-drawdown-log-only-pct", type=float, default=0.10)
    parser.add_argument("--stress-flatten-alert-only", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--stress-allow-drawdown-continuation", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--flatten-before-kill-enabled", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--pause-on-non-quoteable-cycles", type=int, default=2)
    parser.add_argument("--pause-on-pending-backlog-cycles", type=int, default=2)
    parser.add_argument("--pause-on-stale-unwind-count", type=int, default=25)
    parser.add_argument("--pending-command-age-secs", type=float, default=120.0)
    parser.add_argument("--action-cooldown-secs", type=float, default=300.0)
    parser.add_argument("--restart-cooldown-secs", type=float, default=900.0)
    parser.add_argument("--disable-auto-safe-restart", action="store_true", default=False)
    args = parser.parse_args()

    runtime_root = Path(args.runtime_root).resolve()
    meta_dir = runtime_root / "meta"
    meta_dir.mkdir(parents=True, exist_ok=True)
    state_path = meta_dir / "overnight_protocol_state.json"
    event_log_path = meta_dir / "overnight_protocol_events.jsonl"
    config = OvernightProtocolConfig(
        overnight_protocol_mode=str(args.overnight_protocol_mode or "live_safe"),
        kill_drawdown_pct=float(args.kill_drawdown_pct),
        stress_drawdown_log_only_pct=float(args.stress_drawdown_log_only_pct),
        stress_flatten_alert_only=bool(args.stress_flatten_alert_only),
        stress_allow_drawdown_continuation=bool(args.stress_allow_drawdown_continuation),
        flatten_before_kill_enabled=bool(args.flatten_before_kill_enabled),
        pause_on_non_quoteable_cycles=int(args.pause_on_non_quoteable_cycles),
        pause_on_pending_backlog_cycles=int(args.pause_on_pending_backlog_cycles),
        pause_on_stale_unwind_count=int(args.pause_on_stale_unwind_count),
        pending_command_age_secs=float(args.pending_command_age_secs),
        action_cooldown_secs=float(args.action_cooldown_secs),
        restart_cooldown_secs=float(args.restart_cooldown_secs),
        auto_safe_restart=not bool(args.disable_auto_safe_restart),
    )

    started = time.time()
    while True:
        payload = _one_iteration(runtime_root, config=config, state_path=state_path, event_log_path=event_log_path)
        print(json.dumps({"ts_ms": payload["ts_ms"], "queued_actions": payload["queued_actions"]}, sort_keys=True))
        if args.once:
            break
        if float(args.duration_secs) > 0.0 and (time.time() - started) >= float(args.duration_secs):
            break
        time.sleep(max(1.0, float(args.check_interval_secs)))


if __name__ == "__main__":
    main()
