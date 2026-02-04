from __future__ import annotations

import json
import math
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from core.market_time import parse_end_epoch_from_slug, window_start_end_ms


@dataclass(frozen=True)
class Spec:
    raw: Dict[str, Any]

    @property
    def hypothesis_id(self) -> str:
        return str(self.raw.get("hypothesis_id"))

    @property
    def name(self) -> str:
        return str(self.raw.get("name"))


def load_spec(path: Path) -> Spec:
    return Spec(json.loads(path.read_text()))


def run_experiment(spec_path: Path, output_dir_override: Optional[Path] = None) -> Dict[str, Any]:
    spec = load_spec(spec_path)
    _validate_label(spec.raw.get("label"))

    inputs = spec.raw.get("inputs") or {}
    decision_paths = _expand_paths(inputs.get("decision_paths") or [])
    market_paths = _expand_paths(inputs.get("market_paths") or [])
    reference_paths = _expand_paths(inputs.get("reference_paths") or [])

    selection = spec.raw.get("market_selection") or {}
    sampling = spec.raw.get("sampling") or {}
    start_ms = sampling.get("start_ms")
    end_ms = sampling.get("end_ms")
    cadence_ms = int(sampling.get("cadence_ms") or 1000)

    market_records = _load_jsonl(market_paths)
    decision_records = _load_jsonl(decision_paths)
    reference_records = _load_jsonl(reference_paths)

    if spec.hypothesis_id == "H001":
        results = _analyze_h001(spec.raw, market_records, selection, start_ms, end_ms, cadence_ms)
    elif spec.hypothesis_id == "H002":
        results = _analyze_h002(
            spec.raw,
            market_records,
            decision_records,
            selection,
            start_ms,
            end_ms,
            cadence_ms,
        )
    elif spec.hypothesis_id == "H003":
        results = _analyze_h003(
            spec.raw,
            decision_records,
            reference_records,
            selection,
            start_ms,
            end_ms,
        )
    else:
        raise ValueError(f"unknown_hypothesis:{spec.hypothesis_id}")

    results["schema_version"] = "scientific_method_result_v1"
    results["hypothesis_id"] = spec.hypothesis_id
    results["name"] = spec.name
    results["inputs"] = {
        "decision_paths": decision_paths,
        "market_paths": market_paths,
        "reference_paths": reference_paths,
    }
    results["acceptance"] = spec.raw.get("acceptance") or {}

    output_dir = output_dir_override or spec_path.parent / "outputs"
    output_dir.mkdir(parents=True, exist_ok=True)
    results_path = output_dir / "results.json"
    report_path = output_dir / "report.md"
    results_path.write_text(json.dumps(results, indent=2, sort_keys=True))
    report_path.write_text(_render_report(results))
    return results


def _validate_label(label: Optional[Dict[str, Any]]) -> None:
    if not label:
        raise ValueError("missing_label_definition")
    label_type = label.get("type")
    if label_type not in {"micro", "window"}:
        raise ValueError("label_type_must_be_micro_or_window")
    if label_type == "micro" and not label.get("horizons_sec"):
        raise ValueError("micro_label_requires_horizons_sec")
    if label_type == "window" and not label.get("window_secs"):
        raise ValueError("window_label_requires_window_secs")


def _analyze_h001(
    spec: Dict[str, Any],
    market_records: List[Dict[str, Any]],
    selection: Dict[str, Any],
    start_ms: Optional[int],
    end_ms: Optional[int],
    cadence_ms: int,
) -> Dict[str, Any]:
    features = spec.get("features") or {}
    horizons = [int(h) for h in spec.get("label", {}).get("horizons_sec", [])]
    depth_levels = int((features.get("imbalance_depth") or {}).get("levels") or 1)

    series = _build_market_series(market_records, selection, start_ms, end_ms, depth_levels)
    rows = _build_micro_rows(series, horizons)
    if not rows:
        raise ValueError("no_rows_for_h001")

    metrics = {
        "correlation": _correlation_metrics(rows, horizons, ["imbalance_l1", "imbalance_depth"]),
        "ridge_regression": _ridge_metrics(rows, horizons),
        "autocorr_half_life": _autocorr_half_life(series, cadence_ms),
        "row_count": len(rows),
    }

    min_corr = (spec.get("acceptance") or {}).get("min_corr")
    passed = _pass_corr(metrics["correlation"], min_corr)
    return {"pass": passed, "metrics": metrics}


def _analyze_h002(
    spec: Dict[str, Any],
    market_records: List[Dict[str, Any]],
    decision_records: List[Dict[str, Any]],
    selection: Dict[str, Any],
    start_ms: Optional[int],
    end_ms: Optional[int],
    cadence_ms: int,
) -> Dict[str, Any]:
    features = spec.get("features") or {}
    depth_levels = int((features.get("imbalance_depth") or {}).get("levels") or 1)
    series = _build_market_series(market_records, selection, start_ms, end_ms, depth_levels)
    buckets = spec.get("tte_buckets_sec") or [180, 60, 30]
    bucket_edges = sorted([int(x) for x in buckets], reverse=True)

    rows = []
    for record in decision_records:
        if not _match_selection_decision(record, selection):
            continue
        t_decision = _to_int(record.get("t_decision_wall_ms"))
        if t_decision is None:
            continue
        if start_ms is not None and t_decision < start_ms:
            continue
        if end_ms is not None and t_decision > end_ms:
            continue
        slug = record.get("market_slug")
        end_sec = parse_end_epoch_from_slug(str(slug)) if slug else None
        if end_sec is None:
            continue
        tte_sec = (end_sec * 1000 - t_decision) / 1000.0
        if tte_sec < 0:
            continue
        asset_id = record.get("asset_id")
        if not asset_id or asset_id not in series:
            continue
        imbalance = _asof_value(series[asset_id], t_decision, "imbalance_l1")
        if imbalance is None:
            continue
        z_mom = ((record.get("notes") or {}).get("signals") or {}).get("z_mom")
        rows.append(
            {
                "t_decision": t_decision,
                "tte_sec": tte_sec,
                "imbalance_l1": imbalance,
                "z_mom": _to_float(z_mom),
            }
        )

    if not rows:
        raise ValueError("no_rows_for_h002")

    bucket_effects = _bucket_effects(rows, bucket_edges)
    min_gap = (spec.get("acceptance") or {}).get("min_bucket_gap")
    passed = min_gap is None or bucket_effects.get("max_bucket_gap", 0.0) >= float(min_gap)
    return {"pass": passed, "metrics": {"bucket_effects": bucket_effects, "row_count": len(rows)}}


def _analyze_h003(
    spec: Dict[str, Any],
    decision_records: List[Dict[str, Any]],
    reference_records: List[Dict[str, Any]],
    selection: Dict[str, Any],
    start_ms: Optional[int],
    end_ms: Optional[int],
) -> Dict[str, Any]:
    label_spec = spec.get("label") or {}
    window_secs = int(label_spec.get("window_secs") or 900)
    ref_index = _build_reference_index(reference_records)

    rows = []
    for record in decision_records:
        if not _match_selection_decision(record, selection):
            continue
        t_decision = _to_int(record.get("t_decision_wall_ms"))
        if t_decision is None:
            continue
        if start_ms is not None and t_decision < start_ms:
            continue
        if end_ms is not None and t_decision > end_ms:
            continue
        slug = record.get("market_slug")
        if not slug:
            continue
        window = window_start_end_ms(str(slug), window_secs=window_secs)
        if window is None:
            continue
        window_start_ms, window_end_ms = window
        if t_decision >= window_end_ms:
            raise ValueError("leakage_decision_after_label")
        symbol = ((record.get("notes") or {}).get("resolved_market") or {}).get("reference_symbol")
        if symbol is None:
            continue
        label = _label_from_reference(ref_index, str(symbol), window_start_ms, window_end_ms)
        if label is None:
            continue
        hour, dow = _hour_dow(t_decision)
        book = record.get("book") or {}
        exec_cost = record.get("exec_cost") or {}
        rows.append(
            {
                "hour": hour,
                "dow": dow,
                "p_fair": _to_float(record.get("p_fair")),
                "label": label,
                "spread_bps": _to_float(book.get("spread_bps")),
                "depth": _to_float(exec_cost.get("depth_at_qty_buy")),
            }
        )

    if not rows:
        raise ValueError("no_rows_for_h003")

    hourly = _hourly_metrics(rows)
    metrics = {
        "hourly": hourly,
        "row_count": len(rows),
    }
    max_brier = (spec.get("acceptance") or {}).get("max_brier")
    brier_overall = hourly.get("brier_overall")
    passed = max_brier is None or brier_overall is None or brier_overall <= float(max_brier)
    return {"pass": passed, "metrics": metrics}


def _expand_paths(patterns: Sequence[str]) -> List[str]:
    import glob

    paths: List[str] = []
    for pattern in patterns:
        matches = glob.glob(pattern)
        if matches:
            paths.extend(matches)
            continue
        path = Path(pattern)
        if path.exists():
            paths.append(str(path))
    return sorted(set(paths))


def _load_jsonl(paths: Iterable[str]) -> List[Dict[str, Any]]:
    records: List[Dict[str, Any]] = []
    for path in paths:
        for line in Path(path).read_text().splitlines():
            if not line:
                continue
            records.append(json.loads(line))
    return records


def _build_market_series(
    records: List[Dict[str, Any]],
    selection: Dict[str, Any],
    start_ms: Optional[int],
    end_ms: Optional[int],
    depth_levels: int,
) -> Dict[str, List[Dict[str, Any]]]:
    series: Dict[str, List[Dict[str, Any]]] = {}
    for record in records:
        if record.get("channel") != "market":
            continue
        raw = record.get("raw")
        if not isinstance(raw, dict):
            continue
        if not _match_selection_market(record, selection):
            continue
        t_ms = _event_time_ms(record)
        if t_ms is None:
            continue
        if start_ms is not None and t_ms < start_ms:
            continue
        if end_ms is not None and t_ms > end_ms:
            continue
        bids = raw.get("bids") or raw.get("buys") or []
        asks = raw.get("asks") or raw.get("sells") or []
        bid_levels = _parse_levels(bids, reverse=True)
        ask_levels = _parse_levels(asks, reverse=False)
        if not bid_levels or not ask_levels:
            continue
        best_bid, best_bid_size = bid_levels[0]
        best_ask, best_ask_size = ask_levels[0]
        mid = (best_bid + best_ask) / 2.0
        if mid <= 0 or mid >= 1:
            continue
        logit_mid = _logit(mid)
        imbalance_l1 = _imbalance(best_bid_size, best_ask_size)
        imbalance_depth = _depth_imbalance(bid_levels, ask_levels, depth_levels)
        asset_id = _extract_asset_id(raw, record)
        if asset_id is None:
            continue
        series.setdefault(asset_id, []).append(
            {
                "t_ms": t_ms,
                "logit_mid": logit_mid,
                "imbalance_l1": imbalance_l1,
                "imbalance_depth": imbalance_depth,
            }
        )
    for asset_id, rows in series.items():
        rows.sort(key=lambda row: row["t_ms"])
    return series


def _build_micro_rows(
    series: Dict[str, List[Dict[str, Any]]],
    horizons: Sequence[int],
) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for asset_id, points in series.items():
        times = [point["t_ms"] for point in points]
        for idx, point in enumerate(points):
            t_ms = point["t_ms"]
            for horizon_sec in horizons:
                label_time = t_ms + horizon_sec * 1000
                future_idx = _index_at_or_after(times, label_time)
                if future_idx is None:
                    continue
                future = points[future_idx]
                if future["t_ms"] <= t_ms:
                    raise ValueError("leakage_micro_label")
                rows.append(
                    {
                        "asset_id": asset_id,
                        "t_ms": t_ms,
                        "horizon_sec": horizon_sec,
                        "label_time_ms": future["t_ms"],
                        "delta_logit": future["logit_mid"] - point["logit_mid"],
                        "imbalance_l1": point["imbalance_l1"],
                        "imbalance_depth": point["imbalance_depth"],
                    }
                )
    rows.sort(key=lambda row: (row["t_ms"], row["asset_id"], row["horizon_sec"]))
    return rows


def _correlation_metrics(rows: List[Dict[str, Any]], horizons: Sequence[int], features: Sequence[str]) -> Dict[str, Any]:
    metrics: Dict[str, Any] = {}
    for horizon in horizons:
        subset = [row for row in rows if row["horizon_sec"] == horizon]
        if not subset:
            continue
        metrics[str(horizon)] = {}
        y = [row["delta_logit"] for row in subset]
        for feat in features:
            x = [row[feat] for row in subset]
            metrics[str(horizon)][feat] = _corr(x, y)
    return metrics


def _ridge_metrics(rows: List[Dict[str, Any]], horizons: Sequence[int]) -> Dict[str, Any]:
    try:
        import numpy as np
    except ModuleNotFoundError as exc:
        raise RuntimeError("numpy_required_for_ridge") from exc
    results: Dict[str, Any] = {}
    for horizon in horizons:
        subset = [row for row in rows if row["horizon_sec"] == horizon]
        if len(subset) < 5:
            continue
        subset.sort(key=lambda row: row["t_ms"])
        split = max(1, int(len(subset) * 0.7))
        train = subset[:split]
        test = subset[split:]
        X_train = np.asarray([[row["imbalance_l1"], row["imbalance_depth"]] for row in train], dtype=float)
        y_train = np.asarray([row["delta_logit"] for row in train], dtype=float)
        X_test = np.asarray([[row["imbalance_l1"], row["imbalance_depth"]] for row in test], dtype=float)
        y_test = np.asarray([row["delta_logit"] for row in test], dtype=float)
        l2 = 1.0
        w = _ridge_fit(X_train, y_train, l2)
        preds = X_test.dot(w)
        r2 = _r2_score(y_test, preds)
        results[str(horizon)] = {"weights": w.tolist(), "r2": r2, "n_train": len(train), "n_test": len(test)}
    return results


def _autocorr_half_life(series: Dict[str, List[Dict[str, Any]]], cadence_ms: int) -> Dict[str, Any]:
    autocorr: Dict[str, Any] = {}
    half_life_ms: Optional[int] = None
    for asset_id, points in series.items():
        times = [point["t_ms"] for point in points]
        values = [point["imbalance_l1"] for point in points]
        if len(times) < 3:
            continue
        resampled = _resample_asof(times, values, cadence_ms)
        if len(resampled) < 3:
            continue
        lag_corr = _autocorr(resampled, max_lag=min(10, len(resampled) - 1))
        autocorr[asset_id] = lag_corr
        for lag, value in lag_corr.items():
            if value is None:
                continue
            if value <= 0.5:
                half_life_ms = cadence_ms * int(lag)
                break
        if half_life_ms is not None:
            break
    return {"autocorr": autocorr, "half_life_ms": half_life_ms}


def _pass_corr(metrics: Dict[str, Any], min_corr: Optional[float]) -> bool:
    if min_corr is None:
        return True
    target = float(min_corr)
    for horizon, feats in metrics.items():
        for value in feats.values():
            if value is not None and abs(value) >= target:
                return True
    return False


def _bucket_effects(rows: List[Dict[str, Any]], bucket_edges: List[int]) -> Dict[str, Any]:
    buckets = _build_tte_buckets(bucket_edges)
    totals = {"imbalance": [], "z_mom": []}
    by_bucket: Dict[str, List[Dict[str, Any]]] = {bucket["name"]: [] for bucket in buckets}
    for row in rows:
        name = _bucket_name(row["tte_sec"], buckets)
        by_bucket[name].append(row)
        totals["imbalance"].append(abs(row["imbalance_l1"]))
        if row["z_mom"] is not None:
            totals["z_mom"].append(abs(row["z_mom"]))
    overall = {
        "imbalance": _mean(totals["imbalance"]),
        "z_mom": _mean(totals["z_mom"]),
    }
    metrics: Dict[str, Any] = {"overall": overall, "buckets": {}}
    max_gap = 0.0
    for name, bucket_rows in by_bucket.items():
        imb = _mean([abs(row["imbalance_l1"]) for row in bucket_rows])
        zm = _mean([abs(row["z_mom"]) for row in bucket_rows if row["z_mom"] is not None])
        gap = 0.0 if imb is None or overall["imbalance"] is None else imb - overall["imbalance"]
        max_gap = max(max_gap, gap)
        metrics["buckets"][name] = {
            "count": len(bucket_rows),
            "mean_abs_imbalance": imb,
            "mean_abs_z_mom": zm,
            "gap_vs_overall": gap,
            "stability": _daily_stability(bucket_rows),
        }
    metrics["max_bucket_gap"] = max_gap
    return metrics


def _hourly_metrics(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    buckets: Dict[int, List[Dict[str, Any]]] = {hour: [] for hour in range(24)}
    for row in rows:
        buckets[row["hour"]].append(row)
    hourly: Dict[str, Any] = {}
    all_labels = []
    all_preds = []
    for hour in range(24):
        entries = buckets[hour]
        labels = [row["label"] for row in entries]
        preds = [row["p_fair"] for row in entries if row["p_fair"] is not None]
        brier = _brier(preds, labels) if preds and labels else None
        hourly[str(hour)] = {
            "count": len(entries),
            "avg_spread_bps": _mean([row["spread_bps"] for row in entries if row["spread_bps"] is not None]),
            "avg_depth": _mean([row["depth"] for row in entries if row["depth"] is not None]),
            "avg_p_fair": _mean(preds),
            "empirical": _mean(labels),
            "brier": brier,
        }
        all_labels.extend(labels)
        all_preds.extend(preds)
    return {
        "hourly": hourly,
        "brier_overall": _brier(all_preds, all_labels) if all_preds and all_labels else None,
    }


def _build_reference_index(records: List[Dict[str, Any]]) -> Dict[str, List[Tuple[int, float]]]:
    index: Dict[str, List[Tuple[int, float]]] = {}
    for record in records:
        if record.get("channel") != "reference":
            continue
        t_event = _to_int(record.get("t_event_ms"))
        if t_event is None:
            continue
        raw = record.get("raw") or {}
        symbol = raw.get("symbol") or record.get("market")
        if symbol is None:
            continue
        value = raw.get("value") or raw.get("mid")
        price = _to_float(value)
        if price is None:
            continue
        index.setdefault(str(symbol), []).append((t_event, price))
    for symbol, points in index.items():
        points.sort(key=lambda item: item[0])
    return index


def _label_from_reference(
    ref_index: Dict[str, List[Tuple[int, float]]],
    symbol: str,
    window_start_ms: int,
    window_end_ms: int,
) -> Optional[int]:
    points = ref_index.get(symbol)
    if not points:
        return None
    start = _price_at_or_after(points, window_start_ms)
    end = _price_at_or_after(points, window_end_ms)
    if start is None or end is None:
        return None
    return 1 if end >= start else 0


def _match_selection_market(record: Dict[str, Any], selection: Dict[str, Any]) -> bool:
    condition_ids = set(selection.get("condition_ids") or [])
    token_ids = set(selection.get("token_ids") or [])
    raw = record.get("raw") or {}
    market = record.get("market") or raw.get("market")
    asset_id = record.get("asset_id") or raw.get("asset_id")
    if condition_ids and market not in condition_ids:
        return False
    if token_ids and asset_id not in token_ids:
        return False
    return True


def _match_selection_decision(record: Dict[str, Any], selection: Dict[str, Any]) -> bool:
    condition_ids = set(selection.get("condition_ids") or [])
    token_ids = set(selection.get("token_ids") or [])
    if condition_ids and record.get("condition_id") not in condition_ids:
        return False
    if token_ids and record.get("asset_id") not in token_ids:
        return False
    return True


def _event_time_ms(record: Dict[str, Any]) -> Optional[int]:
    return _to_int(record.get("t_event_ms") or record.get("t_recv_wall_ms"))


def _extract_asset_id(raw: Dict[str, Any], record: Dict[str, Any]) -> Optional[str]:
    return raw.get("asset_id") or record.get("asset_id")


def _parse_levels(levels: Any, reverse: bool) -> List[Tuple[float, float]]:
    parsed: List[Tuple[float, float]] = []
    if isinstance(levels, list):
        for level in levels:
            if isinstance(level, dict):
                price = _to_float(level.get("price"))
                size = _to_float(level.get("size"))
                if price is None or size is None:
                    continue
                parsed.append((price, size))
    parsed.sort(key=lambda item: item[0], reverse=reverse)
    return parsed


def _imbalance(bid_size: float, ask_size: float) -> float:
    denom = bid_size + ask_size
    if denom <= 0:
        return 0.0
    return (bid_size - ask_size) / denom


def _depth_imbalance(
    bids: List[Tuple[float, float]],
    asks: List[Tuple[float, float]],
    levels: int,
) -> float:
    if levels <= 0:
        return 0.0
    bid_depth = sum(size for _, size in bids[:levels])
    ask_depth = sum(size for _, size in asks[:levels])
    return _imbalance(bid_depth, ask_depth)


def _logit(p: float) -> float:
    p = min(max(p, 1e-6), 1.0 - 1e-6)
    return math.log(p / (1.0 - p))


def _index_at_or_after(times: List[int], ts: int) -> Optional[int]:
    lo = 0
    hi = len(times)
    while lo < hi:
        mid = (lo + hi) // 2
        if times[mid] < ts:
            lo = mid + 1
        else:
            hi = mid
    if lo >= len(times):
        return None
    return lo


def _asof_value(points: List[Dict[str, Any]], ts_ms: int, key: str) -> Optional[float]:
    times = [point["t_ms"] for point in points]
    idx = _index_before(times, ts_ms)
    if idx is None:
        return None
    return points[idx].get(key)


def _index_before(times: List[int], ts: int) -> Optional[int]:
    lo = 0
    hi = len(times)
    while lo < hi:
        mid = (lo + hi) // 2
        if times[mid] < ts:
            lo = mid + 1
        else:
            hi = mid
    idx = lo - 1
    if idx < 0:
        return None
    return idx


def _corr(xs: Sequence[float], ys: Sequence[float]) -> Optional[float]:
    if not xs or not ys or len(xs) != len(ys):
        return None
    mean_x = sum(xs) / len(xs)
    mean_y = sum(ys) / len(ys)
    cov = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys))
    var_x = sum((x - mean_x) ** 2 for x in xs)
    var_y = sum((y - mean_y) ** 2 for y in ys)
    if var_x <= 0 or var_y <= 0:
        return None
    return cov / math.sqrt(var_x * var_y)


def _ridge_fit(X, y, l2: float):
    import numpy as np

    XT = X.T
    n = X.shape[1]
    A = XT.dot(X) + l2 * np.eye(n)
    b = XT.dot(y)
    return np.linalg.solve(A, b)


def _r2_score(y_true, y_pred) -> Optional[float]:
    if y_true.size == 0:
        return None
    mean = float(y_true.mean())
    ss_tot = float(((y_true - mean) ** 2).sum())
    ss_res = float(((y_true - y_pred) ** 2).sum())
    if ss_tot <= 0:
        return None
    return 1.0 - ss_res / ss_tot


def _resample_asof(times: List[int], values: List[float], cadence_ms: int) -> List[float]:
    if not times:
        return []
    start = times[0]
    end = times[-1]
    sampled: List[float] = []
    idx = 0
    for ts in range(start, end + 1, cadence_ms):
        while idx + 1 < len(times) and times[idx + 1] <= ts:
            idx += 1
        sampled.append(values[idx])
    return sampled


def _autocorr(series: List[float], max_lag: int) -> Dict[str, Optional[float]]:
    results: Dict[str, Optional[float]] = {}
    for lag in range(1, max_lag + 1):
        xs = series[:-lag]
        ys = series[lag:]
        results[str(lag)] = _corr(xs, ys)
    return results


def _mean(values: Sequence[Optional[float]]) -> Optional[float]:
    vals = [v for v in values if v is not None]
    if not vals:
        return None
    return sum(vals) / len(vals)


def _brier(preds: Sequence[float], labels: Sequence[int]) -> Optional[float]:
    if not preds or not labels:
        return None
    n = min(len(preds), len(labels))
    total = 0.0
    for i in range(n):
        total += (preds[i] - labels[i]) ** 2
    return total / n


def _hour_dow(ts_ms: int) -> Tuple[int, int]:
    dt = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc)
    return dt.hour, dt.weekday()


def _price_at_or_after(points: List[Tuple[int, float]], ts_ms: int) -> Optional[float]:
    for ts, price in points:
        if ts >= ts_ms:
            return price
    return None


def _build_tte_buckets(edges: List[int]) -> List[Dict[str, float]]:
    if not edges:
        return [{"name": ">=0s", "lower": 0.0, "upper": float("inf")}]
    edges = sorted(edges, reverse=True)
    buckets: List[Dict[str, float]] = []
    upper = float("inf")
    for edge in edges:
        name = f">={int(edge)}s" if upper == float("inf") else f"{int(edge)}-{int(upper)}s"
        buckets.append({"name": name, "lower": float(edge), "upper": upper})
        upper = float(edge)
    buckets.append({"name": f"0-{int(upper)}s", "lower": 0.0, "upper": upper})
    return buckets


def _bucket_name(value: float, buckets: List[Dict[str, float]]) -> str:
    for bucket in buckets:
        lower = bucket["lower"]
        upper = bucket["upper"]
        if lower <= value < upper:
            return str(bucket["name"])
    return "unknown"


def _daily_stability(rows: List[Dict[str, Any]]) -> Optional[float]:
    by_day: Dict[str, List[float]] = {}
    for row in rows:
        day = datetime.fromtimestamp(row["t_decision"] / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
        by_day.setdefault(day, []).append(abs(row["imbalance_l1"]))
    if not by_day:
        return None
    means = [_mean(values) for values in by_day.values() if values]
    if not means:
        return None
    mean = sum(means) / len(means)
    var = sum((value - mean) ** 2 for value in means) / len(means)
    return math.sqrt(var)


def _to_float(value: Any) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _to_int(value: Any) -> Optional[int]:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _render_report(results: Dict[str, Any]) -> str:
    lines = []
    status = "PASS" if results.get("pass") else "FAIL"
    lines.append(f"{status}: {results.get('hypothesis_id')} {results.get('name')}")
    metrics = results.get("metrics") or {}
    lines.append("")
    lines.append("Summary")
    lines.append("- rows: " + str(metrics.get("row_count")))
    if "correlation" in metrics:
        lines.append("- correlation: " + json.dumps(metrics.get("correlation"), sort_keys=True))
    if "ridge_regression" in metrics:
        lines.append("- ridge_regression: " + json.dumps(metrics.get("ridge_regression"), sort_keys=True))
    if "autocorr_half_life" in metrics:
        lines.append("- autocorr_half_life: " + json.dumps(metrics.get("autocorr_half_life"), sort_keys=True))
    if "bucket_effects" in metrics:
        lines.append("- bucket_effects: " + json.dumps(metrics.get("bucket_effects"), sort_keys=True))
    if "hourly" in metrics:
        lines.append("- hourly: " + json.dumps(metrics.get("hourly"), sort_keys=True))
    lines.append("")
    lines.append("Leakage checks")
    lines.append("- enforced: feature_time < label_time")
    return "\n".join(lines)
