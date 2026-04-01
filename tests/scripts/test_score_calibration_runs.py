import json
import sqlite3
from pathlib import Path

from scripts.score_calibration_runs import (
    CalibrationWeights,
    _resolve_runtime_roots,
    load_runtime_metrics,
    score_runtime_metrics,
)


def _make_runtime(tmp_path: Path, name: str, *, summary: dict, fills: list[tuple], system_states: list[tuple]) -> Path:
    root = tmp_path / name
    meta = root / "meta"
    meta.mkdir(parents=True)
    (meta / "run_summary.json").write_text(json.dumps(summary), encoding="utf-8")
    db = sqlite3.connect((root / "runtime.db").as_posix())
    try:
        db.execute("CREATE TABLE fills (ts_ms INTEGER, token_id TEXT, side TEXT, fill_price REAL, fill_qty REAL)")
        db.executemany("INSERT INTO fills VALUES (?, ?, ?, ?, ?)", fills)
        db.execute("CREATE TABLE system_state (as_of_ts INTEGER, is_frozen INTEGER, reasons TEXT, mode TEXT, payload_json TEXT)")
        db.executemany("INSERT INTO system_state VALUES (?, ?, ?, ?, ?)", system_states)
        db.commit()
    finally:
        db.close()
    return root


def test_score_runtime_metrics_rewards_hedge_and_pnl() -> None:
    metrics = {
        "runtime_root": "/tmp/a",
        "summary": {
            "total_pnl": 10.0,
            "fills": 10,
            "placed_orders": 15,
            "cycle_summary": {"quoteable_ratio": 0.5},
            "risk_proof": {"decision_risk_actions": {"FORCE_FLAT": 10}},
            "hedge_summary": {
                "cluster_actions": {"HEDGE": 2},
                "rejection_reasons": {"no_hedge_market": 5},
            },
            "hedge_candidate_summary": {"accepted_clusters": 2, "quality_gap_positive": 2},
        },
        "hold_summary": {"p90_hold_secs": 5.0, "max_hold_secs": 10.0},
        "action_effectiveness": {"HEDGE": {"observed": 2, "improved": 2, "flat": 0, "worsened": 0}},
        "stranded_positions": {"open_token_count": 0},
        "hedge_candidate_summary": {"accepted_clusters": 2, "quality_gap_positive": 2},
        "hedge_summary": {"cluster_actions": {"HEDGE": 2}, "rejection_reasons": {"no_hedge_market": 5}},
        "rejection_reasons": {"no_hedge_market": 5},
    }

    scored = score_runtime_metrics(metrics, weights=CalibrationWeights())

    assert scored["score"] > 0
    assert scored["headline"]["hedge_events"] == 2
    assert scored["headline"]["hedge_success_ratio"] == 1.0


def test_load_runtime_metrics_reads_run_summary_and_db(tmp_path: Path) -> None:
    summary = {
        "total_pnl": 3.0,
        "fills": 2,
        "placed_orders": 3,
        "cycle_summary": {"quoteable_ratio": 0.5},
        "risk_proof": {"decision_risk_actions": {"FORCE_FLAT": 1}},
        "hedge_summary": {"cluster_actions": {"SKEW": 1}, "rejection_reasons": {"no_hedge_market": 2}},
        "hedge_candidate_summary": {"accepted_clusters": 1, "quality_gap_positive": 1},
    }
    system_states = [
        (
            1000,
            0,
            "",
            "PAPER",
            json.dumps(
                {
                    "cluster_exposure": {"clusters": [{"cluster_id": "BTC-1", "net_yes_exposure_notional": 4.0}], "gross_exposure": 4.0, "active_cluster_count": 1},
                    "cluster_hedge": {"clusters": [{"cluster_id": "BTC-1", "action": "SKEW"}]},
                }
            ),
        ),
        (
            2000,
            0,
            "",
            "PAPER",
            json.dumps(
                {
                    "cluster_exposure": {"clusters": [{"cluster_id": "BTC-1", "net_yes_exposure_notional": 2.0}], "gross_exposure": 2.0, "active_cluster_count": 1},
                    "cluster_hedge": {"clusters": [{"cluster_id": "BTC-1", "action": "HEDGE"}]},
                }
            ),
        ),
    ]
    fills = [
        (1000, "yes_a", "BUY", 0.4, 2.0),
        (4000, "yes_a", "SELL", 0.45, 2.0),
    ]
    root = _make_runtime(tmp_path, "run-a", summary=summary, fills=fills, system_states=system_states)

    metrics = load_runtime_metrics(root)

    assert metrics["summary"]["total_pnl"] == 3.0
    assert metrics["hold_summary"]["matched_round_trips"] == 1
    assert metrics["cluster_summary"]["gross_exposure_peak"] == 4.0
    assert metrics["action_effectiveness"]["SKEW"]["improved"] == 1


def test_resolve_runtime_roots_deduplicates(tmp_path: Path) -> None:
    a = tmp_path / "a"
    b = tmp_path / "b"
    a.mkdir()
    b.mkdir()

    resolved = _resolve_runtime_roots([a.as_posix(), a.as_posix(), b.as_posix()], [])

    assert resolved == [a.resolve(), b.resolve()]
