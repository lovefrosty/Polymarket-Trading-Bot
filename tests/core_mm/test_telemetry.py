import asyncio
import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace
import time

from core_mm.book_manager import BookManager
from core_mm.book_metrics import BookDiagnostic, MeaningfulBBO
from core_mm.main_loop import MarketCycleResult, TokenCycleDecision
from core_mm.market_selector import MarketSelectionConfig, MarketSelector
from core_mm.order_manager import DesiredQuote
from core_mm.paper_broker import PaperBroker
from core_mm.positions import PositionTracker
from core_mm.runner import RunnerStatus
from core_mm.runner import CoreMMRunner
from core_mm.risk_manager import RiskDecision
from core_mm.telemetry import StandaloneTelemetry


def _candidate_event():
    return {
        "slug": "btc-updown-15m-test",
        "conditionId": "cond-1",
        "clobTokenIds": ["yes_test", "no_test"],
        "active": True,
        "closed": False,
        "accepting_orders": True,
        "volatility_sum": 3,
        "spread": 0.03,
        "prices": [0.48, 0.52],
        "reward_per_100": 5,
    }


def test_paper_broker_records_fee_aware_fill_details() -> None:
    books = BookManager()
    books.apply_snapshot("yes", bids=[(0.48, 100)], asks=[(0.52, 100)], ts_ms=int(time.time() * 1000))
    broker = PaperBroker(book_manager=books, fee_bps=25.0, fee_mode="taker")
    result = broker.place_order(
        token_id="yes",
        side="buy",
        price=0.52,
        size=10,
        client_order_id="q1",
        quote_group_id="m1",
        metadata={"quote_mode": "cross", "mid": 0.50},
    )
    assert result.success
    fill = result.payload["fill"]
    assert fill["fee_bps"] == 25.0
    assert fill["fee_source"] == "paper_flat_bps"
    assert fill["liquidity_mode"] == "taker"
    assert fill["gross_notional"] == 5.2
    assert round(fill["fee_usdc"], 6) == round(5.2 * 25.0 / 10000.0, 6)
    assert fill["placement_metadata"]["quote_mode"] == "cross"
    drained = broker.drain_new_fills()
    assert len(drained) == 1
    assert broker.drain_new_fills() == []


def test_standalone_telemetry_writes_sqlite_and_jsonl(tmp_path: Path) -> None:
    books = BookManager()
    broker = PaperBroker(book_manager=books, fee_bps=25.0, fee_mode="maker", min_queue_wait_ms=0)
    runner = CoreMMRunner(
        market_selector=MarketSelector(
            config=MarketSelectionConfig(require_clob_candidate=False, current_window_only=False)
        ),
        book_manager=books,
        broker=broker,
        mode="PAPER",
        min_size=10,
        fallback_size=2,
        within_pct=0.06,
        trade_size=12,
        max_size=150,
    )
    runner.refresh_market_selection([_candidate_event()])
    base_now_ms = int(time.time() * 1000)
    books.apply_snapshot("yes_test", bids=[(0.49, 150)], asks=[(0.51, 160)], ts_ms=base_now_ms - 1_000)
    books.apply_snapshot("no_test", bids=[(0.49, 150)], asks=[(0.51, 160)], ts_ms=base_now_ms - 1_000)
    telemetry = StandaloneTelemetry(
        runtime_root=tmp_path,
        book_manager=books,
        position_tracker=runner.position_tracker,
        mode="PAPER",
    )
    result = asyncio.run(runner.run_cycle(now_ms=base_now_ms, usdc_balance=2500))
    assert result is not None
    broker.sweep_fills()
    fills = broker.drain_new_fills()
    telemetry.record_fill_events(now_ms=base_now_ms, market_slug=runner.current_market.slug, fill_events=fills, broker_stats=broker.stats())
    telemetry.record_cycle(
        now_ms=base_now_ms,
        runner=runner,
        result=result,
        feed_status={"connected": True, "subscribed_token_ids": list(runner.current_market.token_ids), "received_messages": 10, "applied_book_updates": 20},
        last_error=None,
        config={"fee_bps": 25.0, "fee_mode": "maker"},
    )
    books.apply_snapshot("yes_test", bids=[(0.50, 150)], asks=[(0.52, 160)], ts_ms=base_now_ms + 8_000)
    books.apply_snapshot("no_test", bids=[(0.50, 150)], asks=[(0.52, 160)], ts_ms=base_now_ms + 8_000)
    telemetry.process_markouts(now_ms=base_now_ms + 8_000)
    telemetry.close()

    cx = sqlite3.connect((tmp_path / "runtime.db").as_posix())
    try:
        decisions = cx.execute("SELECT COUNT(*) FROM decisions").fetchone()[0]
        fills_count = cx.execute("SELECT COUNT(*) FROM fills").fetchone()[0]
        pnl_rows = cx.execute("SELECT COUNT(*) FROM paper_pnl").fetchone()[0]
        eq_rows = cx.execute("SELECT COUNT(*) FROM execution_quality").fetchone()[0]
        fill_payload = json.loads(
            cx.execute("SELECT payload_json FROM fills ORDER BY ts_ms DESC LIMIT 1").fetchone()[0]
        )
        eq_payload = json.loads(
            cx.execute("SELECT payload_json FROM execution_quality ORDER BY ts_ms DESC LIMIT 1").fetchone()[0]
        )
        system_state_payload = json.loads(
            cx.execute("SELECT payload_json FROM system_state ORDER BY as_of_ts DESC LIMIT 1").fetchone()[0]
        )
    finally:
        cx.close()

    assert decisions >= 2
    assert fills_count >= 2
    assert pnl_rows >= 2
    assert eq_rows >= 2
    assert fill_payload["fee_source"] == "paper_flat_bps"
    assert eq_payload["fee_source"] == "paper_flat_bps"
    summary = json.loads((tmp_path / "meta" / "run_summary.json").read_text())
    assert summary["runtime_db_path"].endswith("runtime.db")
    assert summary["fills"] >= 2
    assert summary["placed_orders"] >= summary["fills"]
    assert summary["fill_rate"] >= 0.0
    assert summary["total_pnl"] == summary["realized_net_pnl"] + summary["unrealized_pnl"]
    assert summary["cycle_summary"]["quoteable_cycles"] >= 1
    assert summary["cycle_summary"]["freeze_cycles"] == 0
    assert summary["execution_quality"]["fills_measured"] >= 2
    assert summary["execution_quality"]["avg_realized_spread_bps"] is not None
    assert summary["execution_quality"]["avg_markout_1s_bps"] is not None
    assert summary["execution_quality"]["avg_markout_5s_bps"] is not None
    assert summary["phase0_acceptance"]["result"] in {"pass", "tunable_loss", "needs_review", "structural_blocker"}
    assert summary["phase0_acceptance"]["quoteable_cycles_present"] is True
    assert summary["phase0_acceptance"]["fills_present"] is True
    assert "risk_proof" in summary
    assert "hedge_summary" in summary
    assert "hedge_candidate_summary" in summary
    assert summary["risk_proof"]["flatten_only_cycles"] >= 0
    assert summary["risk_proof"]["kill_switch_applied_commands"] >= 0
    assert "selection" in system_state_payload
    assert "active_market_health" in system_state_payload
    assert "cluster_exposure" in system_state_payload
    assert "cluster_hedge" in system_state_payload
    assert system_state_payload["runner"]["selection"] == system_state_payload["selection"]
    assert system_state_payload["runner"]["active_market_health"] == system_state_payload["active_market_health"]
    assert system_state_payload["runner"]["cluster_exposure"] == system_state_payload["cluster_exposure"]
    assert system_state_payload["runner"]["cluster_hedge"] == system_state_payload["cluster_hedge"]
    assert (tmp_path / "tapes" / "decisions.jsonl").exists()
    assert (tmp_path / "tapes" / "fills.jsonl").exists()
    assert (tmp_path / "tapes" / "hedge_candidates.jsonl").exists()

    decision_payload = json.loads(
        sqlite3.connect((tmp_path / "runtime.db").as_posix()).execute(
            "SELECT policy_json FROM decisions ORDER BY ts_ms DESC LIMIT 1"
        ).fetchone()[0]
    )
    assert decision_payload["book_diag"]["state"] == "book_ok"
    assert "buy_limiter" in decision_payload["size_plan"]
    assert "sell_limiter" in decision_payload["size_plan"]
    assert "buy_limiters" in decision_payload["size_plan"]
    assert "sell_limiters" in decision_payload["size_plan"]


def test_standalone_telemetry_persists_hedge_control_metadata(tmp_path: Path) -> None:
    books = BookManager()
    now_ms = int(time.time() * 1000)
    books.apply_snapshot("token_yes", bids=[(0.48, 120)], asks=[(0.52, 140)], ts_ms=now_ms - 1_000)
    tracker = PositionTracker()
    tracker.set_position("token_yes", size=5.0, avg_price=0.50)
    telemetry = StandaloneTelemetry(
        runtime_root=tmp_path,
        book_manager=books,
        position_tracker=tracker,
        mode="PAPER",
    )

    book_diag = BookDiagnostic(
        state="book_ok",
        bid_levels=12,
        ask_levels=12,
        best_bid=0.48,
        best_ask=0.52,
        best_bid_size=120.0,
        best_ask_size=140.0,
        min_size=2.0,
        fallback_size=2.0,
        last_update_ms=now_ms - 1_000,
        book_age_ms=1_000,
    )
    metrics = MeaningfulBBO(
        best_bid=0.48,
        best_bid_size=120.0,
        second_bid=0.47,
        best_ask=0.52,
        best_ask_size=140.0,
        second_ask=0.53,
        top_bid=0.48,
        top_ask=0.52,
        bid_sum_within_n_percent=120.0,
        ask_sum_within_n_percent=140.0,
        min_size_used=2.0,
    )
    hedge_metadata = {
        "market_id": "KXBTC-26MAR2317-B70675",
        "quote_mode": "risk_exit_stale_unwind_maker",
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
        "hedge_success_window_ms": 5_000,
        "hedge_failed_cooldown_until_ms": now_ms + 30_000,
    }
    decision = TokenCycleDecision(
        token_id="token_yes",
        book_diag=book_diag,
        metrics=metrics,
        flow_filter=None,
        quote_plan=None,
        size_plan=None,
        risk_decision=RiskDecision(action="NORMAL", allow_buy=True, allow_sell=True, reasons=[]),
        desired_quotes=(
            DesiredQuote(
                quote_key="q1",
                token_id="token_yes",
                side="buy",
                price=0.48,
                size=4.0,
                metadata=hedge_metadata,
            ),
        ),
    )
    result = MarketCycleResult(
        market_id="KXBTC-26MAR2317-B70675",
        token_decisions=(decision,),
        desired_quotes={"q1": decision.desired_quotes[0]},
        order_actions=(),
        execution_results=(),
    )

    class _StubBroker:
        def stats(self) -> dict:
            return {
                "realized_gross_pnl": 0.0,
                "realized_net_pnl": 0.0,
                "cumulative_fees": 0.0,
                "turnover": 0.0,
                "win_count": 0,
                "loss_count": 0,
            }

    class _StubRunner:
        def __init__(self) -> None:
            self.current_market = SimpleNamespace(slug="KXBTC-26MAR2317-B70675", token_ids=("token_yes",))
            self.position_tracker = tracker
            self.broker = _StubBroker()
            self.merge_stats = {}
            self.main_loop = SimpleNamespace(flow_stats={})
            self.per_token_quote_stats = {}

        def status(self) -> RunnerStatus:
            return RunnerStatus(
                mode="PAPER",
                market_id="KXBTC-26MAR2317-B70675",
                market_ids=("KXBTC-26MAR2317-B70675",),
                token_ids=("token_yes",),
                has_books=True,
                book_diag={"token_yes": book_diag.as_dict()},
                selection={},
                active_market_health={},
                cluster_exposure={
                    "payload": {
                        "clusters": [
                            {
                                "cluster_id": "BTC-HOURLY-1",
                                "control_state": "HEDGE_ACTIVE",
                                "hedge_action": "HEDGE",
                                "hedge_action_reason": "Reduce yes-heavy inventory",
                                "hedge_target_market": "btc-updown-15m-b",
                                "hedge_target_token": "no_b",
                                "hedge_target_side": "buy",
                                "dominant_inventory_market_quality_score": 88.0,
                            }
                        ]
                    }
                },
                cluster_hedge={
                    "enabled": True,
                    "paper_only": True,
                    "clusters": [
                        {
                            "cluster_id": "BTC-HOURLY-1",
                            "control_state": "HEDGE_ACTIVE",
                            "action": "HEDGE",
                            "hedge_action_reason": "Reduce yes-heavy inventory",
                            "hedge_target_market": "btc-updown-15m-b",
                            "hedge_target_token": "no_b",
                            "hedge_target_side": "buy",
                            "candidate_summary": {
                                "cluster_id": "BTC-HOURLY-1",
                                "candidate_count": 1,
                                "accepted_count": 1,
                                "rejection_counts": {},
                                "best_candidate": {
                                    "market_id": "btc-updown-15m-b",
                                    "token_id": "no_b",
                                    "quality_score": 91.5,
                                    "quality_gap": 3.5,
                                },
                                "search_profile": "production",
                                "proof_only_lane": False,
                                "proof_only_bucket_distance": 2,
                                "proof_only_expiry_slack_ms": 60_000,
                            },
                            "hedge_quality_score": 91.5,
                            "hedge_quality_gap": 3.5,
                            "candidate_state": "accepted",
                            "inventory_market_quality_score": 88.0,
                        }
                    ],
                },
                control_state={
                    "trading_enabled": True,
                    "kill_switch_enabled": False,
                    "flatten_only_mode": False,
                    "halt_after_flatten": False,
                },
            )

    runner = _StubRunner()
    telemetry.record_cycle(
        now_ms=now_ms,
        runner=runner,
        result=result,
        feed_status={"connected": True, "subscribed_token_ids": ["token_yes"]},
        last_error=None,
        config={"mode": "PAPER"},
    )
    fill = {
        "order_id": "o1",
        "token_id": "token_yes",
        "side": "sell",
        "size": 2.0,
        "price": 0.51,
        "ts_ms": now_ms + 1_000,
        "placed_at_ms": now_ms,
        "gross_notional": 1.02,
        "fee_bps": 25.0,
        "fee_usdc": 0.0,
        "fee_source": "exchange_reported",
        "net_notional": 1.02,
        "liquidity_mode": "maker",
        "fill_trigger": "touch",
        "realized_gross_pnl_delta": 0.02,
        "realized_net_pnl_delta": 0.02,
        "inventory_after_fill": {"size": 3.0, "avg_price": 0.50},
        "placement_metadata": dict(hedge_metadata),
    }
    telemetry.record_fill_events(now_ms=now_ms + 1_000, market_slug="KXBTC-26MAR2317-B70675", fill_events=[fill], broker_stats=runner.broker.stats())
    telemetry.close()

    cx = sqlite3.connect((tmp_path / "runtime.db").as_posix())
    try:
        decision_cols = {row[1] for row in cx.execute("PRAGMA table_info(decisions)").fetchall()}
        fill_cols = {row[1] for row in cx.execute("PRAGMA table_info(fills)").fetchall()}
        hedge_cols = {row[1] for row in cx.execute("PRAGMA table_info(hedge_candidates)").fetchall()}
        assert {"control_state", "hedge_action", "hedge_cluster_id", "hedge_target_token_id", "hedge_target_side"} <= decision_cols
        assert {"control_state", "hedge_action", "hedge_cluster_id", "hedge_target_token_id", "hedge_target_side"} <= fill_cols
        assert {"candidate_state", "hedge_quality_gap", "quality_gap_state"} <= hedge_cols
        decision_row = cx.execute(
            "SELECT control_state, hedge_action, hedge_cluster_id, hedge_action_reason, hedge_market_id, hedge_target_token_id, hedge_target_side FROM decisions ORDER BY ts_ms DESC LIMIT 1"
        ).fetchone()
        fill_row = cx.execute(
            "SELECT control_state, hedge_action, hedge_cluster_id, hedge_action_reason, hedge_market_id, hedge_target_token_id, hedge_target_side FROM fills ORDER BY ts_ms DESC LIMIT 1"
        ).fetchone()
        hedge_row = cx.execute(
            "SELECT candidate_state, hedge_quality_score, inventory_market_quality_score, hedge_quality_gap, quality_gap_state FROM hedge_candidates ORDER BY ts_ms DESC LIMIT 1"
        ).fetchone()
    finally:
        cx.close()

    assert decision_row == (
        "HEDGE_ACTIVE",
        "HEDGE",
        "BTC-HOURLY-1",
        "Reduce yes-heavy inventory",
        "btc-updown-15m-b",
        "no_b",
        "buy",
    )
    assert fill_row == (
        "HEDGE_ACTIVE",
        "HEDGE",
        "BTC-HOURLY-1",
        "Reduce yes-heavy inventory",
        "btc-updown-15m-b",
        "no_b",
        "buy",
    )
    assert hedge_row == ("accepted", 91.5, 88.0, 3.5, "positive")
    summary = json.loads((tmp_path / "meta" / "run_summary.json").read_text())
    assert summary["hedge_summary"]["decision_control_states"]["HEDGE_ACTIVE"] == 1
    assert summary["hedge_summary"]["fill_control_states"]["HEDGE_ACTIVE"] == 1
    assert summary["hedge_summary"]["cluster_control_states"]["HEDGE_ACTIVE"] == 1
    assert summary["hedge_summary"]["decision_target_presence"] == 1
    assert summary["hedge_summary"]["fill_target_presence"] == 1
    assert summary["hedge_candidate_summary"]["accepted_clusters"] == 1
    assert summary["hedge_candidate_summary"]["rejected_clusters"] == 0
    assert summary["hedge_candidate_summary"]["quality_gap_positive"] == 1


def test_telemetry_records_book_snapshots(tmp_path: Path) -> None:
    books = BookManager()
    broker = PaperBroker(book_manager=books, fee_bps=25.0, fee_mode="maker", min_queue_wait_ms=0)
    runner = CoreMMRunner(
        market_selector=MarketSelector(
            config=MarketSelectionConfig(require_clob_candidate=False, current_window_only=False)
        ),
        book_manager=books,
        broker=broker,
        mode="PAPER",
        min_size=10,
        fallback_size=2,
        within_pct=0.06,
        trade_size=12,
        max_size=150,
    )
    runner.refresh_market_selection([_candidate_event()])
    now_ms = int(time.time() * 1000)
    books.apply_snapshot(
        "yes_test",
        bids=[(0.49, 150), (0.48, 200), (0.47, 300)],
        asks=[(0.51, 160), (0.52, 250)],
        ts_ms=now_ms - 1_000,
    )
    books.apply_snapshot(
        "no_test",
        bids=[(0.49, 100)],
        asks=[(0.51, 120)],
        ts_ms=now_ms - 1_000,
    )
    telemetry = StandaloneTelemetry(
        runtime_root=tmp_path,
        book_manager=books,
        position_tracker=runner.position_tracker,
        mode="PAPER",
    )
    result = asyncio.run(runner.run_cycle(now_ms=now_ms, usdc_balance=2500))
    telemetry.record_cycle(
        now_ms=now_ms,
        runner=runner,
        result=result,
        feed_status={"connected": True},
        last_error=None,
        config={},
    )
    telemetry.close()

    cx = sqlite3.connect((tmp_path / "runtime.db").as_posix())
    try:
        # Check book_snapshots table was created and populated
        total = cx.execute("SELECT COUNT(*) FROM book_snapshots").fetchone()[0]
        assert total > 0, "Expected book snapshot rows"

        # Should have bids + asks for both tokens
        yes_bids = cx.execute(
            "SELECT COUNT(*) FROM book_snapshots WHERE token_id='yes_test' AND side='bid'"
        ).fetchone()[0]
        yes_asks = cx.execute(
            "SELECT COUNT(*) FROM book_snapshots WHERE token_id='yes_test' AND side='ask'"
        ).fetchone()[0]
        assert yes_bids == 3  # 3 bid levels
        assert yes_asks == 2  # 2 ask levels

        no_rows = cx.execute(
            "SELECT COUNT(*) FROM book_snapshots WHERE token_id='no_test'"
        ).fetchone()[0]
        assert no_rows == 2  # 1 bid + 1 ask
    finally:
        cx.close()
