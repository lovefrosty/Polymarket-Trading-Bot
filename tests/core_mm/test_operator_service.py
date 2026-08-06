from __future__ import annotations

import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import time

from fastapi.testclient import TestClient

from core_mm.operator_service import OperatorService, create_app


def _create_runtime(root: Path, *, mode: str = "PAPER", run_id: str = "operator-a") -> tuple[Path, str]:
    runtime_root = root / "tmp" / "core_mm_runs" / run_id
    meta = runtime_root / "meta"
    meta.mkdir(parents=True, exist_ok=True)
    status = {
        "run_id": run_id,
        "stage": "running",
        "mode": mode,
        "market": "KXBTC-TEST-B68000",
        "run_name": "Operator A",
        "updated_at_ms": 1_700_000_000_000,
        "control_state": {
            "trading_enabled": True,
            "kill_switch_enabled": False,
            "flatten_only_mode": False,
            "safe_risk_profile": "500",
        },
    }
    (meta / "status.json").write_text(json.dumps(status), encoding="utf-8")
    (meta / "run_summary.json").write_text(json.dumps({"fills": 2, "placed_orders": 4, "total_pnl": 1.25}), encoding="utf-8")
    db_path = runtime_root / "runtime.db"
    cx = sqlite3.connect(db_path.as_posix())
    try:
        cx.execute("CREATE TABLE system_state (as_of_ts INTEGER, is_frozen INTEGER, reasons TEXT, mode TEXT, payload_json TEXT)")
        cx.execute(
            """
            CREATE TABLE control_commands (
                command_id TEXT PRIMARY KEY,
                run_id TEXT NOT NULL,
                runtime_root TEXT NOT NULL,
                scope TEXT NOT NULL,
                command_type TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                requested_by TEXT NOT NULL,
                requested_at_ms INTEGER NOT NULL,
                status TEXT NOT NULL,
                expires_at_ms INTEGER,
                result_json TEXT
            )
            """
        )
        cx.execute(
            """
            CREATE TABLE control_events (
                event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                command_id TEXT NOT NULL,
                ts_ms INTEGER NOT NULL,
                event_type TEXT NOT NULL,
                status TEXT NOT NULL,
                payload_json TEXT NOT NULL
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
        payload = {
            "feed": {"connected": True},
            "runner": {
                "market_id": "KXBTC-TEST-B68000",
                "selection": {
                    "selected_market": {
                        "ticker": "KXBTC-TEST-B68000",
                        "reason": "quoteable_book",
                        "score": 0.42,
                        "spread": 0.02,
                        "liquidity_score": 0.91,
                        "transition_risk": 0.1,
                    },
                    "selected_reason": "quoteable_book",
                    "accepted_candidates": [
                        {"ticker": "KXBTC-TEST-B68000", "score": 0.42, "reason": "quoteable_book"},
                        {"ticker": "KXBTC-TEST-B68100", "score": 0.35, "reason": "quoteable_book"},
                    ],
                    "rejected_candidates": [
                        {"ticker": "KXBTC-TEST-B67900", "score": 0.2, "reason": "price_out_of_range", "blocking_reason": "price_out_of_range"},
                    ],
                    "portfolio_selection": {
                        "launch_scope": "single_market",
                        "max_active_markets": 1,
                        "candidate_decisions": [
                            {"allowed": True, "market_id": "KXBTC-TEST-B68000", "reason": "first_market"},
                            {"allowed": False, "market_id": "KXBTC-TEST-B67900", "reason": "price_out_of_range"},
                        ],
                    },
                },
            },
            "selection": {
                "selected_market": {
                    "ticker": "KXBTC-TEST-B68000",
                    "reason": "quoteable_book",
                    "score": 0.42,
                    "spread": 0.02,
                    "liquidity_score": 0.91,
                    "transition_risk": 0.1,
                },
                "selected_reason": "quoteable_book",
                "accepted_candidates": [
                    {"ticker": "KXBTC-TEST-B68000", "score": 0.42, "reason": "quoteable_book"},
                ],
                "rejected_candidates": [
                    {"ticker": "KXBTC-TEST-B67900", "score": 0.2, "reason": "price_out_of_range", "blocking_reason": "price_out_of_range"},
                ],
                "portfolio_selection": {
                    "launch_scope": "single_market",
                    "max_active_markets": 1,
                    "candidate_decisions": [
                        {"allowed": True, "market_id": "KXBTC-TEST-B68000", "reason": "first_market"},
                    ],
                },
            },
            "active_market_health": {
                "portfolio_risk": {"gross_exposure": 20.0, "active_positions": 1},
            },
            "control_state": status["control_state"],
            "broker_stats": {"realized_net_pnl": 1.25, "unrealized_pnl": -0.5},
        }
        cx.execute(
            "INSERT INTO system_state VALUES (?, ?, ?, ?, ?)",
            (status["updated_at_ms"], 0, "", mode, json.dumps(payload)),
        )
        switched_payload = json.loads(json.dumps(payload))
        switched_payload["runner"]["market_id"] = "KXBTC-TEST-B68100"
        switched_payload["runner"]["selection"]["selected_market"]["ticker"] = "KXBTC-TEST-B68100"
        switched_payload["runner"]["selection"]["selected_reason"] = "better_score"
        switched_payload["selection"]["selected_market"]["ticker"] = "KXBTC-TEST-B68100"
        switched_payload["selection"]["selected_reason"] = "better_score"
        cx.execute(
            "INSERT INTO system_state VALUES (?, ?, ?, ?, ?)",
            (status["updated_at_ms"] + 100, 0, "", mode, json.dumps(switched_payload)),
        )
        cx.execute(
            "INSERT INTO system_state VALUES (?, ?, ?, ?, ?)",
            (status["updated_at_ms"] + 200, 0, "", mode, json.dumps(payload)),
        )
        cx.execute(
            "INSERT INTO fills VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (1_000, 1, "o1", "token_yes", "buy", 0.51, 5.0, "NORMAL", "NONE", None, None, None, None, None, None, None, None, None, None, json.dumps({"market_slug": "KXBTC-TEST-B68000", "fee_source": "paper_kalshi_model", "fee_type": "quadratic", "fee_multiplier": 1.0, "realized_net_pnl_delta": 0.12})),
        )
        cx.execute(
            "INSERT INTO decisions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (900, "d1", "KXBTC-TEST-B68000", "token_yes", "QUOTE", "", None, 0.02, 0.003, "NORMAL", "NONE", None, None, None, None, None, None, None, None, None, None, json.dumps({"size_plan": {"buy_amount": 5.0, "sell_amount": 0.0, "buy_limiter": "trade_size", "sell_limiter": "inventory", "buy_limiters": "trade_size", "sell_limiters": "inventory"}, "risk_decision": {"action": "NORMAL", "risk_state": "normal"}, "desired_quotes": [{"metadata": {"p_fair": 0.54, "fee_type": "quadratic", "fee_multiplier": 1.0}}]})),
        )
        cx.execute(
            "INSERT INTO decisions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (950, "d2", "KXBTC-TEST-B68100", "token_no", "SKIP", "market_switch", None, 0.015, 0.002, "UNWIND_ONLY", "UNWIND", None, None, None, None, None, None, None, None, None, None, json.dumps({"size_plan": {"buy_amount": 0.0, "sell_amount": 4.0, "buy_limiter": "inventory", "sell_limiter": "trade_size", "buy_limiters": "inventory", "sell_limiters": "trade_size"}, "risk_decision": {"action": "STALE_UNWIND", "risk_state": "stale"}, "desired_quotes": [{"metadata": {"p_fair": 0.46, "fee_type": "quadratic", "fee_multiplier": 1.0}}]})),
        )
        cx.executemany(
            "INSERT INTO paper_pnl VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (800, 1.0, 0.8, 0.1, 0.01, 10.0, 1, 0),
                (1_000, 1.0, 1.25, -0.5, 0.02, 12.0, 1, 1),
            ],
        )
        cx.commit()
    finally:
        cx.close()
    return runtime_root, run_id


def test_operator_service_discovers_runtimes_and_builds_snapshot(tmp_path: Path) -> None:
    _, run_id = _create_runtime(tmp_path)
    service = OperatorService(repo_root=tmp_path)

    runtimes = service.discover_runtimes()
    assert runtimes
    assert runtimes[0]["run_id"] == run_id

    snapshot = service.build_operator_snapshot(run_id)
    assert snapshot["runtime"]["run_id"] == run_id
    assert snapshot["market"]["selected_market"] == "KXBTC-TEST-B68000"
    assert snapshot["portfolio"]["total_pnl"] == 0.75
    assert snapshot["health"]["feed_connected"] is True
    assert snapshot["recent"]["fills"][0]["market_slug"] == "KXBTC-TEST-B68000"
    assert snapshot["market"]["selection"]["launch_scope"] == "single_market"
    assert snapshot["decision"]["current"]["p_fair"] == 0.46
    assert snapshot["decision"]["current"]["risk_action"] == "STALE_UNWIND"
    assert snapshot["session"]["selection"]["episode_count"] == 3
    assert snapshot["session"]["selection"]["market_change_count"] == 2
    assert snapshot["session"]["performance"]["latest_fill_fee"]["fee_source"] == "paper_kalshi_model"


def test_operator_service_queues_valid_command_and_rejects_live_patch(tmp_path: Path) -> None:
    _, paper_run_id = _create_runtime(tmp_path, mode="PAPER", run_id="operator-paper")
    live_root, live_run_id = _create_runtime(tmp_path, mode="LIVE", run_id="operator-live")
    live_meta = live_root / "meta" / "status.json"
    status = json.loads(live_meta.read_text())
    status["run_id"] = live_run_id
    live_meta.write_text(json.dumps(status), encoding="utf-8")
    service = OperatorService(repo_root=tmp_path)
    app = create_app(service)
    client = TestClient(app)

    response = client.post(f"/api/runtimes/{paper_run_id}/commands", json={"command_type": "pause_trading"})
    assert response.status_code == 200
    assert response.json()["command_id"].startswith("cmd-")

    bad = client.post(
        f"/api/runtimes/{live_run_id}/commands",
        json={"command_type": "apply_config_patch", "payload": {"patch": {"trade_size": 10.0}}},
    )
    assert bad.status_code == 400
    assert "command_not_allowed:apply_config_patch" in bad.json()["detail"]


def test_operator_service_start_and_stop_managed_runtime(tmp_path: Path) -> None:
    def _fake_builder(_request, _runtime_root: Path) -> list[str]:
        return [sys.executable, "-c", "import time; time.sleep(60)"]

    service = OperatorService(repo_root=tmp_path, command_builder=_fake_builder)
    app = create_app(service)
    client = TestClient(app)

    started = client.post("/api/runtimes/start", json={"symbol": "BTC", "safe_risk_profile": "500"})
    assert started.status_code == 200
    runtime = started.json()["runtime"]
    assert runtime["managed"] is True
    assert runtime["pid"] is not None

    stopped = client.post(f"/api/runtimes/{runtime['run_id']}/stop", json={"force_kill_after_ms": 500})
    assert stopped.status_code == 200
    assert stopped.json()["status"] in {"terminated", "killed", "already_stopped"}


def test_operator_service_websocket_emits_runtime_and_command_events(tmp_path: Path) -> None:
    _, run_id = _create_runtime(tmp_path)
    service = OperatorService(repo_root=tmp_path)
    app = create_app(service)
    client = TestClient(app)

    with client.websocket_connect(f"/ws/runtimes/{run_id}") as websocket:
        first = websocket.receive_json()
        second = websocket.receive_json()
        events = {first["event"], second["event"]}
        assert "runtime_status" in events
        assert "portfolio_status" in events
        runtime_payload = first["data"] if first["event"] == "runtime_status" else second["data"]
        assert "decision" in runtime_payload
        assert "session" in runtime_payload


def test_operator_service_history_endpoint_returns_durable_series(tmp_path: Path) -> None:
    _, run_id = _create_runtime(tmp_path)
    service = OperatorService(repo_root=tmp_path)
    app = create_app(service)
    client = TestClient(app)

    response = client.get(f"/api/runtimes/{run_id}/history?points=5")
    assert response.status_code == 200
    payload = response.json()
    assert payload["run_id"] == run_id
    assert len(payload["points"]) >= 1
    assert payload["points"][-1]["total_pnl"] == 0.75
