from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from core_mm.control_plane import ControlCommandStore
from dashboard import data_access as da


def _create_runtime(root: Path) -> Path:
    runtime_root = root / "tmp" / "core_mm_runs" / "strategy-a"
    meta = runtime_root / "meta"
    meta.mkdir(parents=True, exist_ok=True)
    status = {
        "run_id": "strategy-a",
        "stage": "running",
        "mode": "paper",
        "market": "KXBTC-TEST-B68000",
        "run_name": "Strategy A",
        "updated_at_ms": 1_700_000_000_000,
        "config": {
            "safe_risk_profile": "500",
            "strategy_allocated_equity": 500.0,
            "use_allocated_equity_for_risk": True,
            "risk_based_share_sizing": True,
            "trade_size": 5.0,
            "max_size": 20.0,
            "within_pct": 0.1,
            "cycle_secs": 1.0,
            "refresh_market_secs": 60.0,
            "quote_spread_multiplier": 1.0,
        },
    }
    (meta / "status.json").write_text(json.dumps(status), encoding="utf-8")
    (meta / "run_summary.json").write_text(json.dumps({"fills": 2, "placed_orders": 4, "total_pnl": 1.25}), encoding="utf-8")

    db_path = runtime_root / "runtime.db"
    cx = sqlite3.connect(db_path.as_posix())
    try:
        cx.execute("CREATE TABLE system_state (as_of_ts INTEGER, is_frozen INTEGER, reasons TEXT, mode TEXT, payload_json TEXT)")
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
                "flatten_only_mode": False,
                "cycle_secs": 1.0,
                "refresh_market_secs": 60.0,
                "quote_spread_multiplier": 1.0,
                "strategy_allocated_equity": 500.0,
                "safe_risk_profile": "500",
            },
            "broker_stats": {"realized_net_pnl": 1.25, "unrealized_pnl": -0.5},
            "config": status["config"],
        }
        cx.execute(
            "INSERT INTO system_state VALUES (?, ?, ?, ?, ?)",
            (status["updated_at_ms"], 0, "", "PAPER", json.dumps(payload)),
        )
        cx.commit()
    finally:
        cx.close()
    return runtime_root


def test_queue_control_command_and_snapshot(tmp_path: Path) -> None:
    runtime_root = _create_runtime(tmp_path)
    db_path = runtime_root / "runtime.db"

    command_id = da.queue_control_command(
        command_type="pause_trading",
        payload={"reason": "test"},
        db_path=db_path,
    )
    assert command_id.startswith("cmd-")

    commands = da.get_recent_control_commands(db_path=db_path, limit=5)
    assert not commands.empty
    assert commands.iloc[0]["command_type"] == "pause_trading"

    snapshot = da.get_control_plane_snapshot(db_path=db_path)
    assert snapshot["trading_enabled"] is True
    assert snapshot["pending_count"] == 1
    assert snapshot["strategy_allocated_equity"] == 500.0


def test_runtime_alert_feed_surfaces_command_rejections(tmp_path: Path) -> None:
    runtime_root = _create_runtime(tmp_path)
    db_path = runtime_root / "runtime.db"
    store = ControlCommandStore(db_path)
    command_id = store.submit_command(
        run_id="strategy-a",
        runtime_root=runtime_root.as_posix(),
        scope="global",
        command_type="apply_config_patch",
        payload={"patch": {"trade_size": 10.0}},
        requested_by="dashboard",
    )
    store.mark_command(command_id=command_id, status="rejected", event_type="rejected", result={"reason": "test"})

    alerts = da.get_runtime_alert_feed(db_path=db_path)
    assert not alerts.empty
    assert any(alerts["alert_type"].astype(str) == "command_rejected")
