from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from dashboard import data_access as da


def _create_runtime(root: Path, name: str, *, status: dict, summary: dict, pnl_rows: list[dict], payload: dict) -> Path:
    runtime_root = root / "tmp" / "core_mm_runs" / name
    meta = runtime_root / "meta"
    meta.mkdir(parents=True, exist_ok=True)

    (meta / "status.json").write_text(json.dumps(status), encoding="utf-8")
    (meta / "run_summary.json").write_text(json.dumps(summary), encoding="utf-8")

    db_path = runtime_root / "runtime.db"
    cx = sqlite3.connect(db_path.as_posix())
    try:
        cx.execute(
            "CREATE TABLE system_state (payload_json TEXT, as_of_ts INTEGER)"
        )
        cx.execute(
            """
            CREATE TABLE paper_pnl (
                ts_ms INTEGER,
                realized_gross_pnl REAL,
                realized_net_pnl REAL,
                unrealized_pnl REAL,
                cumulative_fees REAL,
                turnover REAL,
                win_count INTEGER,
                loss_count INTEGER
            )
            """
        )
        cx.executemany(
            "INSERT INTO paper_pnl VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    row["ts_ms"],
                    row["realized_gross_pnl"],
                    row["realized_net_pnl"],
                    row["unrealized_pnl"],
                    row["cumulative_fees"],
                    row["turnover"],
                    row["win_count"],
                    row["loss_count"],
                )
                for row in pnl_rows
            ],
        )
        cx.execute(
            "INSERT INTO system_state VALUES (?, ?)",
            (json.dumps(payload), status["updated_at_ms"]),
        )
        cx.commit()
    finally:
        cx.close()
    return runtime_root


def test_discover_core_mm_runtimes_includes_snapshot_fields(tmp_path: Path) -> None:
    _create_runtime(
        tmp_path,
        "strategy-a",
        status={
            "stage": "running",
            "mode": "paper",
            "market": "KXBTC-26MAR2203-B69150",
            "run_name": "Trading Spacestation",
            "updated_at_ms": 1_700_000_000_000,
            "decisions": 12,
            "fills": 3,
            "order_actions": 4,
        },
        summary={"fills": 3, "decisions": 12, "realized_net_pnl": 1.25, "unrealized_pnl": 0.75},
        pnl_rows=[
            {
                "ts_ms": 1_700_000_000_000,
                "realized_gross_pnl": 1.0,
                "realized_net_pnl": 0.8,
                "unrealized_pnl": 0.2,
                "cumulative_fees": 0.0,
                "turnover": 20.0,
                "win_count": 1,
                "loss_count": 0,
            }
        ],
        payload={
            "runner": {
                "has_books": True,
                "book_diag": {
                    "tokens_ok": 2,
                    "tokens_blocked": 0,
                    "per_token": {
                        "KXBTC-26MAR2203-B69150:yes": {"best_bid": 0.36, "best_ask": 0.37}
                    },
                },
            },
            "broker_stats": {"realized_net_pnl": 1.25, "unrealized_pnl": 0.75},
        },
    )

    runtimes = da.discover_core_mm_runtimes(repo_root=tmp_path)

    assert len(runtimes) == 1
    row = runtimes.iloc[0]
    assert row["strategy_name"] == "Trading Spacestation"
    assert row["exchange"] == "Kalshi"
    assert bool(row["quoteable"]) is True
    assert row["book_health"] == "healthy"
    assert isinstance(row["selection"], dict)
    assert isinstance(row["active_market_health"], dict)


def test_get_portfolio_curve_from_runtimes_aggregates_multiple_db_files(tmp_path: Path) -> None:
    runtime_a = _create_runtime(
        tmp_path,
        "strategy-a",
        status={"stage": "running", "mode": "paper", "market": "KXBTC-26MAR2203-B69150", "run_name": "A", "updated_at_ms": 1_700_000_000_000},
        summary={"fills": 1, "decisions": 1, "realized_net_pnl": 1.0, "unrealized_pnl": 0.5},
        pnl_rows=[
            {
                "ts_ms": 1_700_000_000_000,
                "realized_gross_pnl": 1.0,
                "realized_net_pnl": 1.0,
                "unrealized_pnl": 0.5,
                "cumulative_fees": 0.0,
                "turnover": 10.0,
                "win_count": 1,
                "loss_count": 0,
            },
            {
                "ts_ms": 1_700_000_000_500,
                "realized_gross_pnl": 1.5,
                "realized_net_pnl": 1.2,
                "unrealized_pnl": 0.8,
                "cumulative_fees": 0.0,
                "turnover": 20.0,
                "win_count": 1,
                "loss_count": 0,
            },
        ],
        payload={"runner": {"has_books": True, "book_diag": {"tokens_ok": 2, "tokens_blocked": 0, "per_token": {}}}},
    )
    runtime_b = _create_runtime(
        tmp_path,
        "strategy-b",
        status={"stage": "running", "mode": "paper", "market": "KXBTC-26MAR2203-B69250", "run_name": "B", "updated_at_ms": 1_700_000_000_000},
        summary={"fills": 2, "decisions": 2, "realized_net_pnl": 2.0, "unrealized_pnl": 1.0},
        pnl_rows=[
            {
                "ts_ms": 1_700_000_000_000,
                "realized_gross_pnl": 2.0,
                "realized_net_pnl": 2.0,
                "unrealized_pnl": 1.0,
                "cumulative_fees": 0.0,
                "turnover": 15.0,
                "win_count": 2,
                "loss_count": 0,
            },
            {
                "ts_ms": 1_700_000_000_500,
                "realized_gross_pnl": 2.5,
                "realized_net_pnl": 2.2,
                "unrealized_pnl": 1.3,
                "cumulative_fees": 0.0,
                "turnover": 25.0,
                "win_count": 2,
                "loss_count": 0,
            },
        ],
        payload={"runner": {"has_books": True, "book_diag": {"tokens_ok": 2, "tokens_blocked": 0, "per_token": {}}}},
    )

    runtimes = da.discover_core_mm_runtimes(repo_root=tmp_path)
    curve = da.get_portfolio_curve_from_runtimes(runtimes=runtimes)

    assert not curve.empty
    assert list(curve["ts_ms"]) == [1_700_000_000_000, 1_700_000_000_500]
    first = curve.iloc[0]
    second = curve.iloc[1]
    assert first["total_pnl"] == 4.5
    assert second["total_pnl"] == 5.5
    assert float(second["equity_peak"]) == 5.5


def test_get_runtime_status_snapshot_gracefully_handles_missing_selection_fields(tmp_path: Path) -> None:
    _create_runtime(
        tmp_path,
        "strategy-a",
        status={
            "stage": "awaiting_books",
            "mode": "paper",
            "market": "KXBTC-26MAR2203-B69050",
            "run_name": "A",
            "updated_at_ms": 1_700_000_000_000,
            "decisions": 4,
            "fills": 0,
            "order_actions": 0,
        },
        summary={"fills": 0, "decisions": 4, "realized_net_pnl": 0.0, "unrealized_pnl": 0.0},
        pnl_rows=[
            {
                "ts_ms": 1_700_000_000_000,
                "realized_gross_pnl": 0.0,
                "realized_net_pnl": 0.0,
                "unrealized_pnl": 0.0,
                "cumulative_fees": 0.0,
                "turnover": 0.0,
                "win_count": 0,
                "loss_count": 0,
            }
        ],
        payload={
            "runner": {
                "has_books": True,
                "book_diag": {
                    "tokens_ok": 2,
                    "tokens_blocked": 0,
                    "per_token": {"KXBTC-26MAR2203-B69050:yes": {"best_bid": 0.13, "best_ask": 0.17}},
                },
            }
        },
    )

    snapshot = da.get_runtime_status_snapshot(runtime_root=tmp_path / "tmp" / "core_mm_runs" / "strategy-a")

    assert snapshot["selection"] == {}
    assert snapshot["active_market_health"] == {}
    assert snapshot["quoteable"] is True
    assert snapshot["book_health"] == "healthy"
    assert snapshot["state"] == "healthy"


def test_cluster_exposure_helpers_use_runtime_payload(tmp_path: Path) -> None:
    runtime_root = _create_runtime(
        tmp_path,
        "strategy-clusters",
        status={
            "stage": "running",
            "mode": "paper",
            "market": "KXBTC-26MAR2203-B69050",
            "run_name": "Cluster Strategy",
            "updated_at_ms": 1_700_000_000_000,
        },
        summary={"fills": 0, "decisions": 0, "realized_net_pnl": 0.0, "unrealized_pnl": 0.0},
        pnl_rows=[
            {
                "ts_ms": 1_700_000_000_000,
                "realized_gross_pnl": 0.0,
                "realized_net_pnl": 0.0,
                "unrealized_pnl": 0.0,
                "cumulative_fees": 0.0,
                "turnover": 0.0,
                "win_count": 0,
                "loss_count": 0,
            }
        ],
        payload={
            "runner": {
                "has_books": True,
                "book_diag": {"tokens_ok": 2, "tokens_blocked": 0, "per_token": {}},
                "cluster_exposure": {
                    "cluster_count": 1,
                    "active_cluster_count": 1,
                    "gross_exposure": 7.0,
                    "unrealized_pnl": -0.5,
                    "current_equity": 500.0,
                    "reference_equity": 500.0,
                    "clusters": [
                        {
                            "cluster_id": "BTC-HOURLY-1",
                            "event_id": "BTC-HOURLY-1",
                            "market_count": 2,
                            "active_market_count": 1,
                            "yes_exposure_notional": 5.0,
                            "no_exposure_notional": 2.0,
                            "net_yes_exposure_notional": 3.0,
                            "gross_exposure": 7.0,
                            "unrealized_pnl": -0.5,
                            "time_to_expiry_ms": 120_000,
                            "max_event_exposure_notional": 25.0,
                            "remaining_event_exposure_notional": 18.0,
                            "hedge_action": "SKEW",
                            "hedge_action_reason": "Reduce yes-heavy inventory",
                            "hedge_ratio": 0.75,
                            "hedge_target_market": "btc-updown-15m-b",
                            "hedge_target_token": "no_b",
                            "hedge_target_side": "buy",
                            "markets": [
                                {
                                    "market_id": "btc-updown-15m-a",
                                    "condition_id": "a",
                                    "active": True,
                                    "market_position_notional": 5.0,
                                    "market_unrealized_pnl": -0.3,
                                    "yes_exposure_notional": 5.0,
                                    "no_exposure_notional": 0.0,
                                    "unknown_exposure_notional": 0.0,
                                    "time_to_expiry_ms": 120_000,
                                }
                            ],
                        }
                    ],
                },
                "cluster_hedge": {
                    "enabled": True,
                    "paper_only": True,
                    "clusters": [
                        {
                            "cluster_id": "BTC-HOURLY-1",
                            "control_state": "SKEW_ONLY",
                            "action": "SKEW",
                            "action_reason": "Reduce yes-heavy inventory",
                            "hedge_ratio": 0.75,
                            "hedge_market_id": "btc-updown-15m-b",
                            "hedge_target_token_id": "no_b",
                            "hedge_target_side": "buy",
                            "affected_market_ids": ["btc-updown-15m-a"],
                            "rejection_reasons": [],
                        }
                    ],
                },
            },
            "selection": {
                "accepted_candidates": [
                    {
                        "ticker": "btc-updown-15m-a",
                        "reason": "quoteable_book",
                        "quoteability_state": "quoteable",
                        "score": 0.91,
                        "liquidity_score": 0.88,
                    }
                ],
                "rejected_candidates": [
                    {
                        "ticker": "btc-updown-15m-b",
                        "reason": "price_out_of_range",
                        "quoteability_state": "price_out_of_range",
                        "score": 0.61,
                        "liquidity_score": 0.77,
                        "blocking_market_id": "btc-updown-15m-a",
                        "blocking_cluster_id": "BTC-HOURLY-1",
                        "blocking_reason": "cluster_cap_reached",
                    }
                ],
            },
            "broker_stats": {"realized_net_pnl": 0.0, "unrealized_pnl": 0.0},
        },
    )

    snapshot = da.get_runtime_status_snapshot(runtime_root=runtime_root, db_path=runtime_root / "runtime.db")
    cluster_snapshot = da.get_cluster_exposure_snapshot(runtime_snapshot=snapshot)
    cluster_rows = da.get_cluster_exposure_rows(runtime_snapshot=snapshot)
    market_rows = da.get_cluster_market_rows(runtime_snapshot=snapshot)
    active_market_rows = da.get_active_market_rows(runtime_snapshot=snapshot)
    selection_rows = da.get_selection_diagnostic_rows(runtime_snapshot=snapshot)
    selection_gaps = da.get_selection_diagnostic_gaps(runtime_snapshot=snapshot)

    assert cluster_snapshot["cluster_count"] == 1
    assert snapshot["cluster_exposure"]["gross_exposure"] == 7.0
    assert not cluster_rows.empty
    assert cluster_rows.iloc[0]["cluster_id"] == "BTC-HOURLY-1"
    assert float(cluster_rows.iloc[0]["net_yes_exposure_notional"]) == 3.0
    assert cluster_rows.iloc[0]["hedge_action"] == "SKEW"
    assert cluster_rows.iloc[0]["hedge_action_reason"] == "Reduce yes-heavy inventory"
    assert float(cluster_rows.iloc[0]["hedge_ratio"]) == 0.75
    assert cluster_rows.iloc[0]["hedge_target_market"] == "btc-updown-15m-b"
    assert cluster_rows.iloc[0]["hedge_target_token"] == "no_b"
    assert cluster_rows.iloc[0]["hedge_target_side"] == "buy"
    assert cluster_rows.iloc[0]["control_state"] == "SKEW_ONLY"
    assert cluster_rows.iloc[0]["affected_market_ids"] == ["btc-updown-15m-a"]
    assert not market_rows.empty
    assert market_rows.iloc[0]["market_id"] == "btc-updown-15m-a"
    assert bool(market_rows.iloc[0]["affected_by_cluster_action"]) is True
    assert not active_market_rows.empty
    assert active_market_rows.iloc[0]["market_id"] == "btc-updown-15m-a"
    assert not selection_rows.empty
    rejected = selection_rows[selection_rows["accepted"] == False].iloc[0]
    assert rejected["blocking_market_id"] == "btc-updown-15m-a"
    assert rejected["blocking_cluster_id"] == "BTC-HOURLY-1"
    assert rejected["blocking_reason"] == "cluster_cap_reached"
    assert selection_gaps == []


def test_get_hedge_candidate_rows_uses_runtime_db_table(tmp_path: Path) -> None:
    runtime_root = _create_runtime(
        tmp_path,
        "strategy-hedge-candidates",
        status={
            "stage": "running",
            "mode": "paper",
            "market": "KXBTC-26MAR2203-B69150",
            "run_name": "Hedge Candidates",
            "updated_at_ms": 1_700_000_000_000,
            "decisions": 8,
            "fills": 2,
            "order_actions": 3,
        },
        summary={"fills": 2, "decisions": 8, "realized_net_pnl": 1.0, "unrealized_pnl": 0.25},
        pnl_rows=[
            {
                "ts_ms": 1_700_000_000_000,
                "realized_gross_pnl": 1.1,
                "realized_net_pnl": 1.0,
                "unrealized_pnl": 0.25,
                "cumulative_fees": 0.1,
                "turnover": 11.0,
                "win_count": 1,
                "loss_count": 0,
            }
        ],
        payload={"runner": {"has_books": True, "book_diag": {"tokens_ok": 2, "tokens_blocked": 0, "per_token": {}}}},
    )

    db_path = runtime_root / "runtime.db"
    cx = sqlite3.connect(db_path.as_posix())
    try:
        cx.execute(
            """
            CREATE TABLE hedge_candidates (
                ts_ms INTEGER,
                event_id INTEGER,
                cluster_id TEXT,
                action TEXT,
                candidate_state TEXT,
                control_state TEXT,
                action_reason TEXT,
                dominant_side TEXT,
                hedge_market_id TEXT,
                hedge_target_token_id TEXT,
                hedge_target_side TEXT,
                hedge_ratio REAL,
                inventory_market_quality_score REAL,
                hedge_quality_score REAL,
                hedge_quality_gap REAL,
                hedge_success_window_ms INTEGER,
                hedge_failed_cooldown_until_ms INTEGER,
                candidate_count INTEGER,
                accepted_count INTEGER,
                rejection_counts_json TEXT,
                best_candidate_market_id TEXT,
                best_candidate_token_id TEXT,
                best_candidate_quality_score REAL,
                best_candidate_quality_gap REAL,
                search_profile TEXT,
                proof_only_lane INTEGER,
                proof_only_bucket_distance INTEGER,
                proof_only_expiry_slack_ms INTEGER,
                rejection_reasons TEXT,
                affected_market_ids TEXT,
                token_directives_json TEXT,
                quality_gap_state TEXT,
                payload_json TEXT
            )
            """
        )
        cx.execute(
            """
            INSERT INTO hedge_candidates VALUES (
                1700000000000,
                1,
                'BTC-HOURLY-1',
                'HEDGE',
                'accepted',
                'HEDGE_ACTIVE',
                'better hedge quality',
                'yes',
                'btc-updown-15m-b',
                'no_b',
                'buy',
                0.5,
                88.0,
                91.5,
                3.5,
                5000,
                NULL,
                1,
                1,
                '{"forced_reduction": 1}',
                'btc-updown-15m-b',
                'no_b',
                91.5,
                3.5,
                'production',
                0,
                2,
                60000,
                '["forced_reduction"]',
                '["btc-updown-15m-a"]',
                '[]',
                'positive',
                '{"cluster_id":"BTC-HOURLY-1"}'
            )
            """
        )
        cx.commit()
    finally:
        cx.close()

    snapshot = da.get_runtime_status_snapshot(runtime_root=runtime_root, db_path=db_path)
    rows = da.get_hedge_candidate_rows(runtime_snapshot=snapshot, db_path=db_path)
    gaps = da.get_hedge_candidate_gaps(runtime_snapshot=snapshot, db_path=db_path)

    assert not rows.empty
    row = rows.iloc[0]
    assert bool(row["accepted"]) is True
    assert row["candidate_state"] == "accepted"
    assert row["hedge_quality_gap"] == 3.5
    assert row["quality_gap_state"] == "positive"
    assert gaps == []


def test_cluster_calibration_gaps_flag_missing_optional_runner_fields(tmp_path: Path) -> None:
    runtime_root = _create_runtime(
        tmp_path,
        "strategy-cluster-gaps",
        status={
            "stage": "running",
            "mode": "paper",
            "market": "KXBTC-26MAR2203-B69050",
            "run_name": "Cluster Gaps",
            "updated_at_ms": 1_700_000_000_000,
        },
        summary={"fills": 0, "decisions": 0, "realized_net_pnl": 0.0, "unrealized_pnl": 0.0},
        pnl_rows=[
            {
                "ts_ms": 1_700_000_000_000,
                "realized_gross_pnl": 0.0,
                "realized_net_pnl": 0.0,
                "unrealized_pnl": 0.0,
                "cumulative_fees": 0.0,
                "turnover": 0.0,
                "win_count": 0,
                "loss_count": 0,
            }
        ],
        payload={
            "runner": {
                "has_books": True,
                "book_diag": {"tokens_ok": 2, "tokens_blocked": 0, "per_token": {}},
                "cluster_exposure": {
                    "cluster_count": 1,
                    "active_cluster_count": 1,
                    "clusters": [
                        {
                            "cluster_id": "BTC-HOURLY-1",
                            "market_count": 1,
                            "active_market_count": 1,
                            "yes_exposure_notional": 1.0,
                            "no_exposure_notional": 0.0,
                            "net_yes_exposure_notional": 1.0,
                            "gross_exposure": 1.0,
                            "time_to_expiry_ms": 60_000,
                            "markets": [],
                        }
                    ],
                },
            }
        },
    )

    snapshot = da.get_runtime_status_snapshot(runtime_root=runtime_root, db_path=runtime_root / "runtime.db")
    gaps = da.get_cluster_calibration_gaps(runtime_snapshot=snapshot)
    selection_gaps = da.get_selection_diagnostic_gaps(runtime_snapshot=snapshot)

    assert "cluster control_state" in gaps
    assert "cluster hedge action label" in gaps
    assert "cluster action reason" in gaps
    assert "cluster hedge ratio" in gaps
    assert "hedge target market" in gaps
    assert "selection candidate diagnostics missing" in selection_gaps


def test_get_hedge_readout_summary_normalizes_candidate_and_tail_metrics(tmp_path: Path) -> None:
    runtime_root = _create_runtime(
        tmp_path,
        "strategy-hedge-readout",
        status={
            "stage": "running",
            "mode": "paper",
            "market": "KXBTC-26MAR2203-B69050",
            "run_name": "Hedge Readout",
            "updated_at_ms": 1_700_000_000_000,
        },
        summary={
            "fills": 3,
            "decisions": 5,
            "realized_net_pnl": 1.0,
            "unrealized_pnl": 0.25,
            "risk_proof": {
                "hold_tail": {
                    "sample_count": 7,
                    "p50_ms": 1_200,
                    "p90_ms": 4_500,
                    "p95_ms": 6_500,
                    "max_ms": 12_000,
                    "distribution": {"p50": 1_200, "p90": 4_500, "p95": 6_500, "max": 12_000},
                }
            },
        },
        pnl_rows=[
            {
                "ts_ms": 1_700_000_000_000,
                "realized_gross_pnl": 1.0,
                "realized_net_pnl": 1.0,
                "unrealized_pnl": 0.25,
                "cumulative_fees": 0.0,
                "turnover": 10.0,
                "win_count": 1,
                "loss_count": 0,
            }
        ],
        payload={
            "runner": {
                "has_books": True,
                "book_diag": {
                    "tokens_ok": 2,
                    "tokens_blocked": 0,
                    "per_token": {"KXBTC-26MAR2203-B69050:yes": {"best_bid": 0.13, "best_ask": 0.17}},
                },
            },
            "selection": {
                "selected_reason": "quoteable_book",
                "selected_market": "btc-updown-15m-a",
                "selected_score": 0.94,
                "accepted_candidates": [
                    {
                        "ticker": "btc-updown-15m-a",
                        "reason": "quoteable_book",
                        "quoteability_state": "quoteable",
                        "score": 0.94,
                        "liquidity_score": 0.91,
                    }
                ],
                "rejected_candidates": [
                    {
                        "ticker": "btc-updown-15m-b",
                        "reason": "no_hedge_market",
                        "quoteability_state": "no_hedge_market",
                        "score": 0.57,
                        "liquidity_score": 0.49,
                        "blocking_market_id": "btc-updown-15m-a",
                        "blocking_cluster_id": "BTC-HOURLY-1",
                        "blocking_reason": "cluster_cap_reached",
                    }
                ],
            },
            "control_state": {
                "forced_flat_events": ["evt-1", "evt-2"],
                "forced_flat_markets": ["btc-updown-15m-a"],
            },
            "broker_stats": {"realized_net_pnl": 1.0, "unrealized_pnl": 0.25},
        },
    )

    snapshot = da.get_runtime_status_snapshot(runtime_root=runtime_root, db_path=runtime_root / "runtime.db")
    hedge_summary = da.get_hedge_readout_summary(runtime_snapshot=snapshot, db_path=runtime_root / "runtime.db")

    assert hedge_summary["candidate_count"] == 2
    assert hedge_summary["accepted_count"] == 1
    assert hedge_summary["rejected_count"] == 1
    assert hedge_summary["top_rejection_reason"] == "no_hedge_market"
    assert hedge_summary["rejection_reason_counts"]["no_hedge_market"] == 1
    assert hedge_summary["quality_gap"] == 0.37
    assert hedge_summary["hold_tail"]["sample_count"] == 7
    assert hedge_summary["hold_tail"]["p95_ms"] == 6500.0
    assert hedge_summary["forced_flat_markets"] == ["btc-updown-15m-a"]
    assert hedge_summary["forced_flat_events"] == ["evt-1", "evt-2"]
