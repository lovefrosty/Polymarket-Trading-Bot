from __future__ import annotations

import argparse
import json
import math
from dataclasses import asdict, dataclass
from pathlib import Path
import sys
from typing import Any, Dict, Iterable, List, Optional


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.run_cluster_calibration_suite import (  # noqa: E402
    _action_effectiveness,
    _cluster_summary,
    _hold_summary,
    _stranded_positions,
)


@dataclass(frozen=True)
class CalibrationWeights:
    pnl: float = 1.0
    hedge_event: float = 12.0
    hedge_success_ratio: float = 20.0
    quoteable_ratio: float = 15.0
    accepted_candidate: float = 4.0
    quality_gap_positive: float = 2.0
    force_flat_count: float = 0.02
    hold_p90_secs: float = 0.25
    hold_max_secs: float = 0.05
    stranded_token: float = 3.0
    churn_excess: float = 1.5
    no_hedge_market: float = 0.002
    poor_hedge_quality: float = 0.01


def _read_json(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _safe_ratio(numerator: float, denominator: float) -> float:
    if denominator <= 0.0:
        return 0.0
    return float(numerator) / float(denominator)


def _hedge_improvement_ratio(action_effectiveness: Dict[str, Any]) -> float:
    hedge = action_effectiveness.get("HEDGE") if isinstance(action_effectiveness, dict) else None
    if not isinstance(hedge, dict):
        return 0.0
    observed = float(hedge.get("observed") or 0.0)
    improved = float(hedge.get("improved") or 0.0)
    return _safe_ratio(improved, observed)


def _churn_excess(summary: Dict[str, Any]) -> float:
    fills = float(summary.get("fills") or 0.0)
    placed = float(summary.get("placed_orders") or 0.0)
    if fills <= 0.0:
        return placed
    churn_ratio = placed / fills
    return max(0.0, churn_ratio - 1.0)


def load_runtime_metrics(runtime_root: Path) -> Dict[str, Any]:
    runtime_root = Path(runtime_root)
    summary = _read_json(runtime_root / "meta" / "run_summary.json")
    db_path = runtime_root / "runtime.db"
    hold_summary = _hold_summary(db_path)
    cluster_summary = _cluster_summary(db_path)
    action_effectiveness = _action_effectiveness(db_path)
    stranded_positions = _stranded_positions(db_path)
    hedge_candidate_summary = dict(summary.get("hedge_candidate_summary") or {})
    hedge_summary = dict(summary.get("hedge_summary") or {})
    rejection_reasons = dict(hedge_summary.get("rejection_reasons") or {})
    risk_proof = dict(summary.get("risk_proof") or {})
    return {
        "runtime_root": runtime_root.as_posix(),
        "summary": summary,
        "hold_summary": hold_summary,
        "cluster_summary": cluster_summary,
        "action_effectiveness": action_effectiveness,
        "stranded_positions": stranded_positions,
        "hedge_candidate_summary": hedge_candidate_summary,
        "hedge_summary": hedge_summary,
        "risk_proof": risk_proof,
        "rejection_reasons": rejection_reasons,
    }


def score_runtime_metrics(
    metrics: Dict[str, Any],
    *,
    weights: Optional[CalibrationWeights] = None,
) -> Dict[str, Any]:
    weights = weights or CalibrationWeights()
    summary = dict(metrics.get("summary") or {})
    hold_summary = dict(metrics.get("hold_summary") or {})
    action_effectiveness = dict(metrics.get("action_effectiveness") or {})
    stranded_positions = dict(metrics.get("stranded_positions") or {})
    hedge_candidate_summary = dict(metrics.get("hedge_candidate_summary") or {})
    hedge_summary = dict(metrics.get("hedge_summary") or {})
    rejection_reasons = dict(metrics.get("rejection_reasons") or {})

    total_pnl = float(summary.get("total_pnl") or 0.0)
    quoteable_ratio = float((summary.get("cycle_summary") or {}).get("quoteable_ratio") or 0.0)
    hedge_events = float((hedge_summary.get("cluster_actions") or {}).get("HEDGE") or 0.0)
    hedge_success_ratio = _hedge_improvement_ratio(action_effectiveness)
    accepted_candidates = float(hedge_candidate_summary.get("accepted_clusters") or 0.0)
    quality_gap_positive = float(hedge_candidate_summary.get("quality_gap_positive") or 0.0)
    force_flat_count = float((summary.get("risk_proof") or {}).get("decision_risk_actions", {}).get("FORCE_FLAT") or 0.0)
    hold_p90_secs = float(hold_summary.get("p90_hold_secs") or 0.0)
    hold_max_secs = float(hold_summary.get("max_hold_secs") or 0.0)
    stranded_token_count = float(stranded_positions.get("open_token_count") or 0.0)
    churn_excess = _churn_excess(summary)
    no_hedge_market = float(rejection_reasons.get("no_hedge_market") or 0.0)
    poor_hedge_quality = float(rejection_reasons.get("poor_hedge_quality") or 0.0)

    components = {
        "pnl": total_pnl * weights.pnl,
        "hedge_event": hedge_events * weights.hedge_event,
        "hedge_success_ratio": hedge_success_ratio * weights.hedge_success_ratio,
        "quoteable_ratio": quoteable_ratio * weights.quoteable_ratio,
        "accepted_candidate": accepted_candidates * weights.accepted_candidate,
        "quality_gap_positive": quality_gap_positive * weights.quality_gap_positive,
        "force_flat_count": -force_flat_count * weights.force_flat_count,
        "hold_p90_secs": -hold_p90_secs * weights.hold_p90_secs,
        "hold_max_secs": -hold_max_secs * weights.hold_max_secs,
        "stranded_token": -stranded_token_count * weights.stranded_token,
        "churn_excess": -churn_excess * weights.churn_excess,
        "no_hedge_market": -no_hedge_market * weights.no_hedge_market,
        "poor_hedge_quality": -poor_hedge_quality * weights.poor_hedge_quality,
    }
    total_score = round(sum(components.values()), 6)
    return {
        "runtime_root": metrics.get("runtime_root"),
        "score": total_score,
        "components": {key: round(value, 6) for key, value in components.items()},
        "headline": {
            "total_pnl": total_pnl,
            "hedge_events": int(hedge_events),
            "hedge_success_ratio": round(hedge_success_ratio, 4),
            "quoteable_ratio": round(quoteable_ratio, 4),
            "hold_p90_secs": hold_p90_secs,
            "hold_max_secs": hold_max_secs,
            "force_flat_count": int(force_flat_count),
            "stranded_token_count": int(stranded_token_count),
            "no_hedge_market": int(no_hedge_market),
        },
    }


def score_runtime_root(runtime_root: Path, *, weights: Optional[CalibrationWeights] = None) -> Dict[str, Any]:
    return score_runtime_metrics(load_runtime_metrics(runtime_root), weights=weights)


def _resolve_runtime_roots(roots: Iterable[str], patterns: Iterable[str]) -> List[Path]:
    resolved: List[Path] = []
    seen: set[str] = set()
    for root in roots:
        path = Path(root).expanduser().resolve()
        if path.as_posix() not in seen:
            resolved.append(path)
            seen.add(path.as_posix())
    for pattern in patterns:
        for path in sorted(REPO_ROOT.glob(pattern)):
            resolved_path = path.resolve()
            if resolved_path.as_posix() not in seen:
                resolved.append(resolved_path)
                seen.add(resolved_path.as_posix())
    return resolved


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Rank paper runtime roots using a weighted calibration score.")
    parser.add_argument("runtime_roots", nargs="*", help="Runtime root directories to score.")
    parser.add_argument("--glob", dest="globs", action="append", default=[], help="Glob pattern under repo root for runtime roots.")
    parser.add_argument("--json", action="store_true", help="Emit JSON instead of a human-readable table.")
    return parser


def _format_rows(rows: List[Dict[str, Any]]) -> str:
    if not rows:
        return "No runtime roots matched."
    lines = []
    lines.append("rank | score | pnl | hedge | hedge_success | quoteable | hold_p90 | force_flat | root")
    for idx, row in enumerate(rows, start=1):
        headline = row["headline"]
        lines.append(
            f"{idx:>4} | "
            f"{row['score']:>7.2f} | "
            f"{headline['total_pnl']:>6.2f} | "
            f"{headline['hedge_events']:>5} | "
            f"{headline['hedge_success_ratio']:>13.2%} | "
            f"{headline['quoteable_ratio']:>9.2%} | "
            f"{headline['hold_p90_secs']:>8.1f}s | "
            f"{headline['force_flat_count']:>10} | "
            f"{row['runtime_root']}"
        )
    return "\n".join(lines)


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    runtime_roots = _resolve_runtime_roots(args.runtime_roots, args.globs)
    scored = [score_runtime_root(path) for path in runtime_roots]
    scored.sort(key=lambda row: row["score"], reverse=True)
    if args.json:
        print(json.dumps({"weights": asdict(CalibrationWeights()), "results": scored}, indent=2, sort_keys=True))
    else:
        print(_format_rows(scored))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
