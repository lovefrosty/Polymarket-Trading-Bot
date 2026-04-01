from __future__ import annotations

import asyncio
import json
from pathlib import Path
import time
from typing import Any, Dict, Optional

from core_mm.book_manager import BookManager
from core_mm.market_selector import MarketCandidate, MarketSelectionConfig, MarketSelector
from core_mm.paper_broker import PaperBroker
from core_mm.positions import PositionTracker
from core_mm.risk_manager import RiskConfig
from core_mm.runner import CoreMMRunner
from core_mm.telemetry import StandaloneTelemetry


def run_safe_first_risk_harness(runtime_root: Path) -> Dict[str, Any]:
    runtime_root = Path(runtime_root)
    runtime_root.mkdir(parents=True, exist_ok=True)
    base_now_ms = int(time.time() * 1000)
    _run_scenario(runtime_root=runtime_root, base_now_ms=base_now_ms, scenario=_run_cross_escalation_scenario)
    _run_scenario(runtime_root=runtime_root, base_now_ms=base_now_ms + 120_000, scenario=_run_force_flat_scenario)
    _run_scenario(runtime_root=runtime_root, base_now_ms=base_now_ms + 240_000, scenario=_run_day_loss_flatten_scenario)

    summary_path = runtime_root / "meta" / "run_summary.json"
    summary = json.loads(summary_path.read_text()) if summary_path.exists() else {}
    return {
        "runtime_root": runtime_root.as_posix(),
        "runtime_db_path": (runtime_root / "runtime.db").as_posix(),
        "summary": summary,
    }


def run_proof_only_hedge_harness(runtime_root: Path) -> Dict[str, Any]:
    runtime_root = Path(runtime_root)
    runtime_root.mkdir(parents=True, exist_ok=True)
    base_now_ms = int(time.time() * 1000)
    _run_scenario(
        runtime_root=runtime_root,
        base_now_ms=base_now_ms,
        scenario=_run_proof_only_crypto_hedge_scenario,
        runner_kwargs={
            "max_active_markets": 3,
            "hedge_search_profile": "proof-only",
            "proof_only_bucket_distance": 2,
            "proof_only_expiry_slack_ms": 60_000,
            "strategy_allocated_equity": 1_000.0,
        },
    )
    summary_path = runtime_root / "meta" / "run_summary.json"
    summary = json.loads(summary_path.read_text()) if summary_path.exists() else {}
    return {
        "runtime_root": runtime_root.as_posix(),
        "runtime_db_path": (runtime_root / "runtime.db").as_posix(),
        "summary": summary,
    }


def _build_harness_runner(
    runtime_root: Path,
    *,
    max_active_markets: int = 1,
    hedge_search_profile: str = "production",
    proof_only_bucket_distance: int = 2,
    proof_only_expiry_slack_ms: int = 60_000,
    strategy_allocated_equity: float = 100.0,
) -> tuple[CoreMMRunner, StandaloneTelemetry]:
    books = BookManager()
    positions = PositionTracker()
    broker = PaperBroker(
        book_manager=books,
        position_tracker=positions,
        fee_bps=25.0,
        fee_mode="maker",
        min_queue_wait_ms=0,
        queue_depth_fraction=0.0,
    )
    runner = CoreMMRunner(
        market_selector=MarketSelector(
            config=MarketSelectionConfig(require_clob_candidate=False, current_window_only=False)
        ),
        book_manager=books,
        position_tracker=positions,
        broker=broker,
        mode="PAPER",
        min_size=5.0,
        fallback_size=2.0,
        within_pct=0.10,
        trade_size=20.0,
        max_size=100.0,
        market_dwell_ms=0,
        stale_book_gate_ms=120_000,
        risk_config=RiskConfig(
            per_trade_loss_pct=0.02,
            per_event_loss_pct=0.20,
            per_day_loss_pct=0.06,
            max_order_notional_pct=0.005,
            max_market_exposure_pct=0.03,
            max_event_exposure_pct=0.05,
            maker_exit_grace_secs=3.0,
            cross_escalation_drawdown_pct=0.005,
            stop_open_before_expiry_secs=180.0,
            force_flat_before_expiry_secs=90.0,
            reentry_cooldown_scale=3.0,
            strategy_allocated_equity=strategy_allocated_equity,
            use_allocated_equity_for_risk=True,
        ),
        strategy_allocated_equity=strategy_allocated_equity,
        use_allocated_equity_for_risk=True,
        safe_risk_profile="custom",
        max_active_markets=max_active_markets,
        hedge_search_profile=hedge_search_profile,
        proof_only_bucket_distance=proof_only_bucket_distance,
        proof_only_expiry_slack_ms=proof_only_expiry_slack_ms,
    )
    telemetry = StandaloneTelemetry(
        runtime_root=runtime_root,
        book_manager=books,
        position_tracker=runner.position_tracker,
        mode="PAPER",
    )
    return runner, telemetry


def _run_scenario(
    *,
    runtime_root: Path,
    base_now_ms: int,
    scenario: Any,
    runner_kwargs: Optional[Dict[str, Any]] = None,
) -> None:
    runner, telemetry = _build_harness_runner(runtime_root, **dict(runner_kwargs or {}))
    try:
        scenario(runner=runner, telemetry=telemetry, base_now_ms=base_now_ms)
    finally:
        telemetry.close()


def _run_cross_escalation_scenario(
    *,
    runner: CoreMMRunner,
    telemetry: StandaloneTelemetry,
    base_now_ms: int,
) -> None:
    market = MarketCandidate(
        reference_symbol="BTC",
        slug="btc-updown-15m-cross",
        condition_id="cross-market",
        token_ids=("cross_yes",),
        outcomes=("YES",),
        reward_per_100=0.0,
        volatility_sum=0.0,
        spread=0.02,
        mid_price=0.49,
        active=True,
        closed=False,
        accepting_orders=True,
        tick_size=0.01,
        max_incentive_spread=None,
        min_incentive_size=1.0,
        end_ts_ms=base_now_ms + 900_000,
        end_ts_source="harness",
        active_now=True,
        tradable=True,
        clob_candidate=True,
        score=1.0,
        raw={
            "event_ticker": "HARNESS-CROSS",
            "open_time": (base_now_ms - 900_000) / 1000.0,
        },
    )
    runner.active_markets = [market]
    runner.position_tracker.apply_fill(token_id="cross_yes", side="buy", size=50.0, price=0.50)
    runner.main_loop.record_fill(token_id="cross_yes", side="buy", price=0.50, mid_at_fill=0.49, ts_ms=base_now_ms)

    runner.book_manager.apply_snapshot("cross_yes", bids=[(0.48, 200)], asks=[(0.50, 200)], ts_ms=base_now_ms)
    first = asyncio.run(runner.run_cycle(now_ms=base_now_ms + 31_000, usdc_balance=1_000.0))
    _record_cycle_and_fills(
        runner=runner,
        telemetry=telemetry,
        result=first,
        now_ms=base_now_ms + 31_000,
    )

    runner.book_manager.apply_snapshot("cross_yes", bids=[(0.27, 200)], asks=[(0.29, 200)], ts_ms=base_now_ms + 42_000)
    second = asyncio.run(runner.run_cycle(now_ms=base_now_ms + 42_000, usdc_balance=1_000.0))
    _record_cycle_and_fills(
        runner=runner,
        telemetry=telemetry,
        result=second,
        now_ms=base_now_ms + 42_000,
    )


def _run_force_flat_scenario(
    *,
    runner: CoreMMRunner,
    telemetry: StandaloneTelemetry,
    base_now_ms: int,
) -> None:
    market = MarketCandidate(
        reference_symbol="BTC",
        slug="btc-updown-5m-force-flat",
        condition_id="force-flat-market",
        token_ids=("force_yes",),
        outcomes=("YES",),
        reward_per_100=0.0,
        volatility_sum=0.0,
        spread=0.02,
        mid_price=0.49,
        active=True,
        closed=False,
        accepting_orders=True,
        tick_size=0.01,
        max_incentive_spread=None,
        min_incentive_size=1.0,
        end_ts_ms=base_now_ms + 5_000,
        end_ts_source="harness",
        active_now=True,
        tradable=True,
        clob_candidate=True,
        score=1.0,
        raw={
            "event_ticker": "HARNESS-FORCE-FLAT",
            "open_time": (base_now_ms - 300_000) / 1000.0,
        },
    )
    runner.active_markets = [market]
    runner.position_tracker.apply_fill(token_id="force_yes", side="buy", size=10.0, price=0.50)
    runner.main_loop.record_fill(token_id="force_yes", side="buy", price=0.50, mid_at_fill=0.49, ts_ms=base_now_ms)
    runner.book_manager.apply_snapshot("force_yes", bids=[(0.48, 200)], asks=[(0.50, 200)], ts_ms=base_now_ms)
    result = asyncio.run(runner.run_cycle(now_ms=base_now_ms, usdc_balance=1_000.0))
    _record_cycle_and_fills(
        runner=runner,
        telemetry=telemetry,
        result=result,
        now_ms=base_now_ms,
    )


def _run_day_loss_flatten_scenario(
    *,
    runner: CoreMMRunner,
    telemetry: StandaloneTelemetry,
    base_now_ms: int,
) -> None:
    market = MarketCandidate(
        reference_symbol="BTC",
        slug="btc-updown-15m-day-loss",
        condition_id="day-loss-market",
        token_ids=("day_loss_yes",),
        outcomes=("YES",),
        reward_per_100=0.0,
        volatility_sum=0.0,
        spread=0.02,
        mid_price=0.20,
        active=True,
        closed=False,
        accepting_orders=True,
        tick_size=0.01,
        max_incentive_spread=None,
        min_incentive_size=1.0,
        end_ts_ms=base_now_ms + 900_000,
        end_ts_source="harness",
        active_now=True,
        tradable=True,
        clob_candidate=True,
        score=1.0,
        raw={
            "event_ticker": "HARNESS-DAY-LOSS",
            "open_time": (base_now_ms - 900_000) / 1000.0,
        },
    )
    runner.set_kill_switch(False, reason="risk_harness_reset")
    runner.set_trading_enabled(True, reason="risk_harness_reset")
    runner.active_markets = [market]
    runner.position_tracker.apply_fill(token_id="day_loss_yes", side="buy", size=20.0, price=0.50)
    runner.main_loop.record_fill(token_id="day_loss_yes", side="buy", price=0.50, mid_at_fill=0.49, ts_ms=base_now_ms)

    runner.book_manager.apply_snapshot("day_loss_yes", bids=[(0.19, 200)], asks=[(0.21, 200)], ts_ms=base_now_ms)
    first = asyncio.run(runner.run_cycle(now_ms=base_now_ms + 1_000, usdc_balance=1_000.0))
    _record_cycle_and_fills(
        runner=runner,
        telemetry=telemetry,
        result=first,
        now_ms=base_now_ms + 1_000,
    )

    runner.book_manager.apply_snapshot("day_loss_yes", bids=[(0.04, 200)], asks=[(0.06, 200)], ts_ms=base_now_ms + 5_000)
    second = asyncio.run(runner.run_cycle(now_ms=base_now_ms + 5_000, usdc_balance=1_000.0))
    _record_cycle_and_fills(
        runner=runner,
        telemetry=telemetry,
        result=second,
        now_ms=base_now_ms + 5_000,
    )


def _run_proof_only_crypto_hedge_scenario(
    *,
    runner: CoreMMRunner,
    telemetry: StandaloneTelemetry,
    base_now_ms: int,
) -> None:
    events = [
        {
            "slug": "btc-updown-15m-B70650",
            "conditionId": "proof-70650",
            "clobTokenIds": ["yes_70650", "no_70650"],
            "active": True,
            "closed": False,
            "accepting_orders": True,
            "volatility_sum": 1.0,
            "spread": 0.02,
            "prices": [0.49, 0.51],
            "reward_per_100": 8,
            "event_ticker": "BTC-PROOF-1",
            "open_time": (base_now_ms - 600_000) / 1000.0,
            "endTime": int((base_now_ms + 900_000) / 1000),
        },
        {
            "slug": "btc-updown-15m-B70750",
            "conditionId": "proof-70750",
            "clobTokenIds": ["yes_70750", "no_70750"],
            "active": True,
            "closed": False,
            "accepting_orders": True,
            "volatility_sum": 1.0,
            "spread": 0.02,
            "prices": [0.48, 0.52],
            "reward_per_100": 7,
            "event_ticker": "BTC-PROOF-1",
            "open_time": (base_now_ms - 600_000) / 1000.0,
            "endTime": int((base_now_ms + 900_000) / 1000),
        },
        {
            "slug": "btc-updown-15m-B70850",
            "conditionId": "proof-70850",
            "clobTokenIds": ["yes_70850", "no_70850"],
            "active": True,
            "closed": False,
            "accepting_orders": True,
            "volatility_sum": 1.0,
            "spread": 0.02,
            "prices": [0.47, 0.53],
            "reward_per_100": 6,
            "event_ticker": "BTC-PROOF-1",
            "open_time": (base_now_ms - 600_000) / 1000.0,
            "endTime": int((base_now_ms + 900_000) / 1000),
        },
    ]
    runner.refresh_market_selection(events, now_ms=base_now_ms)
    for token_id in runner.all_token_ids:
        runner.book_manager.apply_snapshot(token_id, bids=[(0.49, 200)], asks=[(0.51, 200)], ts_ms=base_now_ms - 1_000)
    runner.book_manager.apply_snapshot("yes_70650", bids=[(0.45, 50)], asks=[(0.55, 50)], ts_ms=base_now_ms - 1_000)
    runner.book_manager.apply_snapshot("no_70750", bids=[(0.495, 4)], asks=[(0.505, 4)], ts_ms=base_now_ms - 1_000)
    runner.book_manager.apply_snapshot("no_70850", bids=[(0.49, 200)], asks=[(0.50, 200)], ts_ms=base_now_ms - 1_000)
    runner.position_tracker.apply_fill(token_id="yes_70650", side="buy", size=80.0, price=0.50)
    runner.risk_manager.record_fill(token_id="yes_70650", side="buy", ts_ms=base_now_ms - 20_000)

    first = asyncio.run(runner.run_cycle(now_ms=base_now_ms, usdc_balance=1_000.0))
    _record_cycle_and_fills(
        runner=runner,
        telemetry=telemetry,
        result=first,
        now_ms=base_now_ms,
    )

    runner.book_manager.apply_snapshot("no_70850", bids=[(0.47, 200)], asks=[(0.49, 200)], ts_ms=base_now_ms + 1_000)
    if hasattr(runner.broker, "sweep_fills"):
        runner.broker.sweep_fills()
    second = asyncio.run(runner.run_cycle(now_ms=base_now_ms + 1_000, usdc_balance=1_000.0))
    _record_cycle_and_fills(
        runner=runner,
        telemetry=telemetry,
        result=second,
        now_ms=base_now_ms + 1_000,
    )


def _record_cycle_and_fills(
    *,
    runner: CoreMMRunner,
    telemetry: StandaloneTelemetry,
    result: Any,
    now_ms: int,
) -> None:
    if result is None:
        return
    fill_events = runner.broker.drain_new_fills() if hasattr(runner.broker, "drain_new_fills") else []
    if fill_events:
        telemetry.record_fill_events(
            now_ms=now_ms,
            market_slug=result.market_id,
            fill_events=fill_events,
            broker_stats=runner.broker.stats() if hasattr(runner.broker, "stats") else {},
        )
    telemetry.record_cycle(
        now_ms=now_ms,
        runner=runner,
        result=result,
        feed_status={"connected": True, "subscribed_token_ids": list(runner.all_token_ids), "received_messages": 0, "applied_book_updates": 0},
        last_error=None,
        config={"mode": "HARNESS"},
    )
