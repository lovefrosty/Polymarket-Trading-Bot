from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from dashboard import data_access as da


def test_get_fills_recent_and_fill_risk_timeline_unpack_risk_metadata(tmp_path: Path) -> None:
    db_path = tmp_path / "runtime.db"
    cx = sqlite3.connect(db_path.as_posix())
    try:
        cx.executescript(
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
            );
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
            );
            CREATE TABLE system_state (
              as_of_ts INTEGER,
              is_frozen INTEGER,
              reasons TEXT,
              mode TEXT,
              payload_json TEXT
            );
            """
        )
        fill_payload = {
            "market_slug": "KXBTC-TEST-B68000",
            "fee_usdc": 0.01,
            "realized_net_pnl_delta": -0.5,
            "placement_metadata": {
                "event_id": "BTC-HOURLY",
                "quote_mode": "risk_exit_stop_loss_maker",
                "risk_action": "STOP_LOSS",
                "risk_state": "stop_loss_exit",
                "stale_state": "stale",
                "exit_mode": "maker",
                "exit_escalation_reason": None,
                "control_state": "HEDGE_ACTIVE",
                "hedge_action": "HEDGE",
                "hedge_cluster_id": "BTC-HOURLY-1",
                "hedge_action_reason": "Reduce yes-heavy inventory",
                "hedge_market_id": "btc-updown-15m-b",
                "hedge_target_token_id": "no_b",
                "hedge_target_side": "buy",
                "hedge_preferred_side": "sell",
                "hedge_ratio": 0.75,
                "hedge_quality_score": 91.5,
                "hedge_success_window_ms": 5000,
                "hedge_failed_cooldown_until_ms": 1_500,
            },
        }
        decision_payload = {
            "risk_decision": {
                "action": "STOP_LOSS",
                "risk_state": "stop_loss_exit",
                "stale_state": "stale",
                "exit_mode": "maker",
                "stop_open_triggered": False,
                "force_flat_triggered": False,
            }
        }
        state_one = {"runner": {"market_id": "KXBTC-TEST-B67900"}}
        state_two = {"runner": {"market_id": "KXBTC-TEST-B68000"}}
        cx.execute(
            "INSERT INTO fills VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                1_000,
                1,
                "o1",
                "token_yes",
                "sell",
                0.46,
                10.0,
                "HEDGE_ACTIVE",
                "HEDGE",
                "BTC-HOURLY-1",
                "Reduce yes-heavy inventory",
                "btc-updown-15m-b",
                "no_b",
                "buy",
                "sell",
                0.75,
                91.5,
                5000,
                1500,
                json.dumps(fill_payload),
            ),
        )
        cx.execute(
            "INSERT INTO decisions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                900,
                "d1",
                "KXBTC-TEST-B68000",
                "token_yes",
                "STOP_LOSS",
                "stop_loss",
                None,
                None,
                None,
                "HEDGE_ACTIVE",
                "HEDGE",
                "BTC-HOURLY-1",
                "Reduce yes-heavy inventory",
                "btc-updown-15m-b",
                "no_b",
                "buy",
                "sell",
                0.75,
                91.5,
                5000,
                1500,
                json.dumps(decision_payload),
            ),
        )
        cx.execute(
            "INSERT INTO system_state VALUES (?, ?, ?, ?, ?)",
            (800, 0, "", "PAPER", json.dumps(state_one)),
        )
        cx.execute(
            "INSERT INTO system_state VALUES (?, ?, ?, ?, ?)",
            (1_100, 0, "", "PAPER", json.dumps(state_two)),
        )
        cx.commit()
    finally:
        cx.close()

    fills_df = da.get_fills_recent(limit=5, db_path=db_path)
    assert not fills_df.empty
    assert fills_df.iloc[0]["risk_action"] == "STOP_LOSS"
    assert fills_df.iloc[0]["quote_mode"] == "risk_exit_stop_loss_maker"
    assert fills_df.iloc[0]["control_state"] == "HEDGE_ACTIVE"
    assert fills_df.iloc[0]["hedge_action"] == "HEDGE"
    assert fills_df.iloc[0]["hedge_cluster_id"] == "BTC-HOURLY-1"
    timeline = da.get_fill_risk_timeline(limit=10, db_path=db_path)
    assert not timeline.empty
    assert set(timeline["event_kind"]) >= {"fill", "risk", "market_switch"}
    assert "control_state" in timeline.columns
    assert "hedge_action" in timeline.columns
    explainer = da.get_decision_explainer_rows(db_path=db_path, limit=5)
    assert not explainer.empty
    assert explainer.iloc[0]["control_state"] == "HEDGE_ACTIVE"
    assert explainer.iloc[0]["hedge_action"] == "HEDGE"
