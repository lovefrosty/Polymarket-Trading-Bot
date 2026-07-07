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
from core_mm.control_plane import ControlCommand, ControlCommandStore, validate_command
from core_mm.market_selector import MarketSelector
from core_mm.market_ws_adapter import PolymarketMarketFeed
from core_mm.memory import MemoryStore
from core_mm.risk_manager import RiskConfig
from core_mm.runner import CoreMMRunner, SAFE_RISK_PROFILES, resolve_safe_risk_profile_name
from core_mm.telemetry import StandaloneTelemetry
from core_mm.user_ws_adapter import PolymarketUserFeed
from config.settings import load_settings


def _write_status(meta_dir: Path, payload: Dict[str, Any]) -> None:
    (meta_dir / "status.json").write_text(json.dumps(payload, indent=2))


def _read_optional_json(path_str: str | None) -> Dict[str, Any]:
    if not path_str:
        return {}
    path = Path(path_str)
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text())
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _apply_safe_profile_defaults(args: argparse.Namespace) -> argparse.Namespace:
    allocated_equity = float(args.strategy_allocated_equity if args.strategy_allocated_equity not in (None, "") else args.usdc_balance)
    args.strategy_allocated_equity = allocated_equity
    resolved_profile = resolve_safe_risk_profile_name(allocated_equity, args.safe_risk_profile)
    args.safe_risk_profile = resolved_profile
    if resolved_profile == "custom":
        return args
    profile = SAFE_RISK_PROFILES.get(resolved_profile)
    if not profile:
        return args
    legacy_defaults = {
        "min_size": 10.0,
        "fallback_size": 2.0,
        "trade_size": 12.0,
        "max_size": 150.0,
        "hard_position_cap": 250.0,
        "per_event_loss_pct": 0.05,
        "per_day_loss_pct": 0.10,
    }
    profile_map = {
        "min_size": profile["min_size"],
        "fallback_size": profile["fallback_size"],
        "trade_size": profile["trade_size"],
        "max_size": profile["max_size"],
        "hard_position_cap": profile["hard_position_cap"],
        "per_event_loss_pct": profile["per_event_loss_pct"],
        "per_day_loss_pct": profile["per_day_loss_pct"],
    }
    for key, legacy_default in legacy_defaults.items():
        current = getattr(args, key)
        if current == legacy_default:
            setattr(args, key, profile_map[key])
    return args


def _build_config_payload(args: argparse.Namespace, *, symbols: tuple[str, ...], runtime_controls: Dict[str, Any], runner: CoreMMRunner) -> Dict[str, Any]:
    return {
        "symbols": list(symbols),
        "safe_risk_profile": runner._safe_risk_profile,
        "strategy_allocated_equity": runner._strategy_allocated_equity,
        "use_allocated_equity_for_risk": runner._use_allocated_equity_for_risk,
        "risk_based_share_sizing": runner._risk_based_share_sizing,
        "min_size": runner._min_size,
        "fallback_size": runner._fallback_size,
        "within_pct": runner._within_pct,
        "trade_size": runner._trade_size,
        "max_size": runner._max_size,
        "min_order_size": runner._min_order_size_override,
        "market_dwell_secs": runner._market_dwell_ms / 1000.0,
        "cycle_secs": float(runtime_controls.get("cycle_secs") or args.cycle_secs),
        "refresh_market_secs": float(runtime_controls.get("refresh_market_secs") or args.refresh_market_secs),
        "quote_spread_multiplier": float(runner.control_state().get("quote_spread_multiplier") or 1.0),
        "boundary_no_new_risk_min_price": float(runner.control_state().get("boundary_no_new_risk_min_price") or 0.0),
        "boundary_no_new_risk_max_price": float(runner.control_state().get("boundary_no_new_risk_max_price") or 1.0),
        "boundary_guard_mode": str(runner.control_state().get("boundary_guard_mode") or "adaptive"),
        "boundary_adverse_selection_threshold": float(runner.control_state().get("boundary_adverse_selection_threshold") or 0.50),
        "boundary_exit_cost_multiplier": float(runner.control_state().get("boundary_exit_cost_multiplier") or 1.25),
        "fee_bps": args.fee_bps,
        "fee_mode": args.fee_mode,
        "hard_position_cap": runner.risk_manager.config.hard_position_cap,
        "per_trade_loss_pct": runner.risk_manager.config.per_trade_loss_pct,
        "per_event_loss_pct": runner.risk_manager.config.per_event_loss_pct,
        "per_day_loss_pct": runner.risk_manager.config.per_day_loss_pct,
        "max_order_notional_pct": runner.risk_manager.config.max_order_notional_pct,
        "max_market_exposure_pct": runner.risk_manager.config.max_market_exposure_pct,
        "max_event_exposure_pct": runner.risk_manager.config.max_event_exposure_pct,
        "stale_duration_scale": runner.risk_manager.config.stale_duration_scale,
        "maker_exit_grace_secs": runner.risk_manager.config.maker_exit_grace_secs,
        "cross_escalation_drawdown_pct": runner.risk_manager.config.cross_escalation_drawdown_pct,
        "stop_open_before_expiry_secs": runner.risk_manager.config.stop_open_before_expiry_secs,
        "force_flat_before_expiry_secs": runner.risk_manager.config.force_flat_before_expiry_secs,
        "reentry_cooldown_scale": runner.risk_manager.config.reentry_cooldown_scale,
        "pre_kill_warning_fraction": runner.risk_manager.config.pre_kill_warning_fraction,
        "skew_threshold_fraction": float(runner.control_state().get("skew_threshold_fraction") or 0.25),
        "hedge_threshold_fraction": float(runner.control_state().get("hedge_threshold_fraction") or 0.60),
        "hedge_requires_stale_inventory": bool(runner.control_state().get("hedge_requires_stale_inventory")),
        "hedge_quality_must_beat_inventory_market": bool(runner.control_state().get("hedge_quality_must_beat_inventory_market")),
        "hedge_min_quality_score": float(runner.control_state().get("hedge_min_quality_score") or 0.0),
        "hedge_max_temp_gross_increase_fraction": float(runner.control_state().get("hedge_max_temp_gross_increase_fraction") or 0.0),
        "hedge_failure_cooldown_scale": float(runner.control_state().get("hedge_failure_cooldown_scale") or 1.0),
        "hedge_search_profile": str(runner.control_state().get("hedge_search_profile") or "production"),
        "proof_only_bucket_distance": int(runner.control_state().get("proof_only_bucket_distance") or 0),
        "proof_only_expiry_slack_ms": int(runner.control_state().get("proof_only_expiry_slack_ms") or 0),
        "hedge_covariance_enabled": bool(runner.control_state().get("hedge_covariance_enabled")),
        "hedge_covariance_window_secs": float(runner.control_state().get("hedge_covariance_window_secs") or 0.0),
        "hedge_covariance_min_samples": int(runner.control_state().get("hedge_covariance_min_samples") or 0),
        "hedge_covariance_min_correlation": float(runner.control_state().get("hedge_covariance_min_correlation") or 0.0),
        "hedge_covariance_min_abs_beta": float(runner.control_state().get("hedge_covariance_min_abs_beta") or 0.0),
        "hedge_covariance_beta_clip": float(runner.control_state().get("hedge_covariance_beta_clip") or 0.0),
        "hedge_covariance_beta_shrinkage": float(runner.control_state().get("hedge_covariance_beta_shrinkage") or 0.0),
        "hedge_covariance_max_sample_age_ms": int(runner.control_state().get("hedge_covariance_max_sample_age_ms") or 0),
        "hedge_covariance_max_update_gap_ms": int(runner.control_state().get("hedge_covariance_max_update_gap_ms") or 0),
        "hedge_covariance_boundary_buffer": float(runner.control_state().get("hedge_covariance_boundary_buffer") or 0.0),
        "hedge_covariance_boundary_max_fraction": float(runner.control_state().get("hedge_covariance_boundary_max_fraction") or 0.0),
        "hedge_covariance_strong_correlation": float(runner.control_state().get("hedge_covariance_strong_correlation") or 0.0),
        "hedge_covariance_strong_min_samples": int(runner.control_state().get("hedge_covariance_strong_min_samples") or 0),
        "hedge_covariance_stability_ratio_max": float(runner.control_state().get("hedge_covariance_stability_ratio_max") or 0.0),
        "hedge_covariance_gate_required": bool(runner.control_state().get("hedge_covariance_gate_required")),
        "observe_pause_interval_secs": float(runner.control_state().get("observe_pause_interval_secs") or 0.0),
        "observe_pause_duration_secs": float(runner.control_state().get("observe_pause_duration_secs") or 0.0),
        "negative_pnl_reduce_only_enabled": bool(runner.control_state().get("negative_pnl_reduce_only_enabled")),
        "negative_pnl_unwind_requires_worsening": bool(runner.control_state().get("negative_pnl_unwind_requires_worsening")),
        "negative_pnl_unwind_requires_stale_or_worsening": bool(runner.control_state().get("negative_pnl_unwind_requires_stale_or_worsening")),
        "post_fill_reentry_cooldown_secs": runner.main_loop._post_fill_reentry_cooldown_ms / 1000.0,
    }


def _apply_control_command(
    command: ControlCommand,
    *,
    args: argparse.Namespace,
    runner: CoreMMRunner,
    runtime_controls: Dict[str, Any],
    safe_runtime_controls: Dict[str, Any],
) -> Dict[str, Any]:
    command_type = str(command.command_type or "")
    payload = dict(command.payload or {})
    if command_type == "pause_trading":
        pause_result = runner.set_trading_enabled(False, reason="dashboard_command")
        cancel_result = runner.cancel_all_quotes()
        return {"pause": pause_result, "cancel": cancel_result}
    if command_type == "resume_trading":
        if runner.control_state().get("kill_switch_enabled"):
            raise ValueError("kill_switch_enabled")
        return runner.set_trading_enabled(True, reason="dashboard_command")
    if command_type == "cancel_all_quotes":
        return runner.cancel_all_quotes()
    if command_type == "kill_switch_on":
        kill_result = runner.set_kill_switch(True, reason="dashboard_command")
        cancel_result = runner.cancel_all_quotes()
        return {"kill_switch": kill_result, "cancel": cancel_result}
    if command_type == "kill_switch_off":
        if str(args.mode).upper() != "PAPER":
            raise ValueError("kill_switch_off_paper_only")
        return runner.set_kill_switch(False, reason="dashboard_command")
    if command_type == "apply_config_patch":
        patch = dict(payload.get("patch") or payload)
        applied: Dict[str, Any] = {}
        for key in ("cycle_secs", "refresh_market_secs"):
            if key in patch:
                runtime_controls[key] = max(0.1, float(patch[key]))
                applied[key] = float(runtime_controls[key])
        runner_applied = runner.apply_config_patch(patch)
        applied.update(runner_applied)
        return {"applied": applied}
    if command_type == "restart_paper_run_safe_profile":
        if str(args.mode).upper() != "PAPER":
            raise ValueError("restart_safe_profile_paper_only")
        cancel_result = runner.cancel_all_quotes()
        for key, value in safe_runtime_controls.items():
            runtime_controls[key] = value
        restore_result = runner.restore_safe_profile()
        runner.set_kill_switch(False, reason="safe_profile_restart")
        runner.set_trading_enabled(True, reason="safe_profile_restart")
        return {"cancel": cancel_result, "restored": restore_result, "runtime_controls": dict(runtime_controls)}
    if command_type == "flatten_event_inventory":
        return runner.request_flatten_event(str(payload.get("event_id") or ""))
    if command_type == "flatten_market_inventory":
        return runner.request_flatten_market(str(payload.get("market_id") or ""))
    raise ValueError(f"unsupported_command:{command_type}")


def _parse_args() -> argparse.Namespace:
    settings = load_settings()
    parser = argparse.ArgumentParser(description="Standalone core_mm runner")
    parser.add_argument("--exchange", choices=["polymarket", "kalshi"], default="polymarket", help="Target exchange")
    parser.add_argument("--mode", choices=["OBSERVE", "PAPER", "LIVE"], default="OBSERVE")
    parser.add_argument("--runtime-root", required=True)
    parser.add_argument("--duration-secs", type=float, default=120.0)
    parser.add_argument("--refresh-market-secs", type=float, default=60.0)
    parser.add_argument("--cycle-secs", type=float, default=1.0)
    parser.add_argument("--usdc-balance", type=float, default=1000.0)
    parser.add_argument("--strategy-allocated-equity", type=float, default=None, help="Strategy bankroll allocated to this bot for risk sizing")
    parser.add_argument("--safe-risk-profile", choices=["auto", "custom", "200", "500", "1000"], default="auto")
    parser.add_argument("--symbol", default="BTC")
    parser.add_argument("--symbols", default=None, help="Comma-separated symbols, e.g. BTC,ETH,SOL,XRP")
    parser.add_argument("--horizon", default="15m")
    parser.add_argument("--min-size", type=float, default=10.0)
    parser.add_argument("--fallback-size", type=float, default=2.0)
    parser.add_argument("--within-pct", type=float, default=0.06)
    parser.add_argument("--trade-size", type=float, default=12.0)
    parser.add_argument("--max-size", type=float, default=150.0)
    parser.add_argument("--min-order-size", type=float, default=None)
    parser.add_argument("--hard-position-cap", type=float, default=250.0)
    parser.add_argument("--market-dwell-secs", type=float, default=900.0)
    parser.add_argument("--fee-bps", type=float, default=float(settings.fee_rate_bps))
    parser.add_argument("--fee-mode", default=str(settings.fee_mode))
    parser.add_argument("--daily-loss-cap", type=float, default=-50.0, help="Halt if realized PnL <= this (USD)")
    parser.add_argument("--sleep-hours", type=float, default=0.0, help="Risk manager sleep after TP/SL (hours)")
    parser.add_argument("--max-skew-ticks", type=int, default=1, help="Max ticks to shift quote center for inventory skew (0=off)")
    parser.add_argument("--inventory-skew-factor", type=float, default=1.0, help="Continuous size skew factor (0=off, 1=full)")
    parser.add_argument("--kelly-fraction", type=float, default=0.0, help="Fractional Kelly multiplier for sizing (0=disabled, 0.25=quarter-Kelly)")
    parser.add_argument("--boundary-guard-mode", choices=["off", "static", "adaptive"], default="adaptive", help="Tail no-new-risk guard mode")
    parser.add_argument("--boundary-no-new-risk-min-price", type=float, default=0.10, help="Static-mode low-price no-new-risk boundary")
    parser.add_argument("--boundary-no-new-risk-max-price", type=float, default=0.90, help="Static-mode high-price no-new-risk boundary")
    parser.add_argument("--boundary-adverse-selection-threshold", type=float, default=0.50, help="Adaptive adverse-selection score required to block new buy risk")
    parser.add_argument("--boundary-exit-cost-multiplier", type=float, default=1.25, help="Required quoted edge multiple over estimated exit cost")
    parser.add_argument("--flow-filter-ewma-span", type=int, default=10, help="EWMA span for flow filter smoothing")
    parser.add_argument("--per-trade-loss-pct", type=float, default=0.02)
    parser.add_argument("--per-event-loss-pct", type=float, default=0.05)
    parser.add_argument("--per-day-loss-pct", type=float, default=0.10)
    parser.add_argument("--max-order-notional-pct", type=float, default=0.005)
    parser.add_argument("--max-market-exposure-pct", type=float, default=0.03)
    parser.add_argument("--max-event-exposure-pct", type=float, default=0.05)
    parser.add_argument("--stale-duration-scale", type=float, default=10.0 / 3600.0)
    parser.add_argument("--maker-exit-grace-secs", type=float, default=3.0)
    parser.add_argument("--cross-escalation-drawdown-pct", type=float, default=0.005)
    parser.add_argument("--stop-open-before-expiry-secs", type=float, default=180.0)
    parser.add_argument("--force-flat-before-expiry-secs", type=float, default=90.0)
    parser.add_argument("--reentry-cooldown-scale", type=float, default=3.0)
    parser.add_argument("--use-allocated-equity-for-risk", action="store_true", default=True)
    parser.add_argument("--no-use-allocated-equity-for-risk", dest="use_allocated_equity_for_risk", action="store_false")
    parser.add_argument("--risk-based-share-sizing", action="store_true", default=True)
    parser.add_argument("--no-risk-based-share-sizing", dest="risk_based_share_sizing", action="store_false")
    parser.add_argument("--pre-kill-warning-fraction", type=float, default=0.60)
    parser.add_argument("--skew-threshold-fraction", type=float, default=0.25)
    parser.add_argument("--hedge-threshold-fraction", type=float, default=0.60)
    parser.add_argument("--hedge-requires-stale-inventory", action="store_true", default=True)
    parser.add_argument("--no-hedge-requires-stale-inventory", dest="hedge_requires_stale_inventory", action="store_false")
    parser.add_argument("--hedge-quality-must-beat-inventory-market", action="store_true", default=True)
    parser.add_argument("--no-hedge-quality-must-beat-inventory-market", dest="hedge_quality_must_beat_inventory_market", action="store_false")
    parser.add_argument("--hedge-min-quality-score", type=float, default=10_000.0)
    parser.add_argument("--hedge-max-temp-gross-increase-fraction", type=float, default=0.10)
    parser.add_argument("--hedge-failure-cooldown-scale", type=float, default=1.0)
    parser.add_argument("--hedge-search-profile", choices=["production", "proof-only"], default="production")
    parser.add_argument("--proof-only-bucket-distance", type=int, default=2)
    parser.add_argument("--proof-only-expiry-slack-ms", type=int, default=60_000)
    parser.add_argument("--hedge-covariance-enabled", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--hedge-covariance-window-secs", type=float, default=600.0)
    parser.add_argument("--hedge-covariance-min-samples", type=int, default=5)
    parser.add_argument("--hedge-covariance-min-correlation", type=float, default=0.25)
    parser.add_argument("--hedge-covariance-min-abs-beta", type=float, default=0.05)
    parser.add_argument("--hedge-covariance-beta-clip", type=float, default=1.0)
    parser.add_argument("--hedge-covariance-beta-shrinkage", type=float, default=0.35)
    parser.add_argument("--hedge-covariance-max-sample-age-ms", type=int, default=30_000)
    parser.add_argument("--hedge-covariance-max-update-gap-ms", type=int, default=2_000)
    parser.add_argument("--hedge-covariance-boundary-buffer", type=float, default=0.08)
    parser.add_argument("--hedge-covariance-boundary-max-fraction", type=float, default=0.50)
    parser.add_argument("--hedge-covariance-strong-correlation", type=float, default=0.60)
    parser.add_argument("--hedge-covariance-strong-min-samples", type=int, default=8)
    parser.add_argument("--hedge-covariance-stability-ratio-max", type=float, default=3.0)
    parser.add_argument("--hedge-covariance-gate-required", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--observe-pause-interval-secs", type=float, default=1200.0)
    parser.add_argument("--observe-pause-duration-secs", type=float, default=10.0)
    parser.add_argument("--negative-pnl-reduce-only-enabled", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--negative-pnl-unwind-requires-worsening", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--negative-pnl-unwind-requires-stale-or-worsening", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument(
        "--post-fill-reentry-cooldown-secs",
        type=float,
        default=0.0,
        help="Cooldown after a sell fill before the bot can re-buy the same token (0=off)",
    )
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
    parser.add_argument("--kill-switch-report", default=None, help="Optional JSON report path from scripts/test_kill_switch.py")
    return parser.parse_args()


async def _main() -> None:
    args = _parse_args()
    args = _apply_safe_profile_defaults(args)
    symbols = tuple(
        symbol.strip().upper()
        for symbol in (args.symbols.split(",") if args.symbols else [args.symbol])
        if symbol.strip()
    )
    primary_symbol = symbols[0] if symbols else str(args.symbol).strip().upper()
    if args.exchange == "kalshi" and float(args.market_dwell_secs) == 900.0:
        args.market_dwell_secs = 60.0
    if args.exchange == "kalshi" and float(args.post_fill_reentry_cooldown_secs) == 0.0:
        args.post_fill_reentry_cooldown_secs = 60.0
    # Auto-generate run name if not provided
    if args.run_name is None:
        ts_label = datetime.now(timezone.utc).strftime("%b%d-%H%M")
        args.run_name = f"{args.mode.lower()} {','.join(symbols)} {ts_label}"
    runtime_root = Path(args.runtime_root).resolve()
    meta_dir = runtime_root / "meta"
    meta_dir.mkdir(parents=True, exist_ok=True)
    startup_reconciliation: Dict[str, Any] = {"status": "not_applicable", "ok": args.mode != "LIVE"}
    kill_switch_validation = _read_optional_json(args.kill_switch_report) or {"status": "not_run"}
    _write_status(
        meta_dir,
        {
            "mode": args.mode,
            "stage": "bootstrapping",
            "run_name": args.run_name,
            "market": None,
            "token_ids": [],
            "feed": {"connected": False, "subscribed_token_ids": [], "received_messages": 0, "applied_book_updates": 0},
            "runner": {"mode": args.mode, "market_id": None, "token_ids": [], "has_books": False, "selection": {}, "active_market_health": {}, "cluster_exposure": {}, "cluster_hedge": {}},
            "selection": {},
            "active_market_health": {},
            "cluster_exposure": {},
            "cluster_hedge": {},
            "decisions": 0,
            "order_actions": 0,
            "fills": 0,
            "fill_rate_snapshot": 0.0,
            "last_error": None,
            "startup_reconciliation": startup_reconciliation,
            "kill_switch_validation": kill_switch_validation,
            "symbols": list(symbols),
            "updated_at_ms": int(time.time() * 1000),
        },
    )
    is_kalshi = args.exchange == "kalshi"
    risk_config = RiskConfig(
        sleep_hours=args.sleep_hours,
        hard_position_cap=args.hard_position_cap,
        max_total_position_notional=args.max_total_position_notional,
        max_markets_with_position=args.max_markets_with_position,
        per_trade_loss_pct=args.per_trade_loss_pct,
        per_event_loss_pct=args.per_event_loss_pct,
        per_day_loss_pct=args.per_day_loss_pct,
        max_order_notional_pct=args.max_order_notional_pct,
        max_market_exposure_pct=args.max_market_exposure_pct,
        max_event_exposure_pct=args.max_event_exposure_pct,
        stale_duration_scale=args.stale_duration_scale,
        maker_exit_grace_secs=args.maker_exit_grace_secs,
        cross_escalation_drawdown_pct=args.cross_escalation_drawdown_pct,
        stop_open_before_expiry_secs=args.stop_open_before_expiry_secs,
        force_flat_before_expiry_secs=args.force_flat_before_expiry_secs,
        reentry_cooldown_scale=args.reentry_cooldown_scale,
        strategy_allocated_equity=args.strategy_allocated_equity,
        use_allocated_equity_for_risk=args.use_allocated_equity_for_risk,
        risk_based_share_sizing=args.risk_based_share_sizing,
        pre_kill_warning_fraction=args.pre_kill_warning_fraction,
        negative_pnl_reduce_only_enabled=args.negative_pnl_reduce_only_enabled,
        negative_pnl_unwind_requires_worsening=args.negative_pnl_unwind_requires_worsening,
        negative_pnl_unwind_requires_stale_or_worsening=args.negative_pnl_unwind_requires_stale_or_worsening,
    )

    # Build exchange-specific components
    live_broker = None
    user_feed = None
    kalshi_client = None
    kalshi_fill_poller = None

    if is_kalshi:
        # ── Kalshi exchange ──
        from core_mm.kalshi.client import KalshiClient, KalshiOrderArgs, load_private_key_from_path
        from core_mm.kalshi.market_selector import KalshiMarketSelector, KalshiSelectorConfig

        settings = load_settings()
        if not settings.kalshi_api_key_id or not settings.kalshi_private_key_path:
            raise RuntimeError("Kalshi requires KALSHI_API_KEY_ID and KALSHI_PRIVATE_KEY_PATH in .env")
        kalshi_pk = load_private_key_from_path(settings.kalshi_private_key_path)
        kalshi_client = KalshiClient(
            api_key_id=settings.kalshi_api_key_id,
            private_key=kalshi_pk,
            base_url=settings.kalshi_base_url,
        )
        selector = KalshiMarketSelector(
            client=kalshi_client,
            config=KalshiSelectorConfig(
                series_ticker=primary_symbol if primary_symbol else None,
                min_price=0.10,
                max_price=0.90,
            ),
        )
        if args.mode == "LIVE":
            from core_mm.execution import ExecutionAdapter
            from core_mm.live_broker import LiveBroker
            exec_adapter = ExecutionAdapter(
                client=kalshi_client,
                order_args_type=KalshiOrderArgs,
                order_type=type("_KalshiTIF", (), {"GTC": "gtc"})(),
            )
            live_broker = LiveBroker(
                execution_adapter=exec_adapter,
                fee_bps=args.fee_bps,
                max_order_notional=args.max_order_notional,
                max_position_notional=args.max_position_notional,
                max_daily_loss=args.max_daily_loss,
            )
            print(f"[LIVE/Kalshi] Broker ready. Risk limits: order=${args.max_order_notional}, pos=${args.max_position_notional}, daily_loss=${args.max_daily_loss}")
    else:
        # ── Polymarket exchange (default) ──
        selector = MarketSelector()
        selector.config = type(selector.config)(symbol=primary_symbol, symbols=symbols, horizon=args.horizon)
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

    if args.mode == "LIVE" and live_broker is not None:
        startup_reconciliation = live_broker.startup_reconcile()
        print(
            "[LIVE] Startup reconciliation: "
            f"status={startup_reconciliation.get('status')} "
            f"ok={startup_reconciliation.get('ok')} "
            f"open_orders={startup_reconciliation.get('open_order_count')} "
            f"positions={startup_reconciliation.get('position_count')}"
        )
        if not startup_reconciliation.get("ok"):
            _write_status(
                meta_dir,
                {
                    "mode": args.mode,
                    "stage": "startup_reconciliation_blocked",
                    "run_name": args.run_name,
                    "market": None,
                    "token_ids": [],
                    "feed": {"connected": False, "subscribed_token_ids": [], "received_messages": 0, "applied_book_updates": 0},
                    "runner": {"mode": args.mode, "market_id": None, "token_ids": [], "has_books": False, "selection": {}, "active_market_health": {}, "cluster_exposure": {}, "cluster_hedge": {}},
                    "selection": {},
                    "active_market_health": {},
                    "cluster_exposure": {},
                    "cluster_hedge": {},
                    "decisions": 0,
                    "order_actions": 0,
                    "fills": 0,
                    "fill_rate_snapshot": 0.0,
                    "last_error": f"startup_reconciliation_blocked: {startup_reconciliation.get('reason') or 'unknown'}",
                    "startup_reconciliation": startup_reconciliation,
                    "kill_switch_validation": kill_switch_validation,
                    "symbols": list(symbols),
                    "updated_at_ms": int(time.time() * 1000),
                },
            )
            raise RuntimeError(f"startup_reconciliation_blocked: {startup_reconciliation.get('reason') or 'unknown'}")
        _write_status(
            meta_dir,
            {
                "mode": args.mode,
                "stage": "startup_reconciled",
                "run_name": args.run_name,
                "market": None,
                "token_ids": [],
                "feed": {"connected": False, "subscribed_token_ids": [], "received_messages": 0, "applied_book_updates": 0},
                "runner": {"mode": args.mode, "market_id": None, "token_ids": [], "has_books": False, "selection": {}, "active_market_health": {}, "cluster_exposure": {}, "cluster_hedge": {}},
                "selection": {},
                "active_market_health": {},
                "cluster_exposure": {},
                "cluster_hedge": {},
                "decisions": 0,
                "order_actions": 0,
                "fills": 0,
                "fill_rate_snapshot": 0.0,
                "last_error": None,
                "startup_reconciliation": startup_reconciliation,
                "kill_switch_validation": kill_switch_validation,
                "symbols": list(symbols),
                "updated_at_ms": int(time.time() * 1000),
            },
        )

    complement_arb_config = ComplementArbConfig(
        enabled=args.complement_arb,
        min_maker_edge_bps=args.complement_arb_min_maker_edge_bps,
        fee_bps=args.fee_bps,
        fee_model_exchange="kalshi" if is_kalshi else None,
        fee_type="quadratic" if is_kalshi else None,
        fee_multiplier=1.0,
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
        min_order_size_override=args.min_order_size,
        fee_bps=args.fee_bps,
        fee_mode=args.fee_mode,
        market_dwell_ms=int(max(0.0, float(args.market_dwell_secs)) * 1000),
        boundary_no_new_risk_min_price=args.boundary_no_new_risk_min_price,
        boundary_no_new_risk_max_price=args.boundary_no_new_risk_max_price,
        boundary_guard_mode=args.boundary_guard_mode,
        boundary_adverse_selection_threshold=args.boundary_adverse_selection_threshold,
        boundary_exit_cost_multiplier=args.boundary_exit_cost_multiplier,
        max_skew_ticks=args.max_skew_ticks,
        inventory_skew_factor=args.inventory_skew_factor,
        kelly_fraction=args.kelly_fraction,
        flow_filter_ewma_span=args.flow_filter_ewma_span,
        post_fill_reentry_cooldown_ms=int(max(0.0, float(args.post_fill_reentry_cooldown_secs)) * 1000),
        risk_config=risk_config,
        strategy_allocated_equity=args.strategy_allocated_equity,
        use_allocated_equity_for_risk=args.use_allocated_equity_for_risk,
        risk_based_share_sizing=args.risk_based_share_sizing,
        safe_risk_profile=args.safe_risk_profile,
        max_active_markets=args.max_active_markets,
        skew_threshold_fraction=args.skew_threshold_fraction,
        hedge_threshold_fraction=args.hedge_threshold_fraction,
        hedge_requires_stale_inventory=args.hedge_requires_stale_inventory,
        hedge_quality_must_beat_inventory_market=args.hedge_quality_must_beat_inventory_market,
        hedge_min_quality_score=args.hedge_min_quality_score,
        hedge_max_temp_gross_increase_fraction=args.hedge_max_temp_gross_increase_fraction,
        hedge_failure_cooldown_scale=args.hedge_failure_cooldown_scale,
        hedge_search_profile=args.hedge_search_profile,
        proof_only_bucket_distance=args.proof_only_bucket_distance,
        proof_only_expiry_slack_ms=args.proof_only_expiry_slack_ms,
        hedge_covariance_enabled=args.hedge_covariance_enabled,
        hedge_covariance_window_secs=args.hedge_covariance_window_secs,
        hedge_covariance_min_samples=args.hedge_covariance_min_samples,
        hedge_covariance_min_correlation=args.hedge_covariance_min_correlation,
        hedge_covariance_min_abs_beta=args.hedge_covariance_min_abs_beta,
        hedge_covariance_beta_clip=args.hedge_covariance_beta_clip,
        hedge_covariance_beta_shrinkage=args.hedge_covariance_beta_shrinkage,
        hedge_covariance_max_sample_age_ms=args.hedge_covariance_max_sample_age_ms,
        hedge_covariance_max_update_gap_ms=args.hedge_covariance_max_update_gap_ms,
        hedge_covariance_boundary_buffer=args.hedge_covariance_boundary_buffer,
        hedge_covariance_boundary_max_fraction=args.hedge_covariance_boundary_max_fraction,
        hedge_covariance_strong_correlation=args.hedge_covariance_strong_correlation,
        hedge_covariance_strong_min_samples=args.hedge_covariance_strong_min_samples,
        hedge_covariance_stability_ratio_max=args.hedge_covariance_stability_ratio_max,
        hedge_covariance_gate_required=args.hedge_covariance_gate_required,
        observe_pause_interval_secs=args.observe_pause_interval_secs,
        observe_pause_duration_secs=args.observe_pause_duration_secs,
        negative_pnl_reduce_only_enabled=args.negative_pnl_reduce_only_enabled,
        negative_pnl_unwind_requires_worsening=args.negative_pnl_unwind_requires_worsening,
        negative_pnl_unwind_requires_stale_or_worsening=args.negative_pnl_unwind_requires_stale_or_worsening,
        cycle_hint_ms=int(max(0.1, float(args.cycle_secs)) * 1000.0),
        complement_arb_config=complement_arb_config,
    )
    telemetry = StandaloneTelemetry(
        runtime_root=runtime_root,
        book_manager=runner.book_manager,
        position_tracker=runner.position_tracker,
        mode=args.mode,
    )
    control_store = ControlCommandStore(telemetry.db_path)
    run_id = runtime_root.name
    runtime_controls: Dict[str, Any] = {
        "cycle_secs": max(0.1, float(args.cycle_secs)),
        "refresh_market_secs": max(1.0, float(args.refresh_market_secs)),
    }
    safe_runtime_controls = dict(runtime_controls)
    on_applied_update = runner.broker.sweep_fills if hasattr(runner.broker, "sweep_fills") else None
    if is_kalshi:
        from core_mm.kalshi.market_feed import KalshiMarketFeed
        feed = KalshiMarketFeed(client=kalshi_client, book_manager=runner.book_manager, tickers=(), on_applied_update=on_applied_update, poll_interval_secs=0.5)
    else:
        feed = PolymarketMarketFeed(book_manager=runner.book_manager, token_ids=(), on_applied_update=on_applied_update)

    try:
        changed = runner.refresh_market_selection(now_ms=int(time.time() * 1000))
    except Exception as exc:
        _write_status(
            meta_dir,
            {
                "run_id": runtime_root.name,
                "mode": args.mode,
                "stage": "market_selection_failed",
                "market": None,
                "token_ids": [],
                "feed": {"connected": False, "subscribed_token_ids": [], "received_messages": 0, "applied_book_updates": 0},
                "runner": {"mode": args.mode, "market_id": None, "token_ids": [], "has_books": False, "selection": {}, "active_market_health": {}, "cluster_exposure": {}, "cluster_hedge": {}},
                "selection": {},
                "active_market_health": {},
                "cluster_exposure": {},
                "cluster_hedge": {},
                "decisions": 0,
                "order_actions": 0,
                "fills": 0,
                "fill_rate_snapshot": 0.0,
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
                    "min_order_size": args.min_order_size,
                },
            },
        )
        raise
    if changed and runner.active_markets:
        feed.set_token_ids(runner.hedge_search_token_ids)

    feed_task = asyncio.create_task(feed.run())
    user_feed_task = None
    if is_kalshi and args.mode == "LIVE" and kalshi_client is not None and live_broker is not None:
        from core_mm.kalshi.fill_poller import KalshiFillPoller
        def _on_kalshi_fill(fill_msg: Dict[str, Any]) -> None:
            runner.on_user_message(fill_msg)
            if fill_msg.get("event_type") == "trade":
                live_broker.record_fill(fill_msg)
        kalshi_fill_poller = KalshiFillPoller(client=kalshi_client, on_fill=_on_kalshi_fill)
        user_feed_task = asyncio.create_task(kalshi_fill_poller.run())
    elif user_feed is not None and live_broker is not None:
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
    next_refresh = started + float(runtime_controls["refresh_market_secs"])
    decisions = 0
    orders = 0
    fills = 0
    last_error = None
    try:
        while (time.time() - started) < args.duration_secs:
            now = time.time()
            control_store.expire_stale_commands(
                runtime_root=runtime_root.as_posix(),
                active_run_id=run_id,
                ts_ms=int(now * 1000),
            )
            pending_commands = control_store.fetch_pending_commands(
                runtime_root=runtime_root.as_posix(),
                active_run_id=run_id,
                limit=10,
            )
            for command in pending_commands:
                validation_errors = validate_command(args.mode, command.command_type, command.payload)
                if validation_errors:
                    control_store.mark_command(
                        command_id=command.command_id,
                        status="rejected",
                        event_type="rejected",
                        result={"errors": validation_errors},
                        ts_ms=int(now * 1000),
                    )
                    continue
                control_store.mark_command(
                    command_id=command.command_id,
                    status="acknowledged",
                    event_type="acknowledged",
                    result={"command_type": command.command_type},
                    ts_ms=int(now * 1000),
                )
                try:
                    result = _apply_control_command(
                        command,
                        args=args,
                        runner=runner,
                        runtime_controls=runtime_controls,
                        safe_runtime_controls=safe_runtime_controls,
                    )
                except Exception as exc:
                    control_store.mark_command(
                        command_id=command.command_id,
                        status="rejected",
                        event_type="rejected",
                        result={"reason": str(exc)},
                        ts_ms=int(now * 1000),
                    )
                    continue
                control_store.mark_command(
                    command_id=command.command_id,
                    status="applied",
                    event_type="applied",
                    result=result,
                    ts_ms=int(now * 1000),
                )
            if not runner.active_markets or now >= next_refresh:
                changed = runner.refresh_market_selection(now_ms=int(now * 1000))
                if runner.active_markets and changed:
                    feed.set_token_ids(runner.hedge_search_token_ids)
                next_refresh = now + float(runtime_controls["refresh_market_secs"])

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
                        token_market = next((m for m in runner.active_markets if token_id in m.token_ids), None)
                        cooldown_ms = int(max(0.0, float(args.post_fill_reentry_cooldown_secs)) * 1000.0)
                        if token_market is not None:
                            cooldown_ms = runner.risk_manager.reentry_cooldown_ms(
                                runner._market_duration_ms(token_market)
                            )
                        runner.main_loop.record_fill(
                            token_id=token_id,
                            side=side,
                            price=price,
                            mid_at_fill=mid,
                            ts_ms=int(fill_evt.get("ts_ms") or 0),
                            cooldown_ms=cooldown_ms,
                        )

            # Daily loss cap check
            if hasattr(runner.broker, "stats"):
                _broker_stats = runner.broker.stats()
                _net_pnl = _broker_stats.get("realized_net_pnl", 0.0)
                if _net_pnl <= args.daily_loss_cap:
                    last_error = f"DAILY LOSS CAP HIT: PnL ${_net_pnl:.2f} <= ${args.daily_loss_cap:.2f}"
                    break

            feed_snapshot = asdict(feed.status())
            config_payload = _build_config_payload(args, symbols=symbols, runtime_controls=runtime_controls, runner=runner)
            telemetry.record_cycle(
                now_ms=int(now * 1000),
                runner=runner,
                results=cycle_results,
                feed_status=feed_snapshot,
                last_error=last_error,
                config=config_payload,
            )

            runner_status = asdict(runner.status())
            fills_count = len(runner.broker.fills()) if hasattr(runner.broker, "fills") else fills
            status = {
                "mode": args.mode,
                "run_id": run_id,
                "run_name": args.run_name,
                "market": runner.current_market.slug if runner.current_market is not None else None,
                "markets": [m.slug for m in runner.active_markets],
                "token_ids": list(runner.all_token_ids),
                "feed": feed_snapshot,
                "runner": runner_status,
                "selection": runner_status.get("selection", {}),
                "active_market_health": runner_status.get("active_market_health", {}),
                "cluster_exposure": runner_status.get("cluster_exposure", {}),
                "cluster_hedge": runner_status.get("cluster_hedge", {}),
                "decisions": decisions,
                "order_actions": orders,
                "fills": fills_count,
                "fill_rate_snapshot": float(fills_count / max(orders, 1)),
                "last_error": last_error,
                "startup_reconciliation": startup_reconciliation,
                "kill_switch_validation": kill_switch_validation,
                "symbols": list(symbols),
                "run_id": run_id,
                "runtime_db_path": telemetry.db_path.as_posix(),
                "run_summary_path": (telemetry.meta_dir / "run_summary.json").as_posix(),
                "updated_at_ms": int(time.time() * 1000),
                "config": config_payload,
                "control_state": {
                    **runner.control_state(),
                    "cycle_secs": float(runtime_controls["cycle_secs"]),
                    "refresh_market_secs": float(runtime_controls["refresh_market_secs"]),
                },
            }
            if not runner.active_markets:
                status["stage"] = "awaiting_market"
            elif int(feed_snapshot.get("applied_book_updates") or feed_snapshot.get("applied_snapshots") or 0) <= 0:
                status["stage"] = "awaiting_books"
            else:
                status["stage"] = "running"
            _write_status(meta_dir, status)
            await asyncio.sleep(float(runtime_controls["cycle_secs"]))
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
        if kalshi_fill_poller is not None:
            kalshi_fill_poller.stop()
        if user_feed is not None:
            user_feed.stop()
        await asyncio.sleep(0)
        if not feed_task.done():
            try:
                await asyncio.wait_for(feed_task, timeout=5.0)
            except (TimeoutError, asyncio.CancelledError):
                feed_task.cancel()
        if user_feed_task is not None and not user_feed_task.done():
            try:
                await asyncio.wait_for(user_feed_task, timeout=5.0)
            except (TimeoutError, asyncio.CancelledError):
                user_feed_task.cancel()


if __name__ == "__main__":
    asyncio.run(_main())
