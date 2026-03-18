from __future__ import annotations

import argparse
import asyncio
from pathlib import Path
from typing import Optional

from config.settings import load_markets, load_settings, validate_markets_config
from core.decision_tape import DecisionTape
from core.market_discovery import GAMMA_BASE_URL, load_resolved_markets, resolve_markets
from core.model_artifact import load_model
from core.onchain_signals import load_whales
from core.order_book import OrderBook
from core.replay import ReplayRunner
from core.reference_store import ReferenceStore
from core.validators import OrderConstraints
from scripts.walkforward_report import generate_report, write_report


def main() -> None:
    args = _parse_args()
    settings = load_settings()
    log_dir = args.log_dir or settings.log_dir
    markets_path = args.markets or settings.track_markets_yaml
    markets = load_markets(markets_path)
    resolved_markets, asset_meta = resolve_replay_markets(
        markets=markets,
        log_dir=log_dir,
        resolved_arg=args.resolved_markets,
        auto_discover=args.auto_discover,
        no_network=args.no_network,
    )
    asset_ids = [token for market in resolved_markets for token in market.token_ids if token]
    if not asset_ids:
        raise ValueError("no_asset_ids_configured")

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

    decision_tape = DecisionTape(log_dir=log_dir, run_id="replay")
    model_artifact = None
    model_load_error = None
    model_path = args.model
    if model_path:
        try:
            model_artifact = load_model(model_path)
        except Exception as exc:
            model_load_error = f"MODEL_LOAD_ERROR:{exc}"
    reference_store = ReferenceStore()
    whales = load_whales(settings.onchain_whales_path)
    runner = ReplayRunner(
        books=books,
        constraints=constraints,
        decision_tape=decision_tape,
        order_size=settings.dry_run_size,
        fee_rate=settings.fee_rate_bps / 10_000.0,
        fee_mode=settings.fee_mode,
        market_meta=asset_meta,
        model_artifact=model_artifact,
        model_path=model_path,
        model_load_error=model_load_error,
        reference_store=reference_store,
        onchain_whales=whales,
        onchain_window_secs=settings.onchain_window_secs,
        reference_settings={
            "staleness_ms": settings.reference_staleness_ms,
            "lag_guard_ms": settings.reference_lag_guard_ms,
            "disagreement_bps": settings.reference_disagreement_bps,
            "min_confidence": settings.reference_min_confidence,
            "allow_partial": settings.reference_allow_partial,
            "partial_confidence": settings.reference_partial_confidence,
            "allowed_symbols": {market.reference_symbol for market in resolved_markets},
            "hl_vol_sec": settings.hl_vol_sec,
        },
        policy_settings={
            "edge_min": settings.edge_min,
            "edge_exit": settings.edge_exit,
            "edge_stop": settings.edge_stop,
            "z_mom_min": settings.z_mom_min,
            "t_min_secs": settings.t_min_secs,
            "hold_max_secs": settings.hold_max_secs,
            "vol_pct_hi": settings.vol_pct_hi,
            "edge_min_mult_hivol": settings.edge_min_mult_hivol,
            "tox_max": settings.tox_max,
            "hedge_min": settings.hedge_min,
            "hedge_max": settings.hedge_max,
            "hedge_required_vol_pct": settings.hedge_required_vol_pct,
            "pf_bias": settings.pf_bias,
            "pf_w_mom": settings.pf_w_mom,
            "pf_w_revert": settings.pf_w_revert,
            "pf_z_clip": settings.pf_z_clip,
            "pf_vol_dampen_enabled": settings.pf_vol_dampen_enabled,
            "pf_vol_floor": settings.pf_vol_floor,
        },
    )
    runner.run(_resolve_inputs(args.inputs))
    decision_tape.close()
    if args.walkforward:
        decision_paths = sorted(Path(log_dir).glob("decision_*.jsonl"))
        reference_paths = sorted(Path(log_dir).glob("reference_*.jsonl"))
        report = generate_report(
            decision_paths=decision_paths,
            reference_paths=reference_paths,
            train_days=args.walkforward_train_days,
            test_days=args.walkforward_test_days,
            step_days=args.walkforward_step_days,
            fee_bps_shift=args.walkforward_fee_bps_shift,
            slippage_bps_shift=args.walkforward_slippage_bps_shift,
            latency_threshold_ms=args.walkforward_latency_threshold_ms,
            latency_shift_ms=args.walkforward_latency_shift_ms,
        )
        write_report(report, Path(args.walkforward_out or log_dir))


def _resolve_inputs(inputs: list[str]) -> list[str]:
    paths: list[str] = []
    for entry in inputs:
        path = Path(entry)
        if path.is_dir():
            patterns = ["market_*.jsonl", "reference_*.jsonl", "onchain_*.jsonl"]
            for pattern in patterns:
                paths.extend(str(p) for p in sorted(path.glob(pattern)))
        else:
            paths.append(str(path))
    return paths


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Replay market event tape to decision tape")
    parser.add_argument("inputs", nargs="+", help="Event tape files or directory")
    parser.add_argument("--markets", default=None, help="Path to markets.yaml")
    parser.add_argument("--log-dir", default=None, help="Override LOG_DIR")
    parser.add_argument("--auto_discover", action="store_true", help="Resolve markets via Gamma API")
    parser.add_argument("--resolved-markets", default=None, help="Path to resolved_markets JSON")
    parser.add_argument("--no_network", action="store_true", help="Require resolved artifact; forbid Gamma")
    parser.add_argument("--model", default=None, help="Path to trained model artifact JSON")
    parser.add_argument("--walkforward", action="store_true", help="Generate walk-forward report after replay")
    parser.add_argument("--walkforward-out", default=None, help="Output directory for walk-forward report")
    parser.add_argument("--walkforward-train-days", type=int, default=7)
    parser.add_argument("--walkforward-test-days", type=int, default=1)
    parser.add_argument("--walkforward-step-days", type=int, default=1)
    parser.add_argument("--walkforward-fee-bps-shift", type=float, default=0.0)
    parser.add_argument("--walkforward-slippage-bps-shift", type=float, default=0.0)
    parser.add_argument("--walkforward-latency-threshold-ms", type=int, default=2000)
    parser.add_argument("--walkforward-latency-shift-ms", type=int, default=0)
    return parser.parse_args()


def _resolve_resolved_markets(resolved_arg: Optional[str], log_dir: str) -> Optional[Path]:
    if resolved_arg:
        return Path(resolved_arg)
    log_path = Path(log_dir)
    candidates = list(log_path.glob("*/resolved_markets.json"))
    candidates.extend(log_path.glob("resolved_markets_*.json"))
    if not candidates:
        return None
    candidates.sort(key=lambda path: path.stat().st_mtime)
    return candidates[-1]


def resolve_replay_markets(
    markets,
    log_dir: str,
    resolved_arg: Optional[str],
    auto_discover: bool,
    no_network: bool,
):
    resolved_path = _resolve_resolved_markets(resolved_arg, log_dir)
    if resolved_path is not None:
        return load_resolved_markets(resolved_path)
    if no_network:
        raise ValueError("resolved_markets_artifact_missing_no_network")
    validate_markets_config(markets, auto_discover=auto_discover)
    return asyncio.run(
        resolve_markets(
            markets=markets,
            auto_discover=auto_discover,
            cache_path=Path(log_dir) / "cache_gamma_markets.json",
            gamma_base_url=GAMMA_BASE_URL,
        )
    )


if __name__ == "__main__":
    main()
