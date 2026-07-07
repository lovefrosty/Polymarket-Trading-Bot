from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from scripts.run_overnight_protocol import _one_iteration
from core_mm.overnight_protocol import OvernightProtocolConfig


def _create_runtime(root: Path) -> Path:
    runtime_root = root / "tmp" / "core_mm_runs" / "overnight-a"
    meta = runtime_root / "meta"
    meta.mkdir(parents=True, exist_ok=True)
    status = {
        "run_id": "overnight-a",
        "stage": "running",
        "mode": "paper",
        "market": "KXBTC-TEST-B68000",
        "run_name": "Overnight A",
        "updated_at_ms": 1_700_000_000_000,
        "config": {
            "trade_size": 5.0,
            "max_size": 20.0,
            "within_pct": 0.1,
            "cycle_secs": 1.0,
            "refresh_market_secs": 60.0,
            "quote_spread_multiplier": 1.0,
        },
        "control_state": {"trading_enabled": True, "kill_switch_enabled": False},
    }
    summary = {
        "fills": 2,
        "placed_orders": 4,
        "total_pnl": 1.25,
        "realized_net_pnl": 1.0,
        "unrealized_pnl": 0.25,
        "updated_at_ms": 1_700_000_000_000,
    }
    (meta / "status.json").write_text(json.dumps(status), encoding="utf-8")
    (meta / "run_summary.json").write_text(json.dumps(summary), encoding="utf-8")

    db_path = runtime_root / "runtime.db"
    cx = sqlite3.connect(db_path.as_posix())
    try:
        cx.execute("CREATE TABLE system_state (as_of_ts INTEGER, is_frozen INTEGER, reasons TEXT, mode TEXT, payload_json TEXT)")
        cx.execute("CREATE TABLE paper_pnl (ts_ms INTEGER, realized_gross_pnl REAL, realized_net_pnl REAL, unrealized_pnl REAL, cumulative_fees REAL, turnover REAL, win_count INTEGER, loss_count INTEGER)")
        payload = {
            "runner": {
                "has_books": True,
                "market_id": "KXBTC-TEST-B68000",
                "book_diag": {"tokens_ok": 1, "tokens_blocked": 0, "per_token": {"yes": {"best_bid": 0.49, "best_ask": 0.51}}},
            },
            "active_market_health": {
                "event_id": "KXBTC-TEST",
                "book_health": "healthy",
                "quoteable": True,
                "portfolio_risk": {"gross_exposure": 20.0, "unrealized_pnl": -0.5, "active_positions": 1},
                "broker_stats": {"realized_net_pnl": 1.25},
            },
            "control_state": {
                "trading_enabled": True,
                "kill_switch_enabled": False,
                "cycle_secs": 1.0,
                "refresh_market_secs": 60.0,
                "quote_spread_multiplier": 1.0,
            },
            "broker_stats": {"realized_net_pnl": 1.25, "unrealized_pnl": -0.5},
            "config": status["config"],
        }
        cx.execute(
            "INSERT INTO system_state VALUES (?, ?, ?, ?, ?)",
            (status["updated_at_ms"], 0, "", "PAPER", json.dumps(payload)),
        )
        cx.execute(
            "INSERT INTO paper_pnl VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (status["updated_at_ms"], 1.0, 1.0, 0.25, 0.0, 10.0, 1, 0),
        )
        cx.commit()
    finally:
        cx.close()
    return runtime_root


def test_one_iteration_writes_protocol_files(tmp_path: Path) -> None:
    runtime_root = _create_runtime(tmp_path)
    state_path = runtime_root / "meta" / "overnight_protocol_state.json"
    event_log_path = runtime_root / "meta" / "overnight_protocol_events.jsonl"

    payload = _one_iteration(
        runtime_root,
        config=OvernightProtocolConfig(auto_safe_restart=False),
        state_path=state_path,
        event_log_path=event_log_path,
    )

    assert payload["runtime_root"] == runtime_root.as_posix()
    assert payload["protocol_observations"]["protocol_mode"] == "live_safe"
    assert state_path.exists()
    assert event_log_path.exists()
    assert (runtime_root / "meta" / "overnight_protocol_latest.json").exists()
