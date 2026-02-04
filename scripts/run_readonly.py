from __future__ import annotations

import argparse
import asyncio
import signal
import uuid
import time
from pathlib import Path

from config.settings import load_markets, load_settings, validate_markets_config
from core.decision_engine import DecisionEngine, DecisionEngineConfig
from core.decision_tape import DecisionTape, TimeMapper
from core.dry_run import DryRunConfig, DryRunGenerator, DryRunLogger
from core.event_tape import EventTape
from core.execution_runner import ExecutionRunner
from core.broker_sim import SimBroker, SimBrokerConfig
from core.model_artifact import load_model
from core.onchain_ingest import OnchainIngestConfig, OnchainIngestor
from core.onchain_signals import OnchainSignalState, load_whales
from core.reference_store import ReferenceStore
from core.reference_feed import ReferenceFeed, ReferenceFeedConfig
from core.reference_price import ReferencePriceAggregator, parse_reference_event
from core.metrics import Metrics
from core.order_book import OrderBook
from core.validators import OrderConstraints, SimBalances
from core.market_discovery import resolve_markets, GAMMA_BASE_URL
from data.polymarket_ws import MarketWSClient, UserWSClient, WSConfig


async def main() -> None:
    args = _parse_args()
    settings = load_settings()
    log_dir = args.log_dir or settings.log_dir
    markets_path = args.markets or settings.track_markets_yaml

    markets = load_markets(markets_path)
    auto_discover = args.auto_discover or settings.auto_discover
    validate_markets_config(markets, auto_discover=auto_discover)
    discovery_summary: dict = {"started_at": time.time()}

    try: 
        resolved_markets, asset_meta = await resolve_markets(
            markets=markets,
            auto_discover=auto_discover,
            cache_path=Path(log_dir) / "cache_gamma_markets.json",
            gamma_base_url=GAMMA_BASE_URL,
            discovery_summary=discovery_summary,
        )
    finally:
         # ALWAYS write discovery telemetry, even on Ctrl-C
        if auto_discover:
            _write_discovery_summary(log_dir, discovery_summary)

    for market in resolved_markets:
        print(
            "resolved_market",
            {
                "slug": market.slug,
                "condition_id": market.condition_id,
                "clobTokenIds": market.token_ids,
            },
        )
    if auto_discover:
        _write_discovery_summary(log_dir, discovery_summary)
    asset_ids = sorted({token for market in resolved_markets for token in market.token_ids})
    if not asset_ids:
        raise ValueError("no_asset_ids_configured")

    run_id = uuid.uuid4().hex
    _write_resolved_markets(log_dir, run_id, resolved_markets)
    tape = EventTape(log_dir=log_dir, run_id=run_id)
    decision_tape = DecisionTape(log_dir=log_dir, run_id=run_id)
    metrics = Metrics()

    books = {asset_id: OrderBook(asset_id=asset_id, bids={}, asks={}) for asset_id in asset_ids}
    constraints = {
        asset_id: OrderConstraints(
            min_tick=market.min_tick,
            min_size=market.min_size,
            min_price=market.min_price,
            max_price=market.max_price,
            max_spread_bps=settings.max_spread_bps,
            max_slippage_bps=settings.max_slippage_bps,
            max_book_staleness_ms=settings.max_book_staleness_ms,
        )
        for market in resolved_markets
        for asset_id in market.token_ids
        if asset_id
    }

    balances = SimBalances(
        usd=settings.sim_balance_usd,
        tokens={},
        default_token_balance=settings.sim_balance_tokens_default,
    )

    time_mapper = TimeMapper.from_wall_and_mono(
        wall_ms=int(time.time() * 1000),
        mono_ns=time.monotonic_ns(),
    )
    reference_aggregator = ReferencePriceAggregator(
        required_sources={"spot", "perp"},
        staleness_ms=settings.reference_staleness_ms,
        disagreement_bps=settings.reference_disagreement_bps,
        min_confidence=settings.reference_min_confidence,
        disagreement_bps_soft=settings.reference_disagreement_bps_soft,
        disagreement_bps_hard=settings.reference_disagreement_bps_hard,
        disagreement_decay_k=settings.reference_disagreement_decay_k,
        allowed_symbols={market.reference_symbol for market in resolved_markets},
    )
    model_artifact = None
    model_load_error = None
    model_path = args.model
    if model_path:
        try:
            model_artifact = load_model(model_path)
        except Exception as exc:
            model_load_error = f"MODEL_LOAD_ERROR:{exc}"
    reference_store = ReferenceStore()
    onchain_enabled = args.onchain or settings.onchain_ingest_enabled
    onchain_state = None
    if onchain_enabled:
        whales = load_whales(settings.onchain_whales_path)
        onchain_state = OnchainSignalState(window_secs=settings.onchain_window_secs, whales=whales)
    execution_runner = None
    trade_tape = None
    if args.paper_intents or args.sim_exec:
        from core.trade_tape import TradeTape

        trade_tape = TradeTape(log_dir=log_dir, run_id=run_id)
        sim_broker = None
        if args.sim_exec:
            fee_status_by_asset = {}
            for asset_id, meta in asset_meta.items():
                fee_info = meta.get("fee")
                status = fee_info.get("status") if isinstance(fee_info, dict) else None
                fee_status_by_asset[asset_id] = status if status else "unknown"
            sim_broker = SimBroker(
                books=books,
                constraints=constraints,
                time_mapper=time_mapper,
                fee_status_by_asset=fee_status_by_asset,
                config=SimBrokerConfig(),
            )
        execution_runner = ExecutionRunner(
            trade_tape=trade_tape,
            time_mapper=time_mapper,
            broker=sim_broker,
        )

    decision_engine = DecisionEngine(
        books=books,
        constraints=constraints,
        tape=decision_tape,
        time_mapper=time_mapper,
        config=DecisionEngineConfig(
            order_size=settings.dry_run_size,
            execution_mode="TAKER_SIM",
            fee_rate=settings.fee_rate_bps / 10_000.0,
            fee_mode=settings.fee_mode,
            depth_within_ticks_n=settings.depth_within_ticks_n,
            depth_at_notional_target=settings.depth_at_notional_target,
            ref_half_life_sec=settings.hl_vol_sec,
            reference_lag_guard_ms=settings.reference_lag_guard_ms,
            reference_staleness_ms=settings.reference_staleness_ms,
            edge_min=settings.edge_min,
            edge_exit=settings.edge_exit,
            edge_stop=settings.edge_stop,
            z_mom_min=settings.z_mom_min,
            t_min_secs=settings.t_min_secs,
            hold_max_secs=settings.hold_max_secs,
            vol_pct_hi=settings.vol_pct_hi,
            edge_min_mult_hivol=settings.edge_min_mult_hivol,
            tox_max=settings.tox_max,
            hedge_min=settings.hedge_min,
            hedge_max=settings.hedge_max,
            hedge_required_vol_pct=settings.hedge_required_vol_pct,
            pf_bias=settings.pf_bias,
            pf_w_mom=settings.pf_w_mom,
            pf_w_revert=settings.pf_w_revert,
            pf_z_clip=settings.pf_z_clip,
            pf_vol_dampen_enabled=settings.pf_vol_dampen_enabled,
            pf_vol_floor=settings.pf_vol_floor,
        ),
        market_meta=asset_meta,
        reference_aggregator=reference_aggregator,
        model_artifact=model_artifact,
        model_path=model_path,
        model_load_error=model_load_error,
        reference_store=reference_store,
        onchain_state=onchain_state,
        decision_listener=execution_runner.handle_decision if execution_runner else None,
    )

    ws_config = WSConfig(
        reconnect_base_ms=settings.ws_reconnect_base_ms,
        reconnect_max_ms=settings.ws_reconnect_max_ms,
    )

    stop_event = asyncio.Event()

    tasks = []

    if settings.market_ws_enabled:
        market_client = MarketWSClient(
            asset_ids=asset_ids,
            books=books,
            tape=tape,
            metrics=metrics,
            config=ws_config,
            decision_engine=decision_engine,
        )
        tasks.append(asyncio.create_task(market_client.run()))
    else:
        market_client = None

    user_ws_enabled = args.user_ws or settings.user_ws_enabled
    if user_ws_enabled:
        condition_ids = [market.condition_id for market in resolved_markets if market.condition_id]
        user_client = UserWSClient(
            api_key=settings.polymarket_api_key,
            secret=settings.polymarket_secret,
            passphrase=settings.polymarket_passphrase,
            condition_ids=condition_ids,
            tape=tape,
            metrics=metrics,
            config=ws_config,
        )
        tasks.append(asyncio.create_task(user_client.run()))
    else:
        user_client = None

    onchain_ingestor = None
    if onchain_enabled:
        onchain_config = OnchainIngestConfig(
            rpc_http_url=settings.polygon_rpc_http,
            rpc_ws_url=settings.polygon_rpc_ws,
            use_ws=settings.onchain_use_ws,
            poll_reconcile_secs=settings.onchain_poll_reconcile_secs,
            ws_loop_sleep_secs=settings.onchain_ws_loop_sleep_secs,
            heartbeat_secs=settings.onchain_heartbeat_secs,
            dedupe_lru_size=settings.onchain_dedupe_lru_size,
            reconcile_block_lookback=settings.onchain_max_block_range,
            recreate_filter_after_secs=settings.onchain_recreate_filter_after_secs,
            log_level=settings.onchain_log_level,
        )
        onchain_ingestor = OnchainIngestor(
            tape=tape,
            config=onchain_config,
            signal_state=onchain_state,
            metrics=metrics,
        )
        tasks.append(asyncio.create_task(onchain_ingestor.run(stop_event)))

    dry_logger = DryRunLogger(log_dir=log_dir, run_id=run_id)
    dry_config = DryRunConfig(
        strategy_id="toy_mid",
        interval_secs=settings.dry_run_interval_secs,
        order_size=settings.dry_run_size,
    )
    dry_runner = DryRunGenerator(
        books=books,
        constraints=constraints,
        balances=balances,
        metrics=metrics,
        logger=dry_logger,
        config=dry_config,
    )
    tasks.append(asyncio.create_task(dry_runner.run()))
    tasks.append(asyncio.create_task(_decision_heartbeat(decision_engine)))
    if args.reference_tape:
        _ingest_reference_tapes(
            args.reference_tape,
            reference_aggregator,
            decision_engine,
            reference_store,
        )

    reference_source = args.reference_source or settings.reference_source
    if reference_source == "none" and settings.reference_enabled:
        reference_source = "poll_coinbase"
    if reference_source != "none":
        ref_feed = ReferenceFeed(
            aggregator=reference_aggregator,
            tape=tape,
            config=ReferenceFeedConfig(
                symbols=sorted({market.reference_symbol for market in resolved_markets}),
                poll_interval_secs=settings.reference_poll_secs,
                source=reference_source,
            ),
            on_quote=decision_engine.on_reference_event,
            reference_store=reference_store,
        )
        tasks.append(asyncio.create_task(ref_feed.run()))
    else:
        ref_feed = None

    status_path = f"{log_dir}/status.json" if settings.status_json_enabled else None
    tasks.append(
        asyncio.create_task(
            metrics.periodic_report(
                books=books,
                staleness_ms=settings.max_book_staleness_ms,
                interval_secs=10.0,
                status_path=status_path,
            )
        )
    )

    def _handle_stop(*_args) -> None:
        stop_event.set()
        if market_client is not None:
            market_client.stop()
        if user_client is not None:
            user_client.stop()
        if ref_feed is not None:
            ref_feed.stop()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, _handle_stop)

    await stop_event.wait()
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    tape.close()
    dry_logger.close()
    decision_tape.close()
    if trade_tape is not None:
        trade_tape.close()


async def _decision_heartbeat(decision_engine: DecisionEngine) -> None:
    while True:
        decision_engine.emit_heartbeats_until(time.monotonic_ns())
        await asyncio.sleep(1.0)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Polymarket read-only runner")
    parser.add_argument("--markets", default=None, help="Path to markets.yaml")
    parser.add_argument("--user-ws", action="store_true", help="Enable user websocket")
    parser.add_argument("--log-dir", default=None, help="Override LOG_DIR")
    parser.add_argument("--auto_discover", action="store_true", help="Resolve markets via Gamma API")
    parser.add_argument("--onchain", action="store_true", help="Enable on-chain ingestion")
    parser.add_argument("--paper_intents", action="store_true", help="Emit TradeTape intents only")
    parser.add_argument("--sim_exec", action="store_true", help="Simulate taker execution and emit TradeTape")
    parser.add_argument("--model", default=None, help="Path to trained model artifact JSON")
    parser.add_argument(
        "--reference_source",
        default=None,
        help="Reference source: none|poll_coinbase|poll_kraken",
    )
    parser.add_argument(
        "--reference_tape",
        nargs="*",
        default=None,
        help="Optional reference tape files to pre-ingest",
    )
    return parser.parse_args()


def _ingest_reference_tapes(
    paths: list[str],
    aggregator: ReferencePriceAggregator,
    decision_engine: DecisionEngine,
    store: ReferenceStore,
) -> None:
    from pathlib import Path
    import json

    for entry in paths:
        path = Path(entry)
        if not path.exists():
            continue
        for line in path.read_text().splitlines():
            if not line:
                continue
            record = json.loads(line)
            if record.get("channel") != "reference":
                continue
            store.ingest_record(record)
            quote = parse_reference_event(
                record.get("raw"),
                record.get("t_recv_mono_ns"),
                record.get("t_recv_wall_iso"),
                record.get("t_recv_wall_ms"),
            )
            if quote is None:
                continue
            aggregator.ingest(quote)
            decision_engine.on_reference_event(quote)


def _write_resolved_markets(log_dir: str, run_id: str, resolved_markets) -> None:
    from datetime import datetime, timezone
    import json

    run_dir = Path(log_dir) / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    path = run_dir / "resolved_markets.json"
    payload = {
        "schema_version": "resolved_markets_v1",
        "run_id": run_id,
        "t_created_wall_iso": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z",
        "markets": [
            {
                "name": market.name,
                "reference_symbol": market.reference_symbol,
                "market_slug": market.slug,
                "condition_id": market.condition_id,
                "outcomes": market.outcomes,
                "tokens": [
                    {"token_id": token_id, "outcome": market.outcome_by_token.get(token_id)}
                    for token_id in market.token_ids
                ],
                "constraints": {
                    "min_tick": market.min_tick,
                    "min_size": market.min_size,
                    "min_price": market.min_price,
                    "max_price": market.max_price,
                },
            }
            for market in resolved_markets
        ],
    }
    path.write_text(json.dumps(payload, separators=(",", ":"), ensure_ascii=True, sort_keys=True))


def _write_discovery_summary(log_dir: str, summary: dict) -> None:
    from datetime import datetime, timezone
    import json

    totals = {
        "clob_candidates": 0,
        "fee_enabled": 0,
        "identified_15m_crypto": 0,
        "selected_markets": 0,
        "rejected_unknown_symbol": 0,
    }
    for entry in summary.get("by_symbol", []):
        for key in totals:
            totals[key] += int(entry.get(key, 0) or 0)
    payload = {
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z",
        "backend": "clob",
        "by_symbol": summary.get("by_symbol", []),
        **totals,
    }
    path = Path(log_dir) / "discovery_summary.json"
    path.write_text(json.dumps(payload, separators=(",", ":"), ensure_ascii=True, sort_keys=True))


if __name__ == "__main__":
    asyncio.run(main())
