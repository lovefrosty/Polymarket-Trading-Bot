from __future__ import annotations

import json
from pathlib import Path
import sqlite3

from scripts.report_rotating_single_market_run import build_rotating_single_market_report


def _create_runtime(root: Path, name: str, *, switches: int, risk_action: str = "NORMAL") -> Path:
    runtime_root = root / name
    meta = runtime_root / "meta"
    meta.mkdir(parents=True, exist_ok=True)
    status = {
        "run_id": name,
        "stage": "running",
        "mode": "PAPER",
        "market": "KXBTC-TEST-B68000",
        "run_name": name,
        "updated_at_ms": 1_700_000_000_500,
        "control_state": {
            "trading_enabled": True,
            "kill_switch_enabled": False,
            "flatten_only_mode": False,
        },
    }
    (meta / "status.json").write_text(json.dumps(status), encoding="utf-8")
    db_path = runtime_root / "runtime.db"
    cx = sqlite3.connect(db_path.as_posix())
    try:
        cx.execute("CREATE TABLE system_state (as_of_ts INTEGER, is_frozen INTEGER, reasons TEXT, mode TEXT, payload_json TEXT)")
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
        cx.execute(
            """
            CREATE TABLE decisions (
                ts_ms INTEGER,
                decision_id TEXT,
                market TEXT,
                token_id TEXT,
                action TEXT,
                reason_codes TEXT,
                p_hat REAL,
                expected_edge REAL,
                expected_cost REAL,
                control_state TEXT,
                hedge_action TEXT,
                hedge_cluster_id TEXT,
                hedge_action_reason TEXT,
                hedge_market_id TEXT,
                hedge_target_token_id TEXT,
                hedge_target_side TEXT,
                hedge_preferred_side TEXT,
                hedge_ratio REAL,
                hedge_quality_score REAL,
                hedge_success_window_ms INTEGER,
                hedge_failed_cooldown_until_ms INTEGER,
                policy_json TEXT
            )
            """
        )
        cx.execute(
            """
            CREATE TABLE fills (
                ts_ms INTEGER,
                event_id INTEGER,
                order_id TEXT,
                token_id TEXT,
                side TEXT,
                fill_price REAL,
                fill_qty REAL,
                control_state TEXT,
                hedge_action TEXT,
                hedge_cluster_id TEXT,
                hedge_action_reason TEXT,
                hedge_market_id TEXT,
                hedge_target_token_id TEXT,
                hedge_target_side TEXT,
                hedge_preferred_side TEXT,
                hedge_ratio REAL,
                hedge_quality_score REAL,
                hedge_success_window_ms INTEGER,
                hedge_failed_cooldown_until_ms INTEGER,
                payload_json TEXT
            )
            """
        )

        for i in range(switches + 1):
            market = f"KXBTC-TEST-B68{i:03d}"
            payload = {
                "feed": {"connected": True},
                "runner": {"market_id": market},
                "selection": {
                    "selected_market": {"ticker": market, "reason": "quoteable_book"},
                    "selected_reason": "quoteable_book",
                    "portfolio_selection": {"launch_scope": "single_market", "max_active_markets": 1},
                },
                "active_market_health": {"portfolio_risk": {"gross_exposure": 10.0, "active_positions": 1}},
                "broker_stats": {"realized_net_pnl": 4.0, "unrealized_pnl": 1.0},
            }
            cx.execute(
                "INSERT INTO system_state VALUES (?, ?, ?, ?, ?)",
                (1_700_000_000_000 + i * 1000, 0, "", "PAPER", json.dumps(payload)),
            )
            cx.execute(
                "INSERT INTO decisions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    1_700_000_000_000 + i * 1000,
                    f"d{i}",
                    market,
                    f"{market}:yes",
                    "QUOTE",
                    "",
                    None,
                    0.02,
                    0.003,
                    "UNWIND_ONLY" if risk_action == "FORCE_FLAT" else "NORMAL",
                    "NONE",
                    None,
                    None,
                    None,
                    None,
                    None,
                    None,
                    None,
                    None,
                    None,
                    None,
                    json.dumps({"risk_decision": {"action": risk_action, "risk_state": "stale" if risk_action != "NORMAL" else "normal"}}),
                ),
            )

        cx.execute(
            "INSERT INTO fills VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                1_700_000_000_100,
                1,
                "o1",
                "token_yes",
                "buy",
                0.51,
                5.0,
                "NORMAL",
                "NONE",
                None,
                None,
                None,
                None,
                None,
                None,
                None,
                None,
                None,
                None,
                json.dumps({"fee_source": "paper_kalshi_model", "fee_type": "quadratic", "fee_multiplier": 1.0}),
            ),
        )
        cx.executemany(
            "INSERT INTO paper_pnl VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (1_700_000_000_000, 0.0, -0.1, 0.0, 0.01, 10.0, 0, 1),
                (1_700_000_000_500, 5.5, 4.0, 1.0, 0.5, 50.0, 5, 1),
            ],
        )
        cx.commit()
    finally:
        cx.close()
    return runtime_root


def test_report_rotating_single_market_fixed_market_episode_count(tmp_path: Path) -> None:
    runtime_root = _create_runtime(tmp_path, "fixed", switches=0)
    report = build_rotating_single_market_report(runtime_root)
    assert report["session_selection"]["episode_count"] == 1
    assert report["session_selection"]["market_change_count"] == 0
    assert report["verdict"] == "clean_quote_first"


def test_report_rotating_single_market_classifies_rotation_heavy(tmp_path: Path) -> None:
    runtime_root = _create_runtime(tmp_path, "rotating", switches=11)
    report = build_rotating_single_market_report(runtime_root)
    assert report["session_selection"]["market_change_count"] == 11
    assert report["verdict"] == "profitable_but_rotation_heavy"


def test_report_rotating_single_market_classifies_emergency_exit_dependency(tmp_path: Path) -> None:
    runtime_root = _create_runtime(tmp_path, "stress", switches=2, risk_action="FORCE_FLAT")
    report = build_rotating_single_market_report(runtime_root)
    assert report["session_performance"]["risk_action_counts"]["FORCE_FLAT"] == 3
    assert report["verdict"] == "profit_depended_on_emergency_exits"
