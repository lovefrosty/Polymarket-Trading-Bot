from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any, Dict


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from dashboard import data_access as da


def _count_of(mapping: Dict[str, Any], key: str) -> int:
    value = mapping.get(key)
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _classify_verdict(*, total_pnl: float, market_change_count: int, risk_action_counts: Dict[str, Any], control_state_counts: Dict[str, Any]) -> str:
    stale_unwind = _count_of(risk_action_counts, "STALE_UNWIND")
    force_flat = _count_of(risk_action_counts, "FORCE_FLAT")
    unwind_only = _count_of(control_state_counts, "UNWIND_ONLY")
    if total_pnl <= 0.0:
        return "not_launchable"
    if force_flat > 0 or stale_unwind > 25 or unwind_only > 250:
        return "profit_depended_on_emergency_exits"
    if market_change_count > 10:
        return "profitable_but_rotation_heavy"
    return "clean_quote_first"


def build_rotating_single_market_report(runtime_root: Path) -> Dict[str, Any]:
    root = Path(runtime_root).resolve()
    db_path = root / "runtime.db"
    snapshot = da.get_runtime_status_snapshot(runtime_root=root, db_path=db_path if db_path.exists() else None)
    selection = da.get_selection_session_summary(runtime_snapshot=snapshot, db_path=db_path if db_path.exists() else None)
    performance = da.get_session_performance_summary(runtime_snapshot=snapshot, db_path=db_path if db_path.exists() else None)
    total_pnl = float(snapshot.get("total_pnl") or 0.0)
    verdict = _classify_verdict(
        total_pnl=total_pnl,
        market_change_count=int(selection.get("market_change_count") or 0),
        risk_action_counts=dict(performance.get("risk_action_counts") or {}),
        control_state_counts=dict(performance.get("control_state_counts") or {}),
    )
    return {
        "runtime_root": root.as_posix(),
        "mode": snapshot.get("mode"),
        "market": snapshot.get("market"),
        "selected_reason": snapshot.get("selected_reason"),
        "launch_scope": ((snapshot.get("selection") or {}).get("portfolio_selection") or {}).get("launch_scope") if isinstance(snapshot.get("selection"), dict) else None,
        "economics": {
            "realized_net_pnl": performance.get("realized_net_pnl"),
            "unrealized_pnl": performance.get("unrealized_pnl"),
            "total_pnl": performance.get("total_pnl"),
            "cumulative_fees": performance.get("cumulative_fees"),
            "turnover": performance.get("turnover"),
        },
        "session_selection": {
            "episode_count": selection.get("episode_count"),
            "market_change_count": selection.get("market_change_count"),
            "current_episode_started_at_ms": selection.get("current_episode_started_at_ms"),
            "previous_market": selection.get("previous_market"),
            "latest_switch_reason": selection.get("latest_switch_reason"),
            "top_markets_by_decision_count": selection.get("top_markets_by_decision_count") or [],
            "top_switch_reasons": selection.get("top_switch_reasons") or [],
            "recent_episodes": selection.get("recent_episodes") or [],
        },
        "session_performance": {
            "fill_count": performance.get("fill_count"),
            "distinct_orders": performance.get("distinct_orders"),
            "max_drawdown_abs": performance.get("max_drawdown_abs"),
            "max_drawdown_pct_peak": performance.get("max_drawdown_pct_peak"),
            "control_state_counts": performance.get("control_state_counts") or {},
            "risk_action_counts": performance.get("risk_action_counts") or {},
            "hedge_action_counts": performance.get("hedge_action_counts") or {},
            "latest_fill_fee": performance.get("latest_fill_fee") or {},
        },
        "verdict": verdict,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Build rotating single-market launch report for a core_mm runtime")
    parser.add_argument("--runtime-root", required=True)
    parser.add_argument("--output", default=None)
    args = parser.parse_args()

    runtime_root = Path(args.runtime_root).resolve()
    output = Path(args.output) if args.output else runtime_root / "meta" / "rotating_single_market_report.json"
    report = build_rotating_single_market_report(runtime_root)
    output.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps({"output": output.as_posix(), "verdict": report["verdict"]}, sort_keys=True))


if __name__ == "__main__":
    main()
