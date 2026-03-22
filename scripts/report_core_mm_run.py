from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict


def _read_json(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload if isinstance(payload, dict) else {}


def _phase0_recommendation(result: str) -> str:
    mapping = {
        "pass": "phase0_passed_ready_for_breadth",
        "tunable_loss": "tune_parameters_before_expansion",
        "structural_blocker": "fix_architecture_before_expansion",
        "needs_review": "collect_more_runtime_or_review_manually",
    }
    return mapping.get(str(result), "collect_more_runtime_or_review_manually")


def build_phase0_report(runtime_root: Path) -> Dict[str, Any]:
    root = Path(runtime_root)
    meta_dir = root / "meta"
    summary = _read_json(meta_dir / "run_summary.json")
    status = _read_json(meta_dir / "status.json")
    cycle_summary = dict(summary.get("cycle_summary") or {})
    execution_quality = dict(summary.get("execution_quality") or {})
    phase0 = dict(summary.get("phase0_acceptance") or {})
    report = {
        "runtime_root": root.as_posix(),
        "mode": status.get("mode"),
        "market": status.get("market"),
        "symbols": status.get("symbols") or [],
        "economics": {
            "realized_net_pnl": summary.get("realized_net_pnl"),
            "unrealized_pnl": summary.get("unrealized_pnl"),
            "total_pnl": summary.get("total_pnl"),
            "total_fees": summary.get("total_fees"),
            "turnover": summary.get("turnover"),
        },
        "fills": {
            "fills": summary.get("fills"),
            "placed_orders": summary.get("placed_orders"),
            "canceled_orders": summary.get("canceled_orders"),
            "fill_rate": summary.get("fill_rate"),
        },
        "execution_quality": {
            "avg_realized_spread_bps": execution_quality.get("avg_realized_spread_bps"),
            "avg_fee_bps": execution_quality.get("avg_fee_bps"),
            "avg_net_edge_bps": execution_quality.get("avg_net_edge_bps"),
            "avg_markout_1s_bps": execution_quality.get("avg_markout_1s_bps"),
            "avg_markout_5s_bps": execution_quality.get("avg_markout_5s_bps"),
            "negative_markout_1s_rate": execution_quality.get("negative_markout_1s_rate"),
            "negative_markout_5s_rate": execution_quality.get("negative_markout_5s_rate"),
            "negative_net_edge_rate": execution_quality.get("negative_net_edge_rate"),
        },
        "activity": {
            "cycles_total": cycle_summary.get("cycles_total"),
            "quoteable_cycles": cycle_summary.get("quoteable_cycles"),
            "freeze_cycles": cycle_summary.get("freeze_cycles"),
            "no_quote_cycles": cycle_summary.get("no_quote_cycles"),
            "quoteable_ratio": cycle_summary.get("quoteable_ratio"),
            "inactive_ratio": cycle_summary.get("inactive_ratio"),
            "freeze_reason_counts": cycle_summary.get("freeze_reason_counts") or {},
            "no_quote_reason_counts": cycle_summary.get("no_quote_reason_counts") or {},
        },
        "phase0_acceptance": phase0,
        "recommendation": _phase0_recommendation(str(phase0.get("result") or "")),
    }
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Build deterministic Phase 0 profitability report for a core_mm runtime")
    parser.add_argument("--runtime-root", required=True)
    parser.add_argument("--output", default=None)
    args = parser.parse_args()

    runtime_root = Path(args.runtime_root)
    output = Path(args.output) if args.output else runtime_root / "meta" / "phase0_report.json"
    report = build_phase0_report(runtime_root)
    output.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps({"output": output.as_posix()}, sort_keys=True))


if __name__ == "__main__":
    main()
