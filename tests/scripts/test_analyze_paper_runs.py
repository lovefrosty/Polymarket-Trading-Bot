from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.analyze_paper_runs import analyze_run, build_equity_curve, market_breakdown, parse_payload


REPO_ROOT = Path(__file__).resolve().parents[2]


def test_build_equity_curve_deduplicates_realized_and_sums_token_unrealized() -> None:
    rows = [
        {"ts_ms": 1_000, "token_id": "yes", "realized_net_pnl": 2.0, "unrealized_pnl": -0.5},
        {"ts_ms": 1_000, "token_id": "no", "realized_net_pnl": 2.0, "unrealized_pnl": 0.0},
        {"ts_ms": 2_000, "token_id": "yes", "realized_net_pnl": 1.5, "unrealized_pnl": -1.0},
        {"ts_ms": 2_000, "token_id": "no", "realized_net_pnl": 1.5, "unrealized_pnl": 0.0},
    ]

    curve = build_equity_curve(rows)

    assert curve[0]["total_pnl"] == pytest.approx(1.5)
    assert curve[1]["total_pnl"] == pytest.approx(0.5)
    assert curve[1]["drawdown"] == pytest.approx(1.0)


def test_build_equity_curve_rejects_inconsistent_duplicate_realized_values() -> None:
    rows = [
        {"ts_ms": 1_000, "realized_net_pnl": 1.0, "unrealized_pnl": 0.0},
        {"ts_ms": 1_000, "realized_net_pnl": 2.0, "unrealized_pnl": 0.0},
    ]

    with pytest.raises(ValueError, match="Inconsistent portfolio realized PnL"):
        build_equity_curve(rows)


def test_market_breakdown_attributes_fill_level_net_pnl_and_fees() -> None:
    fills = [
        {
            "payload_json": json.dumps(
                {"market_slug": "market-a", "realized_net_pnl_delta": 1.25, "fee_usdc": 0.05}
            )
        },
        {
            "payload_json": json.dumps(
                {"market_slug": "market-a", "realized_net_pnl_delta": -0.25, "fee_usdc": 0.02}
            )
        },
        {"payload_json": {"market_slug": "market-b", "realized_net_pnl_delta": 0.5}},
    ]

    breakdown = market_breakdown(fills)

    assert breakdown == [
        {"market_slug": "market-a", "fills": 2, "realized_net_pnl": 1.0, "fees": 0.07},
        {"market_slug": "market-b", "fills": 1, "realized_net_pnl": 0.5, "fees": 0.0},
    ]
    assert parse_payload({"payload_json": "not json"}) == {}


def test_committed_paper_run_metrics_are_reproducible() -> None:
    run_root = REPO_ROOT / "tmp" / "core_mm_runs" / "btc-paper-20260315T222135Z"

    metrics, curve, markets = analyze_run(run_root)

    assert metrics["fills"] == 188
    assert metrics["total_pnl"] == pytest.approx(17.11425)
    assert metrics["max_drawdown"] == pytest.approx(6.5268)
    assert metrics["sharpe_status"] == "not_estimated_insufficient_independent_windows"
    assert len(curve) == 1_438
    assert [market["fills"] for market in markets] == [173, 8, 7]
