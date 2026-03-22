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
