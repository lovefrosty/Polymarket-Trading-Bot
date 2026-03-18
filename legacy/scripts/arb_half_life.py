from __future__ import annotations

import argparse
import bisect
import json
import math
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple


LAGS_SEC = [0, 1, 2, 5, 10, 20, 30, 60]


def analyze_arb_half_life(
    decision_files: Iterable[str],
    reference_files: Iterable[str],
    shock_horizon_sec: int,
    shock_quantile_q: float,
    shock_min_count: int,
) -> Dict[str, Any]:
    ref_series = _load_reference_series(reference_files)
    decisions = _load_decisions(decision_files)
    lag_stats: Dict[int, Dict[str, Any]] = {}
    half_life_samples: List[float] = []
    shock_by_symbol: Dict[str, Dict[str, Any]] = {}

    for symbol, series in decisions.items():
        ref_points = ref_series.get(symbol, [])
        if not ref_points:
            continue
        abs_returns = _abs_returns(ref_points, shock_horizon_sec)
        threshold, used_q, ref_shock_count = _select_shock_threshold(
            abs_returns, shock_quantile_q, shock_min_count
        )
        shock_by_symbol[symbol] = {
            "threshold": threshold,
            "quantile_q": used_q,
            "ref_samples": len(abs_returns),
            "ref_shock_count": ref_shock_count,
            "decision_shock_count": 0,
            "abs_return_stats": _half_life_summary(abs_returns),
        }
        for lag in LAGS_SEC:
            pairs = []
            for t_ms, logit_p in series:
                future_t = t_ms + lag * 1000
                future_idx = _index_at_or_after(series, future_t)
                if future_idx is None:
                    continue
                logit_future = series[future_idx][1]
                spot_now = _ref_at_or_before(ref_points, t_ms)
                spot_future = _ref_at_or_before(ref_points, future_t)
                if spot_now is None or spot_future is None:
                    continue
                spot_return = math.log(spot_future / spot_now)
                d_logit = logit_future - logit_p
                pairs.append((d_logit, spot_return))
            lag_stats[lag] = _corr_stats(pairs)

        for t_ms, logit_p in series:
            if threshold is None:
                break
            shock_end = t_ms + shock_horizon_sec * 1000
            spot_now = _ref_at_or_before(ref_points, t_ms)
            spot_future = _ref_at_or_before(ref_points, shock_end)
            if spot_now is None or spot_future is None:
                continue
            shock_return = math.log(spot_future / spot_now)
            if abs(shock_return) < threshold:
                continue
            shock_by_symbol[symbol]["decision_shock_count"] += 1
            target = 0.5 * shock_return
            hit = _half_life(series, t_ms, logit_p, target)
            if hit is not None:
                half_life_samples.append(hit)

    best_lag = _best_lag(lag_stats)
    return {
        "schema_version": "arb_half_life_v1",
        "best_lag_sec": best_lag,
        "lag_stats": lag_stats,
        "half_life_ms": _half_life_summary(half_life_samples),
        "shock": {
            "horizon_sec": shock_horizon_sec,
            "quantile_q": shock_quantile_q,
            "min_count": shock_min_count,
            "by_symbol": shock_by_symbol,
        },
        "warnings": [],
    }


def main() -> None:
    args = _parse_args()
    decision_files = _resolve_files(args.decision)
    reference_files = _resolve_files(args.reference)
    if not decision_files or not reference_files:
        raise SystemExit("missing_decision_or_reference_files")
    report = analyze_arb_half_life(
        decision_files,
        reference_files,
        shock_horizon_sec=args.shock_horizon_sec,
        shock_quantile_q=args.shock_quantile_q,
        shock_min_count=args.shock_min_count,
    )
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "arb_half_life.json"
    md_path = output_dir / "arb_half_life.md"
    json_path.write_text(json.dumps(report, indent=2, sort_keys=True))
    md_path.write_text(_render_markdown(report))


def _load_reference_series(paths: Iterable[str]) -> Dict[str, List[Tuple[int, float]]]:
    series: Dict[str, List[Tuple[int, float]]] = {}
    for path in paths:
        for line in Path(path).read_text().splitlines():
            if not line:
                continue
            record = json.loads(line)
            if record.get("channel") != "reference":
                continue
            raw = record.get("raw") or {}
            symbol = raw.get("symbol") or record.get("market")
            if symbol is None:
                continue
            mid = raw.get("mid")
            if mid is None:
                mid = raw.get("value")
            if mid is None:
                continue
            try:
                mid_val = float(mid)
            except (TypeError, ValueError):
                continue
            t_event_ms = record.get("t_event_ms")
            if t_event_ms is None:
                continue
            try:
                t_event_ms = int(t_event_ms)
            except (TypeError, ValueError):
                continue
            series.setdefault(str(symbol), []).append((t_event_ms, mid_val))
    for points in series.values():
        points.sort(key=lambda item: item[0])
    return series


def _load_decisions(paths: Iterable[str]) -> Dict[str, List[Tuple[int, float]]]:
    series: Dict[str, List[Tuple[int, float]]] = {}
    for path in paths:
        for line in Path(path).read_text().splitlines():
            if not line:
                continue
            record = json.loads(line)
            t_ms = record.get("t_decision_wall_ms")
            if t_ms is None:
                continue
            try:
                t_ms = int(t_ms)
            except (TypeError, ValueError):
                continue
            p_market = record.get("p_market_exec_buy")
            if p_market is None:
                continue
            try:
                p_market = float(p_market)
            except (TypeError, ValueError):
                continue
            if p_market <= 0 or p_market >= 1:
                continue
            symbol = (record.get("notes") or {}).get("resolved_market", {}).get("reference_symbol")
            if symbol is None:
                continue
            logit_p = math.log(p_market / (1.0 - p_market))
            series.setdefault(str(symbol), []).append((t_ms, logit_p))
    for points in series.values():
        points.sort(key=lambda item: item[0])
    return series


def _ref_at_or_before(points: List[Tuple[int, float]], ts_ms: int) -> Optional[float]:
    idx = bisect.bisect_right(points, (ts_ms, float("inf"))) - 1
    if idx < 0:
        return None
    return points[idx][1]


def _index_at_or_after(points: List[Tuple[int, float]], ts_ms: int) -> Optional[int]:
    idx = bisect.bisect_left(points, (ts_ms, -float("inf")))
    if idx >= len(points):
        return None
    return idx


def _corr_stats(pairs: List[Tuple[float, float]]) -> Dict[str, Any]:
    if not pairs:
        return {"corr": None, "samples": 0}
    xs = [p[0] for p in pairs]
    ys = [p[1] for p in pairs]
    mean_x = sum(xs) / len(xs)
    mean_y = sum(ys) / len(ys)
    cov = sum((x - mean_x) * (y - mean_y) for x, y in pairs)
    var_x = sum((x - mean_x) ** 2 for x in xs)
    var_y = sum((y - mean_y) ** 2 for y in ys)
    if var_x <= 0 or var_y <= 0:
        return {"corr": None, "samples": len(pairs)}
    corr = cov / math.sqrt(var_x * var_y)
    return {"corr": corr, "samples": len(pairs)}


def _half_life(series: List[Tuple[int, float]], t_ms: int, logit_now: float, target: float) -> Optional[float]:
    for future_t, logit_future in series:
        if future_t <= t_ms:
            continue
        delta = logit_future - logit_now
        if target > 0 and delta >= target:
            return float(future_t - t_ms)
        if target < 0 and delta <= target:
            return float(future_t - t_ms)
    return None


def _half_life_summary(samples: List[float]) -> Dict[str, Any]:
    if not samples:
        return {"count": 0}
    samples = sorted(samples)
    return {
        "count": len(samples),
        "p50": _percentile(samples, 0.5),
        "p90": _percentile(samples, 0.9),
    }


def _percentile(values: List[float], p: float) -> Optional[float]:
    if not values:
        return None
    idx = int(round((len(values) - 1) * p))
    return values[idx]


def _best_lag(stats: Dict[int, Dict[str, Any]]) -> Optional[int]:
    best = None
    best_corr = 0.0
    for lag, entry in stats.items():
        corr = entry.get("corr")
        if corr is None:
            continue
        if best is None or abs(corr) > abs(best_corr):
            best = lag
            best_corr = corr
    return best


def _render_markdown(report: Dict[str, Any]) -> str:
    lines = []
    lines.append("# Arb Half-Life Report")
    lines.append("")
    lines.append(f"Best lag (sec): {report.get('best_lag_sec')}")
    lines.append("")
    lines.append("## Lag Correlations")
    for lag, stats in sorted(report.get("lag_stats", {}).items()):
        lines.append(f"- {lag}s: corr={stats.get('corr')} samples={stats.get('samples')}")
    lines.append("")
    hl = report.get("half_life_ms", {})
    lines.append("## Half-Life")
    lines.append(f"- count: {hl.get('count')}")
    lines.append(f"- p50(ms): {hl.get('p50')}")
    lines.append(f"- p90(ms): {hl.get('p90')}")
    lines.append("")
    lines.append("## Shock Definition")
    shock = report.get("shock") or {}
    lines.append(f"- horizon_sec: {shock.get('horizon_sec')}")
    lines.append(f"- quantile_q: {shock.get('quantile_q')}")
    lines.append(f"- min_count: {shock.get('min_count')}")
    for symbol, meta in sorted((shock.get("by_symbol") or {}).items()):
        lines.append(
            f"- {symbol}: threshold={meta.get('threshold')} q={meta.get('quantile_q')} "
            f"ref_samples={meta.get('ref_samples')} ref_shocks={meta.get('ref_shock_count')} "
            f"decision_shocks={meta.get('decision_shock_count')}"
        )
    lines.append("")
    lines.append("Interpretation: shorter half-life and low lag suggest stronger mechanical arbitrage coupling.")
    return "\n".join(lines)


def _abs_returns(points: List[Tuple[int, float]], horizon_sec: int) -> List[float]:
    if horizon_sec <= 0:
        return []
    abs_returns: List[float] = []
    for t_ms, price in points:
        future = _ref_at_or_before(points, t_ms + horizon_sec * 1000)
        if future is None or price <= 0:
            continue
        abs_returns.append(abs(math.log(future / price)))
    return abs_returns


def _select_shock_threshold(
    abs_returns: List[float], quantile_q: float, min_count: int
) -> Tuple[Optional[float], float, int]:
    if not abs_returns:
        return None, quantile_q, 0
    candidates: List[float] = []
    for q in [quantile_q, 0.02, 0.05, 0.10]:
        if q not in candidates:
            candidates.append(q)
    values = sorted(abs_returns)
    for q in candidates:
        idx = int(round((len(values) - 1) * (1.0 - q)))
        idx = max(0, min(idx, len(values) - 1))
        threshold = values[idx]
        count = sum(1 for value in abs_returns if value >= threshold)
        if count >= min_count:
            return threshold, q, count
    threshold = values[max(0, min(len(values) - 1, int(round((len(values) - 1) * (1.0 - candidates[-1])))))]
    count = sum(1 for value in abs_returns if value >= threshold)
    return threshold, candidates[-1], count


def _resolve_files(path: str) -> List[str]:
    root = Path(path)
    if root.is_dir():
        return sorted(str(p) for p in root.glob("*.jsonl"))
    if root.is_file():
        return [str(root)]
    return []


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Arb half-life analysis")
    parser.add_argument("--decision", required=True, help="DecisionTape JSONL or directory")
    parser.add_argument("--reference", required=True, help="Reference EventTape JSONL or directory")
    parser.add_argument("--shock-horizon-sec", type=int, default=10, help="Shock return horizon in seconds")
    parser.add_argument("--shock-quantile-q", type=float, default=0.01, help="Shock quantile (top-q fraction)")
    parser.add_argument("--shock-min-count", type=int, default=5, help="Minimum shock sample count")
    parser.add_argument("--output-dir", default="./logs", help="Output directory")
    return parser.parse_args()


if __name__ == "__main__":
    main()
