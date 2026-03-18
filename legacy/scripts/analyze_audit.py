from __future__ import annotations

import argparse
import bisect
from collections import Counter, defaultdict, deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
import math
from pathlib import Path
from typing import Any, Deque, Dict, Iterable, List, Optional, Tuple

from core.entry_exit_rules import EntryExitParams
from core.market_time import window_start_end_ms
from core.toxicity import toxicity_from_exec_prices
from core.volatility import ewma_variance_update, percentile_rank


HORIZONS_SEC = (10, 60)
SIGMA10S_FLOOR = 1e-5
CALIBRATION_BINS = 10
LOGLOSS_EPS = 1e-6
EDGE_HIST_MIN = -0.05
EDGE_HIST_MAX = 0.05
EDGE_HIST_BINS = 20
PATHOLOGY_ORDER = [
    "Reference lag",
    "False momentum",
    "Book lies",
    "Late chaos",
    "Feed failure",
    "Odds drift",
    "Cross-asset break",
    "Thin liquidity",
]


@dataclass
class SeriesStats:
    values: List[float] = field(default_factory=list)
    count: int = 0
    total: float = 0.0
    min_value: Optional[float] = None
    max_value: Optional[float] = None

    def add(self, value: float) -> None:
        self.values.append(value)
        self.count += 1
        self.total += value
        if self.min_value is None or value < self.min_value:
            self.min_value = value
        if self.max_value is None or value > self.max_value:
            self.max_value = value

    def summary(self) -> Dict[str, Optional[float]]:
        if self.count == 0:
            return {"count": 0}
        values = sorted(self.values)
        return {
            "count": self.count,
            "mean": self.total / self.count,
            "min": self.min_value,
            "max": self.max_value,
            "p10": _percentile(values, 0.10),
            "p50": _percentile(values, 0.50),
            "p90": _percentile(values, 0.90),
        }


@dataclass
class PendingEntry:
    decision: Dict[str, Any]
    metrics: Dict[str, Any]
    target_ns: int


@dataclass
class PriceState:
    history: Deque[Tuple[int, float]] = field(default_factory=deque)
    last_10s_price: Optional[float] = None
    var_10s: float = 0.0


@dataclass
class CalibrationBin:
    count: int = 0
    sum_p: float = 0.0
    sum_y: float = 0.0


@dataclass
class CalibrationStats:
    bins: List[CalibrationBin] = field(default_factory=list)
    count: int = 0
    brier_sum: float = 0.0
    logloss_sum: float = 0.0


@dataclass
class EdgeHistogram:
    min_edge: float
    max_edge: float
    bins: int
    counts: List[int] = field(default_factory=list)
    underflow: int = 0
    overflow: int = 0

    def __post_init__(self) -> None:
        if not self.counts:
            self.counts = [0 for _ in range(self.bins)]

    def add(self, value: float) -> None:
        if value < self.min_edge:
            self.underflow += 1
            return
        if value >= self.max_edge:
            self.overflow += 1
            return
        span = self.max_edge - self.min_edge
        idx = int((value - self.min_edge) / span * self.bins)
        if idx >= self.bins:
            idx = self.bins - 1
        if idx < 0:
            idx = 0
        self.counts[idx] += 1

    def summary(self) -> Dict[str, Any]:
        bin_width = (self.max_edge - self.min_edge) / self.bins
        bins = []
        total = sum(self.counts)
        cumulative = 0
        quantiles = {0.05: None, 0.25: None, 0.5: None, 0.75: None, 0.95: None}
        targets = {q: q * total for q in quantiles}
        for idx, count in enumerate(self.counts):
            start = self.min_edge + idx * bin_width
            end = start + bin_width
            cumulative += count
            for q, target in list(targets.items()):
                if quantiles[q] is None and cumulative >= target:
                    quantiles[q] = (start + end) / 2.0
            bins.append({"range": [start, end], "count": count})
        return {
            "bins": bins,
            "underflow": self.underflow,
            "overflow": self.overflow,
            "quantiles": {f"p{int(q*100)}": v for q, v in quantiles.items()},
        }


def analyze_decision_files(
    decision_files: Iterable[str],
    reference_files: Optional[Iterable[str]] = None,
) -> Dict[str, Any]:
    gate_failures: Counter = Counter()
    z_stats = SeriesStats()
    edge_stats = SeriesStats()
    vol_stats = SeriesStats()
    tox_stats: Dict[str, Dict[str, SeriesStats]] = {
        f"{horizon}s": {"accepted": SeriesStats(), "rejected": SeriesStats()} for horizon in HORIZONS_SEC
    }
    tox_unavailable: Dict[str, int] = {f"{horizon}s": 0 for horizon in HORIZONS_SEC}
    confusion = Counter()
    pathology_counts: Dict[str, Dict[str, Any]] = defaultdict(lambda: {"count": 0, "survives": 0, "loss": Counter()})
    hedge_counts: Counter = Counter()
    hedge_blockers: Counter = Counter()
    hedge_by_regime: Dict[str, Counter] = {"low": Counter(), "mid": Counter(), "high": Counter()}
    calib_overall = _init_calibration()
    calib_by_outcome: Dict[str, CalibrationStats] = defaultdict(_init_calibration)
    calib_by_model: Dict[str, CalibrationStats] = defaultdict(_init_calibration)
    edge_hist_buy = EdgeHistogram(EDGE_HIST_MIN, EDGE_HIST_MAX, EDGE_HIST_BINS)
    edge_hist_sell = EdgeHistogram(EDGE_HIST_MIN, EDGE_HIST_MAX, EDGE_HIST_BINS)
    edge_hist_by_model: Dict[str, Dict[str, EdgeHistogram]] = {}

    entry_params = EntryExitParams(
        edge_min=0.015,
        edge_exit=0.00375,
        edge_stop=0.0075,
        z_mom_min=1.0,
        t_min_secs=90.0,
        hold_max_secs=480.0,
        vol_pct_hi=95.0,
        edge_min_mult_hivol=1.5,
    )

    pending_tox: Dict[str, Dict[int, Deque[PendingEntry]]] = defaultdict(
        lambda: {horizon: deque() for horizon in HORIZONS_SEC}
    )
    sigma_hist: Dict[str, List[float]] = defaultdict(list)
    price_state: Dict[str, PriceState] = defaultdict(PriceState)
    reference_series = _load_reference_series(reference_files or [])

    run_id = None
    first_decision_iso = None
    for record in _iter_decisions(decision_files):
        run_id = run_id or record.get("run_id")
        if first_decision_iso is None:
            first_decision_iso = record.get("t_decision_wall_iso")
        asset_id = str(record.get("asset_id", ""))
        t_mono_ns = int(record.get("t_decision_mono_ns", 0))
        decision_reasons = record.get("gates", {}).get("reasons", []) or []
        entry_gate = _extract_entry_gate(record, entry_params)
        entry_allow = bool(entry_gate.get("allow", False))
        entry_reasons = entry_gate.get("reasons", []) or []

        for reason in decision_reasons:
            gate_failures[str(reason)] += 1

        signals = _extract_signals(record)
        z_mom = signals.get("z_mom")
        if z_mom is not None:
            z_stats.add(float(z_mom))

        edge_net = signals.get("edge_net")
        if edge_net is not None:
            edge_stats.add(float(edge_net))

        edge_buy = record.get("edge_net_buy")
        if edge_buy is not None:
            edge_hist_buy.add(float(edge_buy))
        edge_sell = record.get("edge_net_sell")
        if edge_sell is not None:
            edge_hist_sell.add(float(edge_sell))
        model_key = str((record.get("notes") or {}).get("model_used") or "unknown")
        if model_key not in edge_hist_by_model:
            edge_hist_by_model[model_key] = {
                "edge_net_buy": EdgeHistogram(EDGE_HIST_MIN, EDGE_HIST_MAX, EDGE_HIST_BINS),
                "edge_net_sell": EdgeHistogram(EDGE_HIST_MIN, EDGE_HIST_MAX, EDGE_HIST_BINS),
            }
        if edge_buy is not None:
            edge_hist_by_model[model_key]["edge_net_buy"].add(float(edge_buy))
        if edge_sell is not None:
            edge_hist_by_model[model_key]["edge_net_sell"].add(float(edge_sell))

        sigma_t = signals.get("sigma_t")
        vol_regime = None
        if sigma_t is not None:
            hist = sigma_hist[asset_id]
            vol_regime = percentile_rank(float(sigma_t), hist)
            hist.append(float(sigma_t))
            vol_stats.add(vol_regime)

        sigma_10s = _update_sigma_10s(price_state[asset_id], record, t_mono_ns, SIGMA10S_FLOOR)

        metrics = {
            "allow": entry_allow,
            "reasons": entry_reasons,
            "edge_net": edge_net,
            "z_mom": z_mom,
            "vol_regime": vol_regime,
            "sigma_10s": sigma_10s,
            "time_remaining_sec": signals.get("time_remaining_sec"),
        }

        label = _label_from_reference(record, reference_series)
        if label is not None:
            _update_calibration(calib_overall, record, label)
            outcome_key = str(record.get("outcome") or "unknown")
            _update_calibration(calib_by_outcome[outcome_key], record, label)
            model_used = (record.get("notes") or {}).get("model_used") or "unknown"
            _update_calibration(calib_by_model[str(model_used)], record, label)

        _process_pending_toxicity(
            pending_tox[asset_id],
            record,
            t_mono_ns,
            tox_stats,
            tox_unavailable,
            metrics,
            hedge_counts,
            hedge_blockers,
            hedge_by_regime,
        )

        if entry_allow:
            for horizon in HORIZONS_SEC:
                pending_tox[asset_id][horizon].append(
                    PendingEntry(
                        decision=record,
                        metrics=metrics,
                        target_ns=t_mono_ns + horizon * 1_000_000_000,
                    )
                )

        _update_confusion(confusion, entry_gate, z_mom)
        _update_exit_confusion(confusion, record)
        _update_pathology(pathology_counts, record, metrics, entry_params)

    _finalize_pending_toxicity(pending_tox, tox_unavailable)
    calibration_by_model = {
        model: _calibration_summary(stats) for model, stats in calib_by_model.items()
    }
    report = {
        "run_id": run_id,
        "generated_at": first_decision_iso or "unknown",
        "gate_failures": dict(gate_failures),
        "top_failure_reasons": _top_reasons(gate_failures, limit=10),
        "signal_stats": {
            "z_mom": z_stats.summary(),
            "edge_net": edge_stats.summary(),
            "vol_regime": vol_stats.summary(),
        },
        "calibration": {
            "overall": _calibration_summary(calib_overall),
            "by_outcome": {outcome: _calibration_summary(stats) for outcome, stats in calib_by_outcome.items()},
            "by_model": calibration_by_model,
            "comparison": _calibration_comparison(calibration_by_model),
        },
        "toxicity": {
            "horizons": {
                horizon: {
                    "accepted": tox_stats[horizon]["accepted"].summary(),
                    "rejected": tox_stats[horizon]["rejected"].summary(),
                    "unavailable": tox_unavailable[horizon],
                }
                for horizon in tox_stats
            }
        },
        "confusion": dict(confusion),
        "pathology_table": _pathology_table(pathology_counts),
        "edge_histograms": {
            "edge_net_buy": edge_hist_buy.summary(),
            "edge_net_sell": edge_hist_sell.summary(),
        },
        "edge_histograms_by_model": {
            model: {key: hist.summary() for key, hist in hists.items()}
            for model, hists in edge_hist_by_model.items()
        },
        "hedge_policy": {
            "h_counts": dict(hedge_counts),
            "blockers": dict(hedge_blockers),
            "by_vol_regime": {bucket: dict(counter) for bucket, counter in hedge_by_regime.items()},
        },
        "lifecycle": {
            "status": "read_only_audit",
            "implemented": [
                "event_tape",
                "decision_tape",
                "executable_pricing",
                "reference_validation",
                "deterministic_replay",
            ],
            "next_steps": [
                "wire p_fair model outputs into edge_net",
                "compute delta_binary from position model",
                "add venue hedge execution models",
            ],
        },
    }
    return report


def main() -> None:
    args = _parse_args()
    decision_files = _resolve_decision_files(args.input)
    if not decision_files:
        raise SystemExit("no_decision_files_found")
    reference_files = _resolve_reference_files(args.event_tape or args.input)
    report = analyze_decision_files(decision_files, reference_files=reference_files)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "audit_report.json"
    md_path = output_dir / "audit_report.md"
    json_path.write_text(json.dumps(report, indent=2, sort_keys=True))
    md_path.write_text(_render_markdown(report))


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Analyze DecisionTape audit metrics")
    parser.add_argument("input", help="Path to decision JSONL file or directory")
    parser.add_argument("--output-dir", default="./logs", help="Output directory for audit report")
    parser.add_argument("--event-tape", default=None, help="Reference event tape file or directory")
    return parser.parse_args()


def _resolve_decision_files(path: str) -> List[str]:
    root = Path(path)
    if root.is_dir():
        return sorted(str(p) for p in root.glob("decision_*.jsonl"))
    if root.is_file():
        return [str(root)]
    return []


def _resolve_reference_files(path: str) -> List[str]:
    root = Path(path)
    if root.is_dir():
        return sorted(str(p) for p in root.glob("reference_*.jsonl"))
    if root.is_file() and root.name.startswith("reference_"):
        return [str(root)]
    return []


def _iter_decisions(paths: Iterable[str]) -> Iterable[Dict[str, Any]]:
    for path in paths:
        for line in Path(path).read_text().splitlines():
            if not line:
                continue
            yield json.loads(line)


def _extract_signals(record: Dict[str, Any]) -> Dict[str, Any]:
    notes = record.get("notes") or {}
    signals = notes.get("signals") or {}
    return signals


def _extract_entry_gate(record: Dict[str, Any], params: EntryExitParams) -> Dict[str, Any]:
    notes = record.get("notes") or {}
    entry_gate = notes.get("entry_gate")
    if isinstance(entry_gate, dict):
        return entry_gate
    signals = _extract_signals(record)
    reasons: List[str] = []
    edge_net = signals.get("edge_net")
    if edge_net is None:
        reasons.append("EDGE_MISSING")
    elif abs(float(edge_net)) < params.edge_min:
        reasons.append("EDGE_TOO_SMALL")
    z_mom = signals.get("z_mom")
    if z_mom is None:
        reasons.append("Z_MOM_MISSING")
    elif abs(float(z_mom)) < params.z_mom_min:
        reasons.append("Z_TOO_WEAK")
    allow = not reasons
    return {"allow": allow, "reasons": reasons, "edge_min_required": params.edge_min}


def _init_calibration() -> CalibrationStats:
    bins = [CalibrationBin() for _ in range(CALIBRATION_BINS)]
    return CalibrationStats(bins=bins)


def _update_calibration(stats: CalibrationStats, record: Dict[str, Any], label: int) -> None:
    p_fair = record.get("p_fair")
    if p_fair is None:
        return
    try:
        p_val = float(p_fair)
    except (TypeError, ValueError):
        return
    p_val = min(max(p_val, 0.0), 1.0)
    idx = min(int(p_val * CALIBRATION_BINS), CALIBRATION_BINS - 1)
    stats.bins[idx].count += 1
    stats.bins[idx].sum_p += p_val
    stats.bins[idx].sum_y += float(label)

    stats.count += 1
    stats.brier_sum += (p_val - label) ** 2
    p_safe = min(max(p_val, LOGLOSS_EPS), 1.0 - LOGLOSS_EPS)
    stats.logloss_sum += -(
        label * math.log(p_safe) + (1 - label) * math.log(1.0 - p_safe)
    )


def _calibration_summary(stats: CalibrationStats) -> Dict[str, Any]:
    if stats.count == 0:
        return {"count": 0, "bins": []}
    bins = []
    for idx, bin_stat in enumerate(stats.bins):
        if bin_stat.count == 0:
            bins.append({"bin": idx, "count": 0})
            continue
        avg_p = bin_stat.sum_p / bin_stat.count
        avg_y = bin_stat.sum_y / bin_stat.count
        bins.append(
            {
                "bin": idx,
                "count": bin_stat.count,
                "avg_p": avg_p,
                "empirical": avg_y,
                "calibration_error": abs(avg_p - avg_y),
            }
        )
    return {
        "count": stats.count,
        "brier": stats.brier_sum / stats.count,
        "logloss": stats.logloss_sum / stats.count,
        "bins": bins,
    }


def _calibration_comparison(by_model: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    trained = by_model.get("trained")
    baseline = by_model.get("baseline")
    if not trained or not baseline:
        return {}
    if not trained.get("count") or not baseline.get("count"):
        return {}
    return {
        "trained": {
            "brier": trained.get("brier"),
            "logloss": trained.get("logloss"),
            "count": trained.get("count"),
        },
        "baseline": {
            "brier": baseline.get("brier"),
            "logloss": baseline.get("logloss"),
            "count": baseline.get("count"),
        },
        "delta": {
            "brier": _delta(trained.get("brier"), baseline.get("brier")),
            "logloss": _delta(trained.get("logloss"), baseline.get("logloss")),
        },
    }


def _delta(a: Optional[float], b: Optional[float]) -> Optional[float]:
    if a is None or b is None:
        return None
    return float(a) - float(b)


def _load_reference_series(reference_files: Iterable[str]) -> Dict[str, List[Tuple[int, float]]]:
    series: Dict[str, List[Tuple[int, float]]] = defaultdict(list)
    for path in reference_files:
        for line in Path(path).read_text().splitlines():
            if not line:
                continue
            record = json.loads(line)
            if record.get("channel") != "reference":
                continue
            raw = record.get("raw") or {}
            symbol = raw.get("symbol")
            value = raw.get("value")
            if symbol is None or value is None:
                continue
            try:
                price = float(value)
            except (TypeError, ValueError):
                continue
            t_event_ms = record.get("t_event_ms")
            if t_event_ms is None:
                t_event_ms = _parse_wall_ms(record.get("t_recv_wall_iso"))
            try:
                t_event_ms = int(t_event_ms)
            except (TypeError, ValueError):
                continue
            series[str(symbol)].append((t_event_ms, price))
    for symbol, points in series.items():
        points.sort(key=lambda item: item[0])
    return series


def _reference_at(points: List[Tuple[int, float]], ts_ms: int) -> Optional[float]:
    if not points:
        return None
    idx = bisect.bisect_right(points, (ts_ms, float("inf"))) - 1
    if idx < 0:
        return None
    return points[idx][1]


def _label_from_reference(record: Dict[str, Any], series: Dict[str, List[Tuple[int, float]]]) -> Optional[int]:
    slug = record.get("market_slug")
    if not slug:
        return None
    window = window_start_end_ms(str(slug))
    if window is None:
        return None
    start_ms, end_ms = window
    resolved = record.get("notes", {}).get("resolved_market") or {}
    symbol = resolved.get("reference_symbol")
    if symbol is None:
        return None
    points = series.get(str(symbol))
    if not points:
        return None
    p_start = _reference_at(points, start_ms)
    p_end = _reference_at(points, end_ms)
    if p_start is None or p_end is None:
        return None
    y_up = 1 if p_end >= p_start else 0
    outcome = record.get("outcome")
    if outcome is None:
        return None
    if "up" in str(outcome).lower():
        return y_up
    if "down" in str(outcome).lower():
        return 1 - y_up
    return None


def _update_sigma_10s(state: PriceState, record: Dict[str, Any], t_mono_ns: int, sigma_floor: float) -> Optional[float]:
    p_star = record.get("p_star") or {}
    value = p_star.get("value")
    if value is None:
        return None
    try:
        price = float(value)
    except (TypeError, ValueError):
        return None
    state.history.append((t_mono_ns, price))
    horizon_ns = 10 * 1_000_000_000
    while state.history and t_mono_ns - state.history[0][0] >= horizon_ns:
        state.last_10s_price = state.history.popleft()[1]
    if state.last_10s_price is None or state.last_10s_price <= 0 or price <= 0:
        return None
    r_10s = math_log_return(state.last_10s_price, price)
    state.var_10s = ewma_variance_update(state.var_10s, r_10s, 10.0, 120.0)
    sigma_10s = max(state.var_10s, 0.0) ** 0.5
    return max(sigma_10s, sigma_floor)


def _process_pending_toxicity(
    pending: Dict[int, Deque[PendingEntry]],
    record: Dict[str, Any],
    t_mono_ns: int,
    tox_stats: Dict[str, Dict[str, SeriesStats]],
    tox_unavailable: Dict[str, int],
    metrics: Dict[str, Any],
    hedge_counts: Counter,
    hedge_blockers: Counter,
    hedge_by_regime: Dict[str, Counter],
) -> None:
    exec_buy = record.get("p_market_exec_buy")
    for horizon in HORIZONS_SEC:
        queue = pending[horizon]
        while queue and queue[0].target_ns <= t_mono_ns:
            entry = queue.popleft()
            entry_decision = entry.decision
            entry_metrics = entry.metrics
            entry_allow = bool(entry_metrics.get("allow", False))
            tox = toxicity_from_exec_prices("buy", entry_decision.get("p_market_exec_buy"), exec_buy)
            bucket = "accepted" if entry_allow else "rejected"
            if tox.tox_bps is None:
                tox_unavailable[f"{horizon}s"] += 1
            else:
                tox_stats[f"{horizon}s"][bucket].add(float(tox.tox_bps))
            if horizon == 10 and entry_allow:
                _update_hedge_policy_from_notes(
                    entry_decision,
                    entry_metrics,
                    hedge_counts,
                    hedge_blockers,
                    hedge_by_regime,
                )


def _update_hedge_policy_from_notes(
    decision: Dict[str, Any],
    metrics: Dict[str, Any],
    hedge_counts: Counter,
    hedge_blockers: Counter,
    hedge_by_regime: Dict[str, Counter],
) -> None:
    hedge = decision.get("notes", {}).get("hedge_policy")
    if not isinstance(hedge, dict):
        return
    blockers = hedge.get("blockers") or []
    if blockers:
        for blocker in blockers:
            hedge_blockers[str(blocker)] += 1
        return
    ratio = hedge.get("hedge_ratio_target")
    if ratio is None:
        return
    hedge_counts[str(ratio)] += 1
    regime_bucket = _regime_bucket(metrics.get("vol_regime"))
    hedge_by_regime[regime_bucket][str(ratio)] += 1


def _update_confusion(confusion: Counter, entry_gate: Dict[str, Any], z_mom: Optional[float]) -> None:
    reasons = entry_gate.get("reasons", []) or []
    z_pass = z_mom is not None and abs(float(z_mom)) >= 1.0
    edge_fail = "EDGE_TOO_SMALL" in reasons
    if edge_fail and z_pass:
        confusion["rejected_by_EDGE_accepted_by_Z"] += 1


def _update_exit_confusion(confusion: Counter, record: Dict[str, Any]) -> None:
    exit_rec = record.get("notes", {}).get("exit_recommendation")
    if not isinstance(exit_rec, dict):
        return
    if not exit_rec.get("should_exit"):
        return
    reason = exit_rec.get("reason")
    if reason == "EDGE_COLLAPSE":
        confusion["accepted_then_exited_by_EDGE_COLLAPSE"] += 1
    elif reason == "EDGE_STOP":
        confusion["accepted_then_exited_by_EDGE_STOP"] += 1


def _finalize_pending_toxicity(
    pending: Dict[str, Dict[int, Deque[PendingEntry]]],
    tox_unavailable: Dict[str, int],
) -> None:
    for asset in pending.values():
        for horizon, queue in asset.items():
            tox_unavailable[f"{horizon}s"] += len(queue)
            queue.clear()


def _update_pathology(
    pathology_counts: Dict[str, Dict[str, Any]],
    record: Dict[str, Any],
    metrics: Dict[str, Any],
    params: EntryExitParams,
) -> None:
    bucket, loss_mode = _classify_pathology(record, metrics, params)
    bucket_entry = pathology_counts[bucket]
    bucket_entry["count"] += 1
    if metrics.get("allow"):
        bucket_entry["survives"] += 1
    if loss_mode:
        bucket_entry["loss"][loss_mode] += 1


def _classify_pathology(
    record: Dict[str, Any],
    metrics: Dict[str, Any],
    params: EntryExitParams,
) -> Tuple[str, Optional[str]]:
    decision_reasons = set(str(r) for r in record.get("gates", {}).get("reasons", []) or [])
    entry_reasons = set(str(r) for r in _extract_entry_gate(record, params).get("reasons", []) or [])
    signals = _extract_signals(record)
    z_mom = signals.get("z_mom")
    edge_net = signals.get("edge_net")
    time_remaining = signals.get("time_remaining_sec")

    if "CROSS_ASSET_BREAK" in decision_reasons or "CROSS_ASSET_BREAK" in entry_reasons:
        return "Cross-asset break", "CROSS_ASSET_BREAK"
    if "SIGNAL_AGE" in decision_reasons or "WS_LAG" in decision_reasons:
        return "Reference lag", "SIGNAL_AGE"
    if any(reason.startswith("REF_") for reason in decision_reasons) or "REF_FROZEN" in entry_reasons:
        return "Feed failure", "REF_FROZEN"
    if "DEPTH_TOO_THIN" in decision_reasons or "SLIPPAGE_TOO_HIGH" in decision_reasons:
        return "Thin liquidity", "LIQUIDITY"
    if "SPREAD_TOO_WIDE" in decision_reasons or "BOOK_STALE" in decision_reasons:
        return "Book lies", "BOOK"
    if time_remaining is not None and float(time_remaining) < params.t_min_secs:
        return "Late chaos", "TIME_TOO_SHORT"
    if z_mom is not None and abs(float(z_mom)) >= params.z_mom_min:
        if edge_net is None or abs(float(edge_net)) < params.edge_min:
            return "Book lies", "EDGE_TOO_SMALL"
    if edge_net is not None and (z_mom is None or abs(float(z_mom)) < params.z_mom_min):
        return "Odds drift", "Z_TOO_WEAK"
    exit_rec = record.get("notes", {}).get("exit_recommendation")
    exit_reason = exit_rec.get("reason") if isinstance(exit_rec, dict) else None
    if exit_reason == "EDGE_COLLAPSE":
        return "False momentum", "EDGE_COLLAPSE"
    return "Reference lag", None


def _pathology_table(pathology_counts: Dict[str, Dict[str, Any]]) -> List[Dict[str, Any]]:
    table = []
    for bucket in PATHOLOGY_ORDER:
        data = pathology_counts.get(bucket, {"count": 0, "survives": 0, "loss": Counter()})
        loss_mode = None
        if data["loss"]:
            loss_mode = data["loss"].most_common(1)[0][0]
        survives = data.get("survives", 0)
        count = data.get("count", 0)
        survive_ratio = survives / count if count else 0.0
        table.append(
            {
                "pathology": bucket,
                "survives": survive_ratio,
                "loss_mode": loss_mode,
                "count": count,
            }
        )
    return table


def _render_markdown(report: Dict[str, Any]) -> str:
    lines = []
    lines.append("# Audit Report")
    lines.append("")
    lines.append(f"Run ID: {report.get('run_id')}")
    lines.append("")
    lines.append("## Gate Failures")
    for reason, count in sorted(report.get("gate_failures", {}).items(), key=lambda x: -x[1]):
        lines.append(f"- {reason}: {count}")
    lines.append("")
    lines.append("## Top Failure Reasons")
    for entry in report.get("top_failure_reasons", []):
        lines.append(f"- {entry.get('reason')}: {entry.get('count')}")
    lines.append("")
    lines.append("## Confusion Matrices")
    for key, count in sorted(report.get("confusion", {}).items()):
        lines.append(f"- {key}: {count}")
    lines.append("")
    lines.append("## Calibration")
    calibration = report.get("calibration", {})
    overall = calibration.get("overall", {})
    if overall:
        lines.append(f"- brier: {overall.get('brier')}")
        lines.append(f"- logloss: {overall.get('logloss')}")
        bins = overall.get("bins") or []
        if bins:
            lines.append("  bins: bin count avg_p empirical")
            for entry in bins:
                lines.append(
                    f"  - {entry.get('bin')}: n={entry.get('count')} avg_p={entry.get('avg_p')} emp={entry.get('empirical')}"
                )
    for outcome, stats in (calibration.get("by_outcome") or {}).items():
        lines.append(f"- {outcome}: brier={stats.get('brier')} logloss={stats.get('logloss')}")
    for model, stats in (calibration.get("by_model") or {}).items():
        lines.append(f"- model {model}: brier={stats.get('brier')} logloss={stats.get('logloss')}")
    comparison = calibration.get("comparison") or {}
    if comparison:
        lines.append(
            f"- comparison delta: brier={comparison.get('delta', {}).get('brier')} logloss={comparison.get('delta', {}).get('logloss')}"
        )
    lines.append("")
    lines.append("## Edge Histograms")
    hist = report.get("edge_histograms", {})
    for name, data in hist.items():
        lines.append(f"- {name}: underflow={data.get('underflow')} overflow={data.get('overflow')}")
        quantiles = data.get("quantiles") or {}
        if quantiles:
            lines.append(f"  quantiles: {quantiles}")
        lines.extend(_render_edge_histogram(data))
    hist_by_model = report.get("edge_histograms_by_model", {})
    for model, data in hist_by_model.items():
        lines.append(f"- model {model}")
        for name, hist_data in (data or {}).items():
            lines.append(f"  {name}: underflow={hist_data.get('underflow')} overflow={hist_data.get('overflow')}")
            quantiles = hist_data.get("quantiles") or {}
            if quantiles:
                lines.append(f"  quantiles: {quantiles}")
            lines.extend(_render_edge_histogram(hist_data))
    lines.append("")
    lines.append("## Pathology Table")
    lines.append("Pathology | Survives? | Loss Mode")
    lines.append("---|---|---")
    for row in report.get("pathology_table", []):
        survives = f"{row['survives']:.2f}"
        lines.append(f"{row['pathology']} | {survives} | {row.get('loss_mode')}")
    lines.append("")
    lines.append("## Hedge Policy")
    for h, count in sorted(report.get("hedge_policy", {}).get("h_counts", {}).items()):
        lines.append(f"- h={h}: {count}")
    lines.append("")
    lines.append("## Project Lifecycle")
    lifecycle = report.get("lifecycle", {})
    lines.append(f"Status: {lifecycle.get('status')}")
    lines.append("")
    lines.append("Implemented:")
    for item in lifecycle.get("implemented", []):
        lines.append(f"- {item}")
    lines.append("")
    lines.append("Next steps:")
    for item in lifecycle.get("next_steps", []):
        lines.append(f"- {item}")
    lines.append("")
    return "\n".join(lines)


def _percentile(values: List[float], p: float) -> Optional[float]:
    if not values:
        return None
    if p <= 0:
        return values[0]
    if p >= 1:
        return values[-1]
    idx = int(round(p * (len(values) - 1)))
    return values[idx]


def _regime_bucket(vol_regime: Optional[float]) -> str:
    if vol_regime is None:
        return "unknown"
    if vol_regime >= 0.7:
        return "high"
    if vol_regime >= 0.3:
        return "mid"
    return "low"


def _render_edge_histogram(data: Dict[str, Any]) -> List[str]:
    bins = data.get("bins") or []
    if not bins:
        return []
    counts = [int(b.get("count", 0)) for b in bins]
    max_count = max(counts) if counts else 0
    lines = []
    if max_count == 0:
        return lines
    scale = 20 / max_count if max_count > 0 else 1.0
    for entry, count in zip(bins, counts):
        span = entry.get("range") or []
        if len(span) != 2:
            continue
        bar = "#" * int(count * scale)
        lines.append(f"  {span[0]:.4f}..{span[1]:.4f} | {bar}")
    return lines


def math_log_return(prev: float, current: float) -> float:
    return math.log(current / prev)


def _iso_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def _top_reasons(counter: Counter, limit: int) -> List[Dict[str, Any]]:
    return [{"reason": reason, "count": count} for reason, count in counter.most_common(limit)]


def _parse_wall_ms(wall_iso: Optional[str]) -> int:
    if not wall_iso:
        return 0
    try:
        if wall_iso.endswith("Z"):
            wall_iso = wall_iso[:-1] + "+00:00"
        dt = datetime.fromisoformat(wall_iso)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return int(dt.timestamp() * 1000)
    except ValueError:
        return 0


if __name__ == "__main__":
    main()
