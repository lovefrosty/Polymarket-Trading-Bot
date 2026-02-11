from __future__ import annotations

import argparse
import bisect
from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from core.market_time import window_start_end_ms


@dataclass
class Trade:
    ts_ms: int
    symbol: str
    asset_id: str
    outcome: Optional[str]
    side: str
    price: float
    size: float
    fee_bps: float
    slippage_bps: float
    latency_ms: Optional[float]
    label: Optional[int] = None


def main() -> None:
    args = _parse_args()
    log_dir = Path(args.logs)
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    decision_paths = _resolve_paths(args.decision, log_dir, "decision_*.jsonl")
    reference_paths = _resolve_paths(args.reference, log_dir, "reference_*.jsonl")
    report = generate_report(
        decision_paths=decision_paths,
        reference_paths=reference_paths,
        train_days=args.train_days,
        test_days=args.test_days,
        step_days=args.step_days,
        fee_bps_shift=args.fee_bps_shift,
        slippage_bps_shift=args.slippage_bps_shift,
        latency_threshold_ms=args.latency_threshold_ms,
        latency_shift_ms=args.latency_shift_ms,
    )
    write_report(report, out_dir)


def generate_report(
    decision_paths: List[Path],
    reference_paths: List[Path],
    train_days: int,
    test_days: int,
    step_days: int,
    fee_bps_shift: float,
    slippage_bps_shift: float,
    latency_threshold_ms: int,
    latency_shift_ms: int,
) -> Dict[str, Any]:
    if not decision_paths:
        raise SystemExit("no_decision_files_found")
    if not reference_paths:
        raise SystemExit("no_reference_files_found")

    decisions = list(_iter_decisions(decision_paths))
    if not decisions:
        raise SystemExit("no_decisions_loaded")

    reference_series = _load_reference_series(reference_paths)
    trades = _extract_trades(decisions, reference_series)
    if not trades:
        raise SystemExit("no_trades_extracted")

    folds = _walkforward_folds(
        trades=trades,
        train_days=train_days,
        test_days=test_days,
        step_days=step_days,
        fee_bps_shift=fee_bps_shift,
        slippage_bps_shift=slippage_bps_shift,
        latency_threshold_ms=latency_threshold_ms,
        latency_shift_ms=latency_shift_ms,
    )

    overall = _compute_metrics(
        trades,
        fee_bps_shift=fee_bps_shift,
        slippage_bps_shift=slippage_bps_shift,
        latency_threshold_ms=latency_threshold_ms,
        latency_shift_ms=latency_shift_ms,
    )

    return {
        "schema_version": "walkforward_report_v1",
        "created_at": _utc_iso(),
        "inputs": {
            "decision_files": [str(p) for p in decision_paths],
            "reference_files": [str(p) for p in reference_paths],
        },
        "settings": {
            "train_days": train_days,
            "test_days": test_days,
            "step_days": step_days,
            "fee_bps_shift": fee_bps_shift,
            "slippage_bps_shift": slippage_bps_shift,
            "latency_threshold_ms": latency_threshold_ms,
            "latency_shift_ms": latency_shift_ms,
        },
        "overall": overall,
        "folds": folds,
        "stability": _stability_stats(folds),
    }


def write_report(report: Dict[str, Any], out_dir: Path) -> None:
    json_path = out_dir / "walkforward_report.json"
    json_path.write_text(json.dumps(report, separators=(",", ":"), ensure_ascii=True, sort_keys=True))
    md_path = out_dir / "walkforward_report.md"
    md_path.write_text(_render_markdown(report))


def _resolve_paths(inputs: Optional[list[str]], log_dir: Path, pattern: str) -> List[Path]:
    if inputs:
        paths = [Path(entry) for entry in inputs]
        resolved: List[Path] = []
        for path in paths:
            if path.is_dir():
                resolved.extend(sorted(path.glob(pattern)))
            else:
                resolved.append(path)
        return resolved
    return sorted(log_dir.glob(pattern))


def _iter_decisions(paths: Iterable[Path]) -> Iterable[Dict[str, Any]]:
    for path in paths:
        for line in path.read_text().splitlines():
            if not line:
                continue
            yield json.loads(line)


def _load_reference_series(paths: Iterable[Path]) -> Dict[str, List[Tuple[int, float]]]:
    series: Dict[str, List[Tuple[int, float]]] = {}
    for path in paths:
        for line in path.read_text().splitlines():
            if not line:
                continue
            record = json.loads(line)
            if record.get("channel") != "reference":
                continue
            raw = record.get("raw") or {}
            symbol = raw.get("symbol") or record.get("market")
            if symbol is None:
                continue
            value = raw.get("value")
            if value is None:
                continue
            try:
                price = float(value)
            except (TypeError, ValueError):
                continue
            t_event_ms = record.get("t_event_ms") or raw.get("t_event_ms")
            if t_event_ms is None:
                continue
            try:
                ts = int(t_event_ms)
            except (TypeError, ValueError):
                continue
            series.setdefault(str(symbol), []).append((ts, price))
    for symbol, points in series.items():
        points.sort(key=lambda item: item[0])
    return series


def _extract_trades(
    decisions: List[Dict[str, Any]],
    reference_series: Dict[str, List[Tuple[int, float]]],
) -> List[Trade]:
    trades: List[Trade] = []
    for record in decisions:
        entry_gate = (record.get("notes") or {}).get("entry_gate") or {}
        if not entry_gate.get("allow", False):
            continue
        chosen_action = (record.get("notes") or {}).get("chosen_action") or {}
        side = chosen_action.get("side")
        if side not in {"buy", "sell"}:
            continue
        slug = record.get("market_slug")
        if not slug:
            continue
        symbol = _reference_symbol(record, slug)
        if symbol is None or symbol not in reference_series:
            continue
        window = window_start_end_ms(str(slug))
        if window is None:
            continue
        start_ms, end_ms = window
        label = _label_from_reference(reference_series[symbol], start_ms, end_ms)
        if label is None:
            continue
        exec_price = chosen_action.get("p_exec")
        if exec_price is None:
            exec_price = record.get("p_market_exec_buy") if side == "buy" else record.get("p_market_exec_sell")
        if exec_price is None:
            continue
        try:
            exec_price = float(exec_price)
        except (TypeError, ValueError):
            continue
        size = (record.get("exec_cost") or {}).get("q") or 1.0
        try:
            size = float(size)
        except (TypeError, ValueError):
            size = 1.0
        fee_bps = (record.get("exec_cost") or {}).get("fee_bps_used") or 0.0
        slippage_bps = (record.get("exec_cost") or {}).get("slippage_bps") or 0.0
        try:
            fee_bps = float(fee_bps)
        except (TypeError, ValueError):
            fee_bps = 0.0
        try:
            slippage_bps = float(slippage_bps)
        except (TypeError, ValueError):
            slippage_bps = 0.0
        latency_ms = (record.get("notes") or {}).get("signals", {}).get("ref_latency_ms")
        try:
            latency_ms = float(latency_ms) if latency_ms is not None else None
        except (TypeError, ValueError):
            latency_ms = None
        trade = Trade(
            ts_ms=int(record.get("t_decision_wall_ms", 0)),
            symbol=symbol,
            asset_id=str(record.get("asset_id") or ""),
            outcome=record.get("outcome"),
            side=side,
            price=float(exec_price),
            size=size,
            fee_bps=fee_bps,
            slippage_bps=slippage_bps,
            latency_ms=latency_ms,
            label=label,
        )
        trades.append(trade)
    trades.sort(key=lambda item: item.ts_ms)
    return trades


def _reference_symbol(record: Dict[str, Any], slug: str) -> Optional[str]:
    resolved = (record.get("notes") or {}).get("resolved_market") or {}
    symbol = resolved.get("reference_symbol")
    if symbol:
        return str(symbol)
    parts = str(slug).split("-")
    if parts:
        return parts[0].upper()
    return None


def _label_from_reference(
    points: List[Tuple[int, float]], start_ms: int, end_ms: int
) -> Optional[int]:
    price_t0 = _price_at_or_after(points, start_ms)
    price_t1 = _price_at_or_after(points, end_ms)
    if price_t0 is None or price_t1 is None:
        return None
    return 1 if price_t1[0] >= price_t0[0] else 0


def _price_at_or_after(points: List[Tuple[int, float]], ts_ms: int) -> Optional[Tuple[float, int]]:
    idx = bisect.bisect_left(points, (ts_ms, -float("inf")))
    if idx >= len(points):
        return None
    ts, price = points[idx]
    if ts < ts_ms:
        return None
    return price, ts


def _walkforward_folds(
    trades: List[Trade],
    train_days: int,
    test_days: int,
    step_days: int,
    fee_bps_shift: float,
    slippage_bps_shift: float,
    latency_threshold_ms: int,
    latency_shift_ms: int,
) -> List[Dict[str, Any]]:
    if not trades:
        return []
    start_ms = trades[0].ts_ms
    end_ms = trades[-1].ts_ms
    train_ms = train_days * 24 * 60 * 60 * 1000
    test_ms = test_days * 24 * 60 * 60 * 1000
    step_ms = step_days * 24 * 60 * 60 * 1000

    folds: List[Dict[str, Any]] = []
    cursor = start_ms
    while cursor + train_ms + test_ms <= end_ms:
        train_start = cursor
        train_end = cursor + train_ms
        test_start = train_end
        test_end = train_end + test_ms

        test_trades = [trade for trade in trades if test_start <= trade.ts_ms < test_end]
        fold_metrics = _compute_metrics(
            test_trades,
            fee_bps_shift=fee_bps_shift,
            slippage_bps_shift=slippage_bps_shift,
            latency_threshold_ms=latency_threshold_ms,
            latency_shift_ms=latency_shift_ms,
        )
        folds.append(
            {
                "train_start_ms": train_start,
                "train_end_ms": train_end,
                "test_start_ms": test_start,
                "test_end_ms": test_end,
                "train_start_iso": _iso_from_ms(train_start),
                "train_end_iso": _iso_from_ms(train_end),
                "test_start_iso": _iso_from_ms(test_start),
                "test_end_iso": _iso_from_ms(test_end),
                "metrics": fold_metrics,
            }
        )
        cursor += step_ms
    return folds


def _compute_metrics(
    trades: List[Trade],
    fee_bps_shift: float,
    slippage_bps_shift: float,
    latency_threshold_ms: int,
    latency_shift_ms: int,
) -> Dict[str, Any]:
    if not trades:
        return {"count": 0}
    per_symbol: Dict[str, List[Trade]] = {}
    per_outcome: Dict[str, List[Trade]] = {}
    pnl_series: List[Tuple[int, float]] = []
    turnover = 0.0
    wins = 0
    losses = 0
    gross_profit = 0.0
    gross_loss = 0.0

    for trade in trades:
        if trade.latency_ms is not None:
            if trade.latency_ms + latency_shift_ms > latency_threshold_ms:
                continue
        pnl = _trade_pnl(trade, fee_bps_shift, slippage_bps_shift)
        pnl_series.append((trade.ts_ms, pnl))
        turnover += trade.price * trade.size
        if pnl >= 0:
            wins += 1
            gross_profit += pnl
        else:
            losses += 1
            gross_loss += abs(pnl)

        per_symbol.setdefault(trade.symbol, []).append(trade)
        outcome_key = str(trade.outcome or "unknown")
        per_outcome.setdefault(outcome_key, []).append(trade)

    pnl_series.sort(key=lambda item: item[0])
    pnl_values = [value for _, value in pnl_series]
    cumulative, drawdown = _drawdown(pnl_series)
    win_rate = wins / max(1, wins + losses)
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else None

    metrics = {
        "count": wins + losses,
        "pnl_total": sum(pnl_values),
        "pnl_mean": sum(pnl_values) / max(1, len(pnl_values)),
        "pnl_std": _stddev(pnl_values),
        "win_rate": win_rate,
        "turnover": turnover,
        "max_drawdown": drawdown,
        "profit_factor": profit_factor,
        "cumulative_pnl_end": cumulative[-1] if cumulative else 0.0,
        "by_symbol": {symbol: _summary_for_trades(rows, fee_bps_shift, slippage_bps_shift, latency_threshold_ms, latency_shift_ms) for symbol, rows in per_symbol.items()},
        "by_outcome": {outcome: _summary_for_trades(rows, fee_bps_shift, slippage_bps_shift, latency_threshold_ms, latency_shift_ms) for outcome, rows in per_outcome.items()},
    }
    return metrics


def _summary_for_trades(
    trades: List[Trade],
    fee_bps_shift: float,
    slippage_bps_shift: float,
    latency_threshold_ms: int,
    latency_shift_ms: int,
) -> Dict[str, Any]:
    if not trades:
        return {"count": 0}
    pnl_series: List[Tuple[int, float]] = []
    turnover = 0.0
    wins = 0
    losses = 0
    gross_profit = 0.0
    gross_loss = 0.0
    for trade in trades:
        if trade.latency_ms is not None:
            if trade.latency_ms + latency_shift_ms > latency_threshold_ms:
                continue
        pnl = _trade_pnl(trade, fee_bps_shift, slippage_bps_shift)
        pnl_series.append((trade.ts_ms, pnl))
        turnover += trade.price * trade.size
        if pnl >= 0:
            wins += 1
            gross_profit += pnl
        else:
            losses += 1
            gross_loss += abs(pnl)
    pnl_series.sort(key=lambda item: item[0])
    pnl_values = [value for _, value in pnl_series]
    _, drawdown = _drawdown(pnl_series)
    win_rate = wins / max(1, wins + losses)
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else None
    return {
        "count": wins + losses,
        "pnl_total": sum(pnl_values),
        "pnl_mean": sum(pnl_values) / max(1, len(pnl_values)),
        "pnl_std": _stddev(pnl_values),
        "win_rate": win_rate,
        "turnover": turnover,
        "max_drawdown": drawdown,
        "profit_factor": profit_factor,
    }


def _trade_pnl(trade: Trade, fee_bps_shift: float, slippage_bps_shift: float) -> float:
    label = trade.label or 0
    fee_bps = trade.fee_bps + fee_bps_shift
    slippage_bps = trade.slippage_bps + slippage_bps_shift
    cost_bps = max(0.0, fee_bps + slippage_bps)
    cost_per_share = trade.price * (cost_bps / 10000.0)
    if trade.side == "buy":
        pnl_per_share = label - trade.price - cost_per_share
    else:
        pnl_per_share = trade.price - label - cost_per_share
    return pnl_per_share * trade.size


def _drawdown(pnl_series: List[Tuple[int, float]]) -> Tuple[List[float], float]:
    cumulative: List[float] = []
    peak = 0.0
    max_dd = 0.0
    total = 0.0
    for _, pnl in pnl_series:
        total += pnl
        cumulative.append(total)
        if total > peak:
            peak = total
        dd = peak - total
        if dd > max_dd:
            max_dd = dd
    return cumulative, max_dd


def _stability_stats(folds: List[Dict[str, Any]]) -> Dict[str, Any]:
    if not folds:
        return {}
    pnl = [fold["metrics"].get("pnl_total", 0.0) for fold in folds if fold.get("metrics")]
    win_rates = [fold["metrics"].get("win_rate", 0.0) for fold in folds if fold.get("metrics")]
    return {
        "fold_count": len(folds),
        "pnl_mean": _mean(pnl),
        "pnl_std": _stddev(pnl),
        "win_rate_mean": _mean(win_rates),
        "win_rate_std": _stddev(win_rates),
    }


def _mean(values: List[float]) -> float:
    if not values:
        return 0.0
    return sum(values) / len(values)


def _stddev(values: List[float]) -> Optional[float]:
    if len(values) < 2:
        return None
    mean = sum(values) / len(values)
    var = sum((value - mean) ** 2 for value in values) / (len(values) - 1)
    return var ** 0.5


def _iso_from_ms(ts_ms: int) -> str:
    return datetime.fromtimestamp(ts_ms / 1000.0, tz=timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%S.%f"
    )[:-3] + "Z"


def _utc_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def _render_markdown(report: Dict[str, Any]) -> str:
    overall = report.get("overall", {})
    lines = [
        "# Walk-forward Report",
        "",
        f"Generated: {report.get('created_at')}",
        "",
        "## Overall",
        "",
        f"- Trades: {overall.get('count')}",
        f"- PnL: {overall.get('pnl_total')}",
        f"- Max Drawdown: {overall.get('max_drawdown')}",
        f"- Turnover: {overall.get('turnover')}",
        f"- Win Rate: {overall.get('win_rate')}",
        "",
        "## Folds",
        "",
    ]
    for fold in report.get("folds", []):
        metrics = fold.get("metrics", {})
        lines.extend(
            [
                f"- {fold.get('test_start_iso')} → {fold.get('test_end_iso')}",
                f"  Trades: {metrics.get('count')}",
                f"  PnL: {metrics.get('pnl_total')}",
                f"  Max DD: {metrics.get('max_drawdown')}",
                f"  Win Rate: {metrics.get('win_rate')}",
            ]
        )
    stability = report.get("stability", {})
    lines.extend(
        [
            "",
            "## Stability",
            "",
            f"- Fold count: {stability.get('fold_count')}",
            f"- PnL mean: {stability.get('pnl_mean')}",
            f"- PnL std: {stability.get('pnl_std')}",
            f"- Win rate mean: {stability.get('win_rate_mean')}",
            f"- Win rate std: {stability.get('win_rate_std')}",
        ]
    )
    return "\n".join(lines) + "\n"


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Walk-forward report on DecisionTape + ReferenceTape")
    parser.add_argument("--logs", default="./logs", help="Logs directory")
    parser.add_argument("--decision", nargs="*", default=None, help="Decision tape files or directories")
    parser.add_argument("--reference", nargs="*", default=None, help="Reference tape files or directories")
    parser.add_argument("--out", default="./logs", help="Output directory")
    parser.add_argument("--train-days", type=int, default=7)
    parser.add_argument("--test-days", type=int, default=1)
    parser.add_argument("--step-days", type=int, default=1)
    parser.add_argument("--fee-bps-shift", type=float, default=0.0)
    parser.add_argument("--slippage-bps-shift", type=float, default=0.0)
    parser.add_argument("--latency-threshold-ms", type=int, default=2000)
    parser.add_argument("--latency-shift-ms", type=int, default=0)
    return parser.parse_args()


if __name__ == "__main__":
    main()
