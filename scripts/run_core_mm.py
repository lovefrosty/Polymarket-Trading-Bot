from __future__ import annotations

import argparse
import asyncio
from dataclasses import asdict
import json
import time
from pathlib import Path
import sys
from typing import Any, Dict

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core_mm.market_selector import MarketSelector
from core_mm.market_ws_adapter import PolymarketMarketFeed
from core_mm.runner import CoreMMRunner
from core_mm.telemetry import StandaloneTelemetry
from config.settings import load_settings


def _write_status(meta_dir: Path, payload: Dict[str, Any]) -> None:
    (meta_dir / "status.json").write_text(json.dumps(payload, indent=2))


def _parse_args() -> argparse.Namespace:
    settings = load_settings()
    parser = argparse.ArgumentParser(description="Standalone core_mm runner")
    parser.add_argument("--mode", choices=["OBSERVE", "PAPER"], default="OBSERVE")
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
    return parser.parse_args()


async def _main() -> None:
    args = _parse_args()
    symbols = tuple(
        symbol.strip().upper()
        for symbol in (args.symbols.split(",") if args.symbols else [args.symbol])
        if symbol.strip()
    )
    primary_symbol = symbols[0] if symbols else str(args.symbol).strip().upper()
    runtime_root = Path(args.runtime_root)
    meta_dir = runtime_root / "meta"
    meta_dir.mkdir(parents=True, exist_ok=True)
    _write_status(
        meta_dir,
        {
            "mode": args.mode,
            "stage": "bootstrapping",
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
    runner = CoreMMRunner(
        market_selector=selector,
        mode=args.mode,
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
    if changed and runner.current_market is not None:
        feed.set_token_ids(runner.current_market.token_ids)

    feed_task = asyncio.create_task(feed.run())
    started = time.time()
    next_refresh = started + args.refresh_market_secs
    decisions = 0
    orders = 0
    fills = 0
    last_error = None
    try:
        while (time.time() - started) < args.duration_secs:
            now = time.time()
            if runner.current_market is None or now >= next_refresh:
                changed = runner.refresh_market_selection(now_ms=int(now * 1000))
                if runner.current_market is not None and changed:
                    feed.set_token_ids(runner.current_market.token_ids)
                next_refresh = now + args.refresh_market_secs

            try:
                result = await runner.run_cycle(now_ms=int(now * 1000), usdc_balance=args.usdc_balance)
                if result is not None:
                    decisions += 1
                    orders += len(result.order_actions)
                    fills += sum(1 for item in result.execution_results if item.payload.get("fill"))
            except Exception as exc:  # pragma: no cover - live smoke path
                last_error = str(exc)
                result = None

            new_fills = runner.broker.drain_new_fills() if hasattr(runner.broker, "drain_new_fills") else []
            if new_fills:
                telemetry.record_fill_events(
                    now_ms=int(now * 1000),
                    market_slug=(runner.current_market.slug if runner.current_market is not None else None),
                    fill_events=new_fills,
                    broker_stats=(runner.broker.stats() if hasattr(runner.broker, "stats") else {}),
                )
                fills = len(runner.broker.fills()) if hasattr(runner.broker, "fills") else fills

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
                result=result,
                feed_status=feed_snapshot,
                last_error=last_error,
                config=config_payload,
            )

            status = {
                "mode": args.mode,
                "market": runner.current_market.slug if runner.current_market is not None else None,
                "token_ids": list(runner.current_market.token_ids) if runner.current_market is not None else [],
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
            if runner.current_market is None:
                status["stage"] = "awaiting_market"
            elif int(feed_snapshot.get("applied_book_updates") or 0) <= 0:
                status["stage"] = "awaiting_books"
            else:
                status["stage"] = "running"
            _write_status(meta_dir, status)
            await asyncio.sleep(args.cycle_secs)
    finally:
        telemetry.close()
        feed.stop()
        await asyncio.sleep(0)
        if not feed_task.done():
            await asyncio.wait_for(feed_task, timeout=5.0)


if __name__ == "__main__":
    asyncio.run(_main())
