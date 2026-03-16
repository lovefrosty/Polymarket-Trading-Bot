import asyncio
import json
import sqlite3
from pathlib import Path
import time

from core_mm.book_manager import BookManager
from core_mm.market_selector import MarketSelectionConfig, MarketSelector
from core_mm.paper_broker import PaperBroker
from core_mm.runner import CoreMMRunner
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
    books.apply_snapshot("yes", bids=[(0.48, 100)], asks=[(0.52, 100)], ts_ms=1_000)
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
    assert fill["liquidity_mode"] == "taker"
    assert fill["gross_notional"] == 5.2
    assert round(fill["fee_usdc"], 6) == round(5.2 * 25.0 / 10000.0, 6)
    assert fill["placement_metadata"]["quote_mode"] == "cross"
    drained = broker.drain_new_fills()
    assert len(drained) == 1
    assert broker.drain_new_fills() == []


def test_standalone_telemetry_writes_sqlite_and_jsonl(tmp_path: Path) -> None:
    books = BookManager()
    broker = PaperBroker(book_manager=books, fee_bps=25.0, fee_mode="maker")
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
        reverse_position_min_size=2,
    )
    runner.refresh_market_selection([_candidate_event()])
    books.apply_snapshot("yes_test", bids=[(0.49, 150)], asks=[(0.51, 160)], ts_ms=1_000)
    books.apply_snapshot("no_test", bids=[(0.49, 150)], asks=[(0.51, 160)], ts_ms=1_000)
    telemetry = StandaloneTelemetry(
        runtime_root=tmp_path,
        book_manager=books,
        position_tracker=runner.position_tracker,
        mode="PAPER",
    )

    base_now_ms = int(time.time() * 1000)
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
    finally:
        cx.close()

    assert decisions >= 2
    assert fills_count >= 2
    assert pnl_rows >= 2
    assert eq_rows >= 2
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
    assert (tmp_path / "tapes" / "decisions.jsonl").exists()
    assert (tmp_path / "tapes" / "fills.jsonl").exists()

    decision_payload = json.loads(
        sqlite3.connect((tmp_path / "runtime.db").as_posix()).execute(
            "SELECT policy_json FROM decisions ORDER BY ts_ms DESC LIMIT 1"
        ).fetchone()[0]
    )
    assert decision_payload["book_diag"]["state"] == "book_ok"
