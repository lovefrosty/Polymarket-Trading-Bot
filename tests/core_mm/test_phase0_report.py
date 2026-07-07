import json
from pathlib import Path

from scripts.report_core_mm_run import build_phase0_report


def test_build_phase0_report_uses_run_summary_and_status(tmp_path: Path) -> None:
    meta = tmp_path / "meta"
    meta.mkdir(parents=True, exist_ok=True)
    (meta / "status.json").write_text(
        json.dumps(
            {
                "mode": "PAPER",
                "market": "btc-updown-15m-1700000100",
                "symbols": ["BTC"],
                "kill_switch_validation": {"status": "passed"},
            }
        ),
        encoding="utf-8",
    )
    (meta / "run_summary.json").write_text(
        json.dumps(
            {
                "fills": 12,
                "placed_orders": 24,
                "canceled_orders": 3,
                "fill_rate": 0.5,
                "realized_net_pnl": 4.0,
                "unrealized_pnl": -1.0,
                "total_pnl": 3.0,
                "total_fees": 0.8,
                "turnover": 120.0,
                "cycle_summary": {
                    "cycles_total": 100,
                    "quoteable_cycles": 70,
                    "freeze_cycles": 5,
                    "no_quote_cycles": 25,
                    "quoteable_ratio": 0.7,
                    "inactive_ratio": 0.3,
                    "freeze_reason_counts": {"books_unavailable": 5},
                    "no_quote_reason_counts": {"flow_blocks_buy": 10},
                },
                "execution_quality": {
                    "avg_realized_spread_bps": 8.0,
                    "avg_fee_bps": 2.5,
                    "avg_net_edge_bps": 5.5,
                    "avg_markout_1s_bps": 1.0,
                    "avg_markout_5s_bps": -0.5,
                    "negative_markout_1s_rate": 0.4,
                    "negative_markout_5s_rate": 0.6,
                    "negative_net_edge_rate": 0.2,
                },
                "phase0_acceptance": {
                    "result": "pass",
                    "economics_ready_for_phase1": True,
                    "quoteable_cycles_present": True,
                    "loss_source_hints": [],
                },
                "risk_proof": {
                    "stale_unwind_observed": True,
                    "force_flat_observed": True,
                    "day_loss_observed": True,
                    "kill_switch_applied_commands": 1,
                    "kill_switch_cycles": 1,
                },
                "hedge_candidate_summary": {
                    "clusters_seen": 2,
                    "accepted_clusters": 1,
                    "rejected_clusters": 1,
                    "deferred_clusters": 0,
                    "action_counts": {"HEDGE": 1, "NONE": 1},
                    "candidate_state_counts": {"accepted": 1, "rejected": 1},
                    "rejection_reason_counts": {"forced_reduction": 1},
                    "quality_gap_state_counts": {"positive": 1},
                    "avg_quality_gap": 3.5,
                },
            }
        ),
        encoding="utf-8",
    )

    report = build_phase0_report(tmp_path)
    assert report["mode"] == "PAPER"
    assert report["market"] == "btc-updown-15m-1700000100"
    assert report["economics"]["total_pnl"] == 3.0
    assert report["fills"]["fill_rate"] == 0.5
    assert report["execution_quality"]["avg_markout_5s_bps"] == -0.5
    assert report["activity"]["quoteable_cycles"] == 70
    assert report["phase0_acceptance"]["economics_ready_for_phase1"] is True
    assert report["risk_proof"]["stale_unwind_observed"] is True
    assert report["hedge_candidates"]["accepted_clusters"] == 1
    assert report["hedge_candidates"]["quality_gap_state_counts"]["positive"] == 1
    assert report["live_readiness"]["go_no_go"] == "go"
    assert report["live_readiness"]["kill_switch_validation_status"] == "passed"
    assert report["recommendation"] == "phase0_passed_ready_for_breadth"


def test_build_phase0_report_blocks_go_when_required_safety_controls_missing(tmp_path: Path) -> None:
    meta = tmp_path / "meta"
    meta.mkdir(parents=True, exist_ok=True)
    (meta / "status.json").write_text(
        json.dumps(
            {
                "mode": "PAPER",
                "market": "btc-range-1h-1700000200",
                "symbols": ["BTC"],
                "kill_switch_validation": {"status": "passed"},
            }
        ),
        encoding="utf-8",
    )
    (meta / "run_summary.json").write_text(
        json.dumps(
            {
                "phase0_acceptance": {
                    "result": "pass",
                    "economics_ready_for_phase1": True,
                    "quoteable_cycles_present": True,
                },
                "risk_proof": {
                    "stale_unwind_observed": False,
                    "force_flat_observed": False,
                    "day_loss_observed": False,
                    "kill_switch_applied_commands": 0,
                    "kill_switch_cycles": 0,
                },
            }
        ),
        encoding="utf-8",
    )

    report = build_phase0_report(tmp_path)
    assert report["live_readiness"]["go_no_go"] == "no_go"
    assert report["live_readiness"]["blockers"] == [
        "stale_unwind_not_observed",
        "force_flat_not_observed",
        "day_loss_not_observed",
    ]
