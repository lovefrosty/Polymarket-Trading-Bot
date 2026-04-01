from __future__ import annotations

from pathlib import Path

from dashboard import data_access as da

from core_mm.risk_harness import run_proof_only_hedge_harness, run_safe_first_risk_harness


def test_safe_first_risk_harness_emits_cross_and_force_flat(tmp_path: Path) -> None:
    result = run_safe_first_risk_harness(tmp_path)
    db_path = Path(result["runtime_db_path"])
    timeline = da.get_fill_risk_timeline(limit=50, db_path=db_path)
    assert not timeline.empty
    joined = " | ".join(str(item) for item in timeline.get("summary", []).tolist())
    assert "FORCE_FLAT" in joined

    fills = da.get_fills_recent(limit=20, db_path=db_path)
    assert not fills.empty
    exit_modes = set(str(item) for item in fills.get("exit_mode", []).fillna("").tolist())
    risk_actions = set(str(item) for item in fills.get("risk_action", []).fillna("").tolist())
    assert "FORCE_FLAT" in joined

    control = da.get_control_plane_snapshot(db_path=db_path)
    assert control["kill_switch_enabled"] is True
    assert control["flatten_only_mode"] is True

    summary = result["summary"]
    assert summary["risk_proof"]["day_loss_observed"] is True
    assert summary["risk_proof"]["force_flat_observed"] is True


def test_proof_only_hedge_harness_emits_real_hedge(tmp_path: Path) -> None:
    result = run_proof_only_hedge_harness(tmp_path)
    db_path = Path(result["runtime_db_path"])
    fills = da.get_fills_recent(limit=20, db_path=db_path)
    assert not fills.empty
    hedge_fills = fills[fills["hedge_action"] == "HEDGE"]
    assert not hedge_fills.empty
    assert hedge_fills.iloc[0]["hedge_market_id"] == "btc-updown-15m-B70850"

    summary = result["summary"]
    hedge_candidate_summary = summary["hedge_candidate_summary"]
    hedge_summary = summary["hedge_summary"]
    assert hedge_summary["accepted_count_total"] >= 1
    assert hedge_summary["proof_only_cluster_count"] >= 1
    assert hedge_summary["best_candidate_quality_gap_by_cluster"]
    assert hedge_candidate_summary["candidate_state_counts"]["accepted"] >= 1
    assert hedge_candidate_summary["quality_gap_state_counts"]["positive"] >= 1
    assert hedge_summary["cluster_actions"]["HEDGE"] >= 1
