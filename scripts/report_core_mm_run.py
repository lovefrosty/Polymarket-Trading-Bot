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
    risk_proof = dict(summary.get("risk_proof") or {})
    hedge_candidates = dict(summary.get("hedge_candidate_summary") or {})
    kill_switch_validation = dict(status.get("kill_switch_validation") or {})
    kill_switch_status = str(kill_switch_validation.get("status") or "not_run")
    safety_controls_observed = {
        "stale_unwind": bool(risk_proof.get("stale_unwind_observed")),
        "force_flat": bool(risk_proof.get("force_flat_observed")),
        "day_loss": bool(risk_proof.get("day_loss_observed")),
        "kill_switch": bool(risk_proof.get("kill_switch_applied_commands") or risk_proof.get("kill_switch_cycles")),
    }
    live_safe_go_no_go = "go"
    blockers = []
    if not bool(phase0.get("economics_ready_for_phase1")):
        blockers.append("paper_economics_not_ready")
    if not bool(phase0.get("quoteable_cycles_present")):
        blockers.append("quoteable_selection_not_proven")
    if kill_switch_status != "passed":
        blockers.append("kill_switch_not_validated")
    for control_name in ("stale_unwind", "force_flat", "day_loss"):
        if not safety_controls_observed[control_name]:
            blockers.append(f"{control_name}_not_observed")
    if blockers:
        live_safe_go_no_go = "no_go"
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
        "risk_proof": risk_proof,
        "hedge_candidates": {
            "clusters_seen": hedge_candidates.get("clusters_seen"),
            "accepted_clusters": hedge_candidates.get("accepted_clusters"),
            "rejected_clusters": hedge_candidates.get("rejected_clusters"),
            "deferred_clusters": hedge_candidates.get("deferred_clusters"),
            "action_counts": hedge_candidates.get("action_counts") or {},
            "candidate_state_counts": hedge_candidates.get("candidate_state_counts") or {},
            "rejection_reason_counts": hedge_candidates.get("rejection_reason_counts") or {},
            "quality_gap_state_counts": hedge_candidates.get("quality_gap_state_counts") or {},
            "avg_quality_gap": hedge_candidates.get("avg_quality_gap"),
        },
        "live_readiness": {
            "go_no_go": live_safe_go_no_go,
            "kill_switch_validation_status": kill_switch_status,
            "quoteable_selection_coherent": bool(phase0.get("quoteable_cycles_present")),
            "paper_economics_ready": bool(phase0.get("economics_ready_for_phase1")),
            "safety_controls_observed": safety_controls_observed,
            "blockers": blockers,
        },
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
