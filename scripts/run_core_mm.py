from __future__ import annotations

import argparse
import asyncio
from dataclasses import asdict
from datetime import datetime, timezone
import json
import time
from pathlib import Path
import sys
from typing import Any, Dict

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core_mm.complement_arb import ComplementArbConfig
from core_mm.market_selector import MarketSelector
from core_mm.market_ws_adapter import PolymarketMarketFeed
from core_mm.memory import MemoryStore
from core_mm.risk_manager import RiskConfig
from core_mm.runner import CoreMMRunner
from core_mm.telemetry import StandaloneTelemetry
from core_mm.user_ws_adapter import PolymarketUserFeed
from config.settings import load_settings


def _write_status(meta_dir: Path, payload: Dict[str, Any]) -> None:
    (meta_dir / "status.json").write_text(json.dumps(payload, indent=2))


def _parse_args() -> argparse.Namespace:
    settings = load_settings()
    parser = argparse.ArgumentParser(description="Standalone core_mm runner")
    parser.add_argument("--mode", choices=["OBSERVE", "PAPER", "LIVE"], default="OBSERVE")
    parser.add_argument("--runtime-root", required=True)
    parser.add_argument("--duration-secs", type=float, default=120.0)
    parser.add_argument("--refresh-market-secs", type=float, default=60.0)
    parser.add_argument("--cycle-secs", type=float, default=1.0)
    parser.add_argument("--usdc-balance", type=float, default=1000.0)
    parser.add_argument("--symbol", default="BTC")
    parser.add_argument("--symbols", default=None, help="Comma-separated symbols, e.g. BTC,ETH,SOL,XRP")
    parser.add_argument("--horizon", default="15m")
    parser.add_argument("--min-size", type=float, default=10.0)
    parser.add_argument("--fallback-size", type=float, default=2.0)
    parser.add_argument("--within-pct", type=float, default=0.06)
    parser.add_argument("--trade-size", type=float, default=12.0)
    parser.add_argument("--max-size", type=float, default=150.0)
    parser.add_argument("--reverse-position-min-size", type=float, default=2.0)
    parser.add_argument("--min-order-size", type=float, default=None)
    parser.add_argument("--market-dwell-secs", type=float, default=900.0)
    parser.add_argument("--fee-bps", type=float, default=float(settings.fee_rate_bps))
    parser.add_argument("--fee-mode", default=str(settings.fee_mode))
    parser.add_argument("--daily-loss-cap", type=float, default=-50.0, help="Halt if realized PnL <= this (USD)")
    parser.add_argument("--sleep-hours", type=float, default=0.0, help="Risk manager sleep after TP/SL (hours)")
    parser.add_argument("--max-skew-ticks", type=int, default=1, help="Max ticks to shift quote center for inventory skew (0=off)")
    parser.add_argument("--inventory-skew-factor", type=float, default=1.0, help="Continuous size skew factor (0=off, 1=full)")
    parser.add_argument("--flow-filter-ewma-span", type=int, default=10, help="EWMA span for flow filter smoothing")
    parser.add_argument("--run-name", default=None, help="Human-readable run label for dashboard")
    # Multi-market
    parser.add_argument("--max-active-markets", type=int, default=1, help="Max markets to quote simultaneously")
    parser.add_argument("--max-total-position-notional", type=float, default=0.0, help="Aggregate position notional cap across all markets (0=off)")
    parser.add_argument("--max-markets-with-position", type=int, default=0, help="Max markets holding inventory simultaneously (0=off)")
    # Complement arbitrage
    parser.add_argument("--complement-arb", action="store_true", default=False, help="Enable complement arb scanner")
    parser.add_argument("--complement-arb-min-maker-edge-bps", type=float, default=100.0, help="Min maker edge (bps) to boost sizing")
    parser.add_argument("--complement-arb-maker-mult", type=float, default=2.0, help="Trade-size multiplier when maker arb is active")
    # LIVE mode risk limits
    parser.add_argument("--max-order-notional", type=float, default=5.0, help="Max notional per order in LIVE mode (USD)")
    parser.add_argument("--max-position-notional", type=float, default=10.0, help="Max position notional per token in LIVE mode (USD)")
    parser.add_argument("--max-daily-loss", type=float, default=3.0, help="Max daily loss before LIVE mode shuts down (USD)")
    return parser.parse_args()


async def _main() -> None:
    args = _parse_args()
    symbols = tuple(
        symbol.strip().upper()
        for symbol in (args.symbols.split(",") if args.symbols else [args.symbol])
        if symbol.strip()
    )
    primary_symbol = symbols[0] if symbols else str(args.symbol).strip().upper()
    # Auto-generate run name if not provided
    if args.run_name is None:
        ts_label = datetime.now(timezone.utc).strftime("%b%d-%H%M")
        args.run_name = f"{args.mode.lower()} {','.join(symbols)} {ts_label}"
    runtime_root = Path(args.runtime_root)
    meta_dir = runtime_root / "meta"
    meta_dir.mkdir(parents=True, exist_ok=True)
    _write_status(
        meta_dir,
        {
            "mode": args.mode,
            "stage": "bootstrapping",
            "run_name": args.run_name,
            "market": None,
            "token_ids": [],
            "feed": {"connected": False, "subscribed_token_ids": [], "received_messages": 0, "applied_book_updates": 0},
            "runner": {"mode": args.mode, "market_id": None, "token_ids": [], "has_books": False},
            "decisions": 0,
            "order_actions": 0,
            "fills": 0,
            "last_error": None,
            "symbols": list(symbols),
            "updated_at_ms": int(time.time() * 1000),
        },
    )
    selector = MarketSelector()
    selector.config = type(selector.config)(symbol=primary_symbol, symbols=symbols, horizon=args.horizon)
    risk_config = RiskConfig(
        sleep_hours=args.sleep_hours,
        max_total_position_notional=args.max_total_position_notional,
        max_markets_with_position=args.max_markets_with_position,
    )

    # Build LiveBroker for LIVE mode
    live_broker = None
    user_feed = None
    if args.mode == "LIVE":
        from core_mm.execution import ExecutionAdapter
        from core_mm.live_broker import LiveBroker

        settings = load_settings()
        for key_name, key_val in [
            ("polymarket_api_key", settings.polymarket_api_key),
            ("polymarket_secret", settings.polymarket_secret),
            ("polymarket_passphrase", settings.polymarket_passphrase),
            ("polymarket_private_key", settings.polymarket_private_key),
        ]:
            if not key_val:
                raise RuntimeError(f"LIVE mode requires {key_name} in .env")

        from py_clob_client.client import ClobClient  # type: ignore
        from py_clob_client.clob_types import ApiCreds  # type: ignore

        creds = ApiCreds(
            api_key=settings.polymarket_api_key,
            api_secret=settings.polymarket_secret,
            api_passphrase=settings.polymarket_passphrase,
        )
        sig_type = int(getattr(settings, "polymarket_signature_type", 0) or 0)
        funder = getattr(settings, "polymarket_funder_address", None) or None
        clob_client = ClobClient(
            host="https://clob.polymarket.com",
            chain_id=137,
            key=settings.polymarket_private_key,
            signature_type=sig_type,
            creds=creds,
            funder=funder,
        )
        exec_adapter = ExecutionAdapter(client=clob_client)
        live_broker = LiveBroker(
            execution_adapter=exec_adapter,
            fee_bps=args.fee_bps,
            max_order_notional=args.max_order_notional,
            max_position_notional=args.max_position_notional,
            max_daily_loss=args.max_daily_loss,
        )
        user_feed = PolymarketUserFeed(
            api_key=settings.polymarket_api_key,
            api_secret=settings.polymarket_secret,
            api_passphrase=settings.polymarket_passphrase,
        )
        print(f"[LIVE] Broker ready. Risk limits: order=${args.max_order_notional}, pos=${args.max_position_notional}, daily_loss=${args.max_daily_loss}")

    complement_arb_config = ComplementArbConfig(
        enabled=args.complement_arb,
        min_maker_edge_bps=args.complement_arb_min_maker_edge_bps,
        fee_bps=args.fee_bps,
        maker_size_multiplier=args.complement_arb_maker_mult,
    ) if args.complement_arb else None

    runner = CoreMMRunner(
        market_selector=selector,
        mode=args.mode,
        broker=live_broker,
        min_size=args.min_size,
        fallback_size=args.fallback_size,
        within_pct=args.within_pct,
        trade_size=args.trade_size,
        max_size=args.max_size,
        reverse_position_min_size=args.reverse_position_min_size,
        min_order_size_override=args.min_order_size,
        fee_bps=args.fee_bps,
        fee_mode=args.fee_mode,
        market_dwell_ms=int(max(0.0, float(args.market_dwell_secs)) * 1000),
        max_skew_ticks=args.max_skew_ticks,
        inventory_skew_factor=args.inventory_skew_factor,
        flow_filter_ewma_span=args.flow_filter_ewma_span,
        risk_config=risk_config,
        max_active_markets=args.max_active_markets,
        complement_arb_config=complement_arb_config,
    )
    telemetry = StandaloneTelemetry(
        runtime_root=runtime_root,
        book_manager=runner.book_manager,
        position_tracker=runner.position_tracker,
        mode=args.mode,
    )
    on_applied_update = runner.broker.sweep_fills if hasattr(runner.broker, "sweep_fills") else None
    feed = PolymarketMarketFeed(book_manager=runner.book_manager, token_ids=(), on_applied_update=on_applied_update)

    try:
        changed = runner.refresh_market_selection(now_ms=int(time.time() * 1000))
    except Exception as exc:
        _write_status(
            meta_dir,
            {
                "mode": args.mode,
                "stage": "market_selection_failed",
                "market": None,
                "token_ids": [],
                "feed": {"connected": False, "subscribed_token_ids": [], "received_messages": 0, "applied_book_updates": 0},
                "runner": {"mode": args.mode, "market_id": None, "token_ids": [], "has_books": False},
                "decisions": 0,
                "order_actions": 0,
                "fills": 0,
                "last_error": str(exc),
                "symbols": list(symbols),
                "updated_at_ms": int(time.time() * 1000),
                "config": {
                    "symbols": list(symbols),
                    "min_size": args.min_size,
                    "fallback_size": args.fallback_size,
                    "within_pct": args.within_pct,
                    "trade_size": args.trade_size,
                    "max_size": args.max_size,
                    "reverse_position_min_size": args.reverse_position_min_size,
                    "min_order_size": args.min_order_size,
                },
            },
        )
        raise
    if changed and runner.active_markets:
        feed.set_token_ids(runner.all_token_ids)

    feed_task = asyncio.create_task(feed.run())
    user_feed_task = None
    if user_feed is not None and live_broker is not None:
        def _on_user_message(msg: Dict[str, Any]) -> None:
            # Route through runner's UserFeedState for position tracking
            runner.on_user_message(msg)
            # Also ingest fills into LiveBroker for PnL/stats
            from core_mm.user_feed import parse_user_message
            for evt in parse_user_message(msg):
                if evt.event_type == "trade" and evt.token_id and evt.side and evt.price and evt.size > 0:
                    live_broker.record_fill({
                        "token_id": evt.token_id,
                        "side": evt.side,
                        "price": evt.price,
                        "size": evt.size,
                        "order_id": evt.order_id,
                        "ts_ms": int(time.time() * 1000),
                    })
        user_feed._on_message = _on_user_message
        user_feed_task = asyncio.create_task(user_feed.run())
    started = time.time()
    next_refresh = started + args.refresh_market_secs
    decisions = 0
    orders = 0
    fills = 0
    last_error = None
    try:
        while (time.time() - started) < args.duration_secs:
            now = time.time()
            if not runner.active_markets or now >= next_refresh:
                changed = runner.refresh_market_selection(now_ms=int(now * 1000))
                if runner.active_markets and changed:
                    feed.set_token_ids(runner.all_token_ids)
                next_refresh = now + args.refresh_market_secs

            try:
                cycle_results = await runner.run_cycles(now_ms=int(now * 1000), usdc_balance=args.usdc_balance)
                for result in cycle_results:
                    decisions += 1
                    orders += len(result.order_actions)
                    fills += sum(1 for item in result.execution_results if item.payload.get("fill"))
            except Exception as exc:  # pragma: no cover - live smoke path
                last_error = str(exc)
                cycle_results = []

            new_fills = runner.broker.drain_new_fills() if hasattr(runner.broker, "drain_new_fills") else []
            if new_fills:
                telemetry.record_fill_events(
                    now_ms=int(now * 1000),
                    market_slug=(runner.current_market.slug if runner.current_market is not None else None),
                    fill_events=new_fills,
                    broker_stats=(runner.broker.stats() if hasattr(runner.broker, "stats") else {}),
                )
                fills = len(runner.broker.fills()) if hasattr(runner.broker, "fills") else fills
                # Feed fills into alpha overlay for adversity tracking
                for fill_evt in new_fills:
                    token_id = str(fill_evt.get("token_id") or "")
                    side = str(fill_evt.get("side") or "")
                    price = float(fill_evt.get("price") or 0.0)
                    mid = float(fill_evt.get("mid_at_fill") or 0.0)
                    if not mid:
                        book = runner.book_manager.get_book(token_id)
                        mid = float(book.mid_price) if book and book.mid_price else 0.0
                    if token_id and side and price > 0 and mid > 0:
                        runner.main_loop.record_fill_for_alpha(token_id, side, price, mid)

            # Daily loss cap check
            if hasattr(runner.broker, "stats"):
                _broker_stats = runner.broker.stats()
                _net_pnl = _broker_stats.get("realized_net_pnl", 0.0)
                if _net_pnl <= args.daily_loss_cap:
                    last_error = f"DAILY LOSS CAP HIT: PnL ${_net_pnl:.2f} <= ${args.daily_loss_cap:.2f}"
                    break

            feed_snapshot = asdict(feed.status())
            config_payload = {
                "symbols": list(symbols),
                "min_size": args.min_size,
                "fallback_size": args.fallback_size,
                "within_pct": args.within_pct,
                "trade_size": args.trade_size,
                "max_size": args.max_size,
                "reverse_position_min_size": args.reverse_position_min_size,
                "min_order_size": args.min_order_size,
                "market_dwell_secs": args.market_dwell_secs,
                "fee_bps": args.fee_bps,
                "fee_mode": args.fee_mode,
            }
            telemetry.record_cycle(
                now_ms=int(now * 1000),
                runner=runner,
                results=cycle_results,
                feed_status=feed_snapshot,
                last_error=last_error,
                config=config_payload,
            )

            status = {
                "mode": args.mode,
                "run_name": args.run_name,
                "market": runner.current_market.slug if runner.current_market is not None else None,
                "markets": [m.slug for m in runner.active_markets],
                "token_ids": list(runner.all_token_ids),
                "feed": feed_snapshot,
                "runner": asdict(runner.status()),
                "decisions": decisions,
                "order_actions": orders,
                "fills": len(runner.broker.fills()) if hasattr(runner.broker, "fills") else fills,
                "last_error": last_error,
                "symbols": list(symbols),
                "runtime_db_path": telemetry.db_path.as_posix(),
                "run_summary_path": (telemetry.meta_dir / "run_summary.json").as_posix(),
                "updated_at_ms": int(time.time() * 1000),
                "config": config_payload,
            }
            if not runner.active_markets:
                status["stage"] = "awaiting_market"
            elif int(feed_snapshot.get("applied_book_updates") or 0) <= 0:
                status["stage"] = "awaiting_books"
            else:
                status["stage"] = "running"
            _write_status(meta_dir, status)
            await asyncio.sleep(args.cycle_secs)
    finally:
        # LIVE mode: cancel all resting orders on shutdown/crash
        if args.mode == "LIVE" and runner.broker is not None:
            try:
                print("[LIVE] Cancelling all resting orders...")
                runner.broker.cancel_all()
                print("[LIVE] All orders cancelled.")
            except Exception as cancel_exc:
                print(f"[LIVE] cancel_all failed: {cancel_exc}")
        # Ingest session into memory store before closing telemetry
        try:
            market_slug = runner.current_market.slug if runner.current_market is not None else ""
            summary = telemetry.to_session_summary(
                run_id=runtime_root.name,
                symbol=primary_symbol,
                market_slug=market_slug,
            )
            memory_db = runtime_root.parent / "memory.db"
            mem_store = MemoryStore(memory_db)
            mem_store.ingest_session(summary)
            mem_store.close()
        except Exception:
            pass  # Memory ingestion is best-effort
        telemetry.close()
        feed.stop()
        if user_feed is not None:
            user_feed.stop()
        await asyncio.sleep(0)
        if not feed_task.done():
            await asyncio.wait_for(feed_task, timeout=5.0)
        if user_feed_task is not None and not user_feed_task.done():
            await asyncio.wait_for(user_feed_task, timeout=5.0)


if __name__ == "__main__":
    asyncio.run(_main())
