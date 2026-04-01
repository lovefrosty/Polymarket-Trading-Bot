from __future__ import annotations

import argparse
import itertools
import json
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.score_calibration_runs import CalibrationWeights, score_runtime_root  # noqa: E402


DEFAULT_STALE_FAST = 8.0 / 3600.0
DEFAULT_STALE_HOLDTAIL = 7.0 / 3600.0


@dataclass(frozen=True)
class SweepVariant:
    key: str
    max_active_markets: int
    hedge_threshold_fraction: float
    stale_duration_scale: float
    maker_exit_grace_secs: float
    force_flat_before_expiry_secs: float


def _parse_csv_floats(raw: Optional[str], default: Sequence[float]) -> List[float]:
    if not raw:
        return list(default)
    return [float(item.strip()) for item in raw.split(",") if item.strip()]


def _parse_csv_ints(raw: Optional[str], default: Sequence[int]) -> List[int]:
    if not raw:
        return list(default)
    return [int(item.strip()) for item in raw.split(",") if item.strip()]


def build_preset_variants(preset: str) -> List[SweepVariant]:
    if preset == "overnight_profiles":
        return [
            SweepVariant(
                key="conservative",
                max_active_markets=2,
                hedge_threshold_fraction=0.60,
                stale_duration_scale=DEFAULT_STALE_FAST,
                maker_exit_grace_secs=2.0,
                force_flat_before_expiry_secs=120.0,
            ),
            SweepVariant(
                key="proof045",
                max_active_markets=3,
                hedge_threshold_fraction=0.45,
                stale_duration_scale=DEFAULT_STALE_FAST,
                maker_exit_grace_secs=2.0,
                force_flat_before_expiry_secs=120.0,
            ),
            SweepVariant(
                key="proof040",
                max_active_markets=3,
                hedge_threshold_fraction=0.40,
                stale_duration_scale=DEFAULT_STALE_FAST,
                maker_exit_grace_secs=2.0,
                force_flat_before_expiry_secs=120.0,
            ),
            SweepVariant(
                key="holdtail",
                max_active_markets=3,
                hedge_threshold_fraction=0.45,
                stale_duration_scale=DEFAULT_STALE_HOLDTAIL,
                maker_exit_grace_secs=1.5,
                force_flat_before_expiry_secs=150.0,
            ),
        ]
    if preset == "proof045_max_active":
        return [
            SweepVariant(
                key="proof045_m3_control",
                max_active_markets=3,
                hedge_threshold_fraction=0.45,
                stale_duration_scale=DEFAULT_STALE_FAST,
                maker_exit_grace_secs=2.0,
                force_flat_before_expiry_secs=120.0,
            ),
            SweepVariant(
                key="proof045_m4",
                max_active_markets=4,
                hedge_threshold_fraction=0.45,
                stale_duration_scale=DEFAULT_STALE_FAST,
                maker_exit_grace_secs=2.0,
                force_flat_before_expiry_secs=120.0,
            ),
            SweepVariant(
                key="proof045_m5",
                max_active_markets=5,
                hedge_threshold_fraction=0.45,
                stale_duration_scale=DEFAULT_STALE_FAST,
                maker_exit_grace_secs=2.0,
                force_flat_before_expiry_secs=120.0,
            ),
            SweepVariant(
                key="proof045_m6",
                max_active_markets=6,
                hedge_threshold_fraction=0.45,
                stale_duration_scale=DEFAULT_STALE_FAST,
                maker_exit_grace_secs=2.0,
                force_flat_before_expiry_secs=120.0,
            ),
        ]
    raise ValueError(f"unsupported preset: {preset}")


def build_grid_variants(
    *,
    max_active_markets: Sequence[int],
    hedge_threshold_fractions: Sequence[float],
    stale_duration_scales: Sequence[float],
    maker_exit_grace_secs: Sequence[float],
    force_flat_before_expiry_secs: Sequence[float],
) -> List[SweepVariant]:
    variants: List[SweepVariant] = []
    for idx, combo in enumerate(
        itertools.product(
            max_active_markets,
            hedge_threshold_fractions,
            stale_duration_scales,
            maker_exit_grace_secs,
            force_flat_before_expiry_secs,
        ),
        start=1,
    ):
        max_active, hedge_threshold, stale_scale, maker_grace, force_flat = combo
        variants.append(
            SweepVariant(
                key=f"grid-{idx:02d}-m{max_active}-h{hedge_threshold:.2f}-s{stale_scale:.6f}-g{maker_grace:.1f}-f{int(force_flat)}",
                max_active_markets=int(max_active),
                hedge_threshold_fraction=float(hedge_threshold),
                stale_duration_scale=float(stale_scale),
                maker_exit_grace_secs=float(maker_grace),
                force_flat_before_expiry_secs=float(force_flat),
            )
        )
    return variants


def build_variants(args: argparse.Namespace) -> List[SweepVariant]:
    custom_lists_supplied = any(
        raw
        for raw in (
            args.max_active_markets_values,
            args.hedge_threshold_fraction_values,
            args.stale_duration_scale_values,
            args.maker_exit_grace_secs_values,
            args.force_flat_before_expiry_secs_values,
        )
    )
    if custom_lists_supplied:
        return build_grid_variants(
            max_active_markets=_parse_csv_ints(args.max_active_markets_values, [2, 3]),
            hedge_threshold_fractions=_parse_csv_floats(args.hedge_threshold_fraction_values, [0.60, 0.45, 0.40]),
            stale_duration_scales=_parse_csv_floats(args.stale_duration_scale_values, [DEFAULT_STALE_FAST]),
            maker_exit_grace_secs=_parse_csv_floats(args.maker_exit_grace_secs_values, [2.0]),
            force_flat_before_expiry_secs=_parse_csv_floats(args.force_flat_before_expiry_secs_values, [120.0]),
        )
    return build_preset_variants(args.preset)


def _runtime_root_for(suite_root: Path, index: int, variant: SweepVariant) -> Path:
    return suite_root / f"{index:02d}-{variant.key}"


def _run_subprocess(cmd: List[str]) -> None:
    subprocess.run(cmd, cwd=REPO_ROOT.as_posix(), check=True)


def _run_variant(
    *,
    suite_root: Path,
    index: int,
    variant: SweepVariant,
    duration_secs: int,
    exchange: str,
    symbol: str,
    safe_risk_profile: str,
    allocated_equity: float,
) -> Dict[str, Any]:
    runtime_root = _runtime_root_for(suite_root, index, variant)
    runtime_root.mkdir(parents=True, exist_ok=True)
    run_name = f"Sweep {index:02d} {variant.key}"
    cmd = [
        sys.executable,
        "scripts/run_core_mm.py",
        "--exchange",
        exchange,
        "--mode",
        "PAPER",
        "--runtime-root",
        runtime_root.as_posix(),
        "--run-name",
        run_name,
        "--duration-secs",
        str(duration_secs),
        "--symbol",
        symbol,
        "--safe-risk-profile",
        safe_risk_profile,
        "--strategy-allocated-equity",
        str(allocated_equity),
        "--kelly-fraction",
        "0.0",
        "--max-active-markets",
        str(variant.max_active_markets),
        "--max-market-exposure-pct",
        "0.03",
        "--max-event-exposure-pct",
        "0.04",
        "--pre-kill-warning-fraction",
        "0.60",
        "--hedge-threshold-fraction",
        str(variant.hedge_threshold_fraction),
        "--stale-duration-scale",
        str(variant.stale_duration_scale),
        "--maker-exit-grace-secs",
        str(variant.maker_exit_grace_secs),
        "--force-flat-before-expiry-secs",
        str(variant.force_flat_before_expiry_secs),
        "--negative-pnl-reduce-only-enabled",
        "--negative-pnl-unwind-requires-worsening",
        "--negative-pnl-unwind-requires-stale-or-worsening",
    ]
    _run_subprocess(cmd)
    _run_subprocess(
        [
            sys.executable,
            "scripts/report_core_mm_run.py",
            "--runtime-root",
            runtime_root.as_posix(),
        ]
    )
    scored = score_runtime_root(runtime_root)
    return {
        "variant": asdict(variant),
        "runtime_root": runtime_root.as_posix(),
        "score": scored["score"],
        "headline": scored["headline"],
        "components": scored["components"],
    }


def _format_results(rows: List[Dict[str, Any]]) -> str:
    lines = []
    lines.append("rank | score | pnl | hedge | quoteable | hold_p90 | variant")
    for idx, row in enumerate(rows, start=1):
        headline = row["headline"]
        variant = row["variant"]
        lines.append(
            f"{idx:>4} | "
            f"{row['score']:>7.2f} | "
            f"{headline['total_pnl']:>6.2f} | "
            f"{headline['hedge_events']:>5} | "
            f"{headline['quoteable_ratio']:>9.2%} | "
            f"{headline['hold_p90_secs']:>8.1f}s | "
            f"{variant['key']}"
        )
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run a PAPER parameter sweep and rank variants with the calibration scorer.")
    parser.add_argument("--suite-root", help="Output directory for the sweep. Defaults to tmp/calibration_sweeps/<timestamp>.")
    parser.add_argument("--preset", choices=["overnight_profiles", "proof045_max_active"], default="overnight_profiles")
    parser.add_argument("--duration-secs", type=int, default=120)
    parser.add_argument("--exchange", default="kalshi")
    parser.add_argument("--symbol", default="BTC")
    parser.add_argument("--safe-risk-profile", default="500")
    parser.add_argument("--strategy-allocated-equity", type=float, default=500.0)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--max-runs", type=int, default=None)
    parser.add_argument("--max-active-markets-values")
    parser.add_argument("--hedge-threshold-fraction-values")
    parser.add_argument("--stale-duration-scale-values")
    parser.add_argument("--maker-exit-grace-secs-values")
    parser.add_argument("--force-flat-before-expiry-secs-values")
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    suite_root = Path(args.suite_root).expanduser() if args.suite_root else (
        REPO_ROOT / "tmp" / "calibration_sweeps" / time.strftime("%Y%m%d-%H%M%S")
    )
    suite_root.mkdir(parents=True, exist_ok=True)
    variants = build_variants(args)
    if args.max_runs is not None:
        variants = variants[: max(0, int(args.max_runs))]
    results = []
    for idx, variant in enumerate(variants, start=1):
        results.append(
            _run_variant(
                suite_root=suite_root,
                index=idx,
                variant=variant,
                duration_secs=int(args.duration_secs),
                exchange=args.exchange,
                symbol=args.symbol,
                safe_risk_profile=args.safe_risk_profile,
                allocated_equity=float(args.strategy_allocated_equity),
            )
        )
    results.sort(key=lambda row: row["score"], reverse=True)
    payload = {
        "suite_root": suite_root.as_posix(),
        "duration_secs": int(args.duration_secs),
        "variant_count": len(results),
        "weights": asdict(CalibrationWeights()),
        "results": results,
    }
    (suite_root / "summary.json").write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(_format_results(results))
        print(f"\nsummary: {suite_root / 'summary.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
