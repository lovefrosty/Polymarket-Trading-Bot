from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any, Dict, Optional

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from dashboard import data_access as da


def build_report(runtime_root: Path, *, protocol_observations: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    db_path = runtime_root / "runtime.db"
    status = da.get_run_status(runtime_root=runtime_root, db_path=db_path)
    summary = da.get_run_summary(runtime_root=runtime_root, db_path=db_path)
    snapshot = da.get_runtime_status_snapshot(runtime_root=runtime_root, db_path=db_path)
    control = da.get_control_plane_snapshot(db_path=db_path)
    alerts = da.get_runtime_alert_feed(db_path=db_path)
    workstreams = da.get_overnight_supervision_rows(db_path=db_path)
    commands = da.get_recent_control_commands(db_path=db_path, limit=25)
    return {
        "runtime_root": runtime_root.as_posix(),
        "db_path": db_path.as_posix(),
        "run_id": status.get("run_id") or runtime_root.name,
        "strategy_name": snapshot.get("strategy_name"),
        "mode": snapshot.get("mode"),
        "stage": snapshot.get("stage"),
        "market": snapshot.get("market"),
        "quoteable": snapshot.get("quoteable"),
        "book_health": snapshot.get("book_health"),
        "total_pnl": snapshot.get("total_pnl"),
        "summary": {
            "fills": summary.get("fills"),
            "placed_orders": summary.get("placed_orders"),
            "total_pnl": summary.get("total_pnl"),
            "realized_net_pnl": summary.get("realized_net_pnl"),
            "unrealized_pnl": summary.get("unrealized_pnl"),
            "updated_at_ms": summary.get("updated_at_ms"),
        },
        "control_state": control,
        "protocol_observations": dict(protocol_observations or {}),
        "alerts": alerts.to_dict(orient="records") if not alerts.empty else [],
        "workstreams": workstreams.to_dict(orient="records") if not workstreams.empty else [],
        "recent_commands": commands.to_dict(orient="records") if not commands.empty else [],
        "linear_comment_template": {
            "What changed": "Summarize the runtime, dashboard, or control-plane change in one paragraph.",
            "Evidence": f"Runtime root: {runtime_root.as_posix()} | Summary: {(runtime_root / 'meta' / 'run_summary.json').as_posix()}",
            "Risk impact": "State whether risk improved, degraded, or stayed neutral.",
            "Next task": "Name the next concrete task and route it to Kant or Ramanujan.",
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Build overnight supervision report for a core_mm runtime")
    parser.add_argument("--runtime-root", required=True)
    parser.add_argument("--output", default=None)
    args = parser.parse_args()

    runtime_root = Path(args.runtime_root)
    output = Path(args.output) if args.output else runtime_root / "meta" / "overnight_supervisor_report.json"
    payload = build_report(runtime_root)
    output.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps({"output": output.as_posix()}, sort_keys=True))


if __name__ == "__main__":
    main()
