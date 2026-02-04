from __future__ import annotations

import bisect
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from core.market_time import window_start_end_ms
from core.volatility import ewma_variance_update


@dataclass(frozen=True)
class ReferenceRow:
    row: Dict[str, Any]


def build_reference_window_dataset(
    reference_tape_paths: List[Path],
    symbol: str,
    window_secs: int = 900,
    horizon_secs: int = 900,
    lookbacks: Dict[str, int] | None = None,
    ewma_halflife_secs: int = 300,
    clip_sigma: float = 8.0,
    tz: str = "UTC",
) -> List[Dict[str, Any]]:
    if lookbacks is None:
        lookbacks = {"ret_60s": 60, "ret_300s": 300, "ret_900s": 900}
    points = _load_reference_points(reference_tape_paths, symbol)
    if not points:
        return []
    points.sort(key=lambda item: item[0])
    min_ts = points[0][0]
    max_ts = points[-1][0]
    window_ms = window_secs * 1000
    horizon_ms = horizon_secs * 1000
    start_sec = (min_ts // 1000) // window_secs * window_secs
    end_sec = (max_ts // 1000) // window_secs * window_secs

    rows: List[Dict[str, Any]] = []
    max_lookback = max(lookbacks.values())
    for window_start_sec in range(start_sec, end_sec + 1, window_secs):
        as_of_ts_ms = window_start_sec * 1000
        window_start_ts_ms = as_of_ts_ms
        window_end_ts_ms = window_start_ts_ms + window_ms
        label_end_ts_ms = as_of_ts_ms + horizon_ms
        price_t0 = _price_at_or_after(points, window_start_ts_ms)
        price_t1 = _price_at_or_after(points, label_end_ts_ms)
        if price_t0 is None or price_t1 is None:
            continue
        p_start, t0_ts = price_t0
        p_end, t1_ts = price_t1

        p_asof = _price_before(points, as_of_ts_ms)
        if p_asof is None:
            continue
        p_asof_val, _ = p_asof

        features = {}
        for name, lookback_sec in lookbacks.items():
            lookback_ts = as_of_ts_ms - lookback_sec * 1000
            p_prev = _price_before(points, lookback_ts)
            if p_prev is None:
                features[name] = None
                continue
            features[name] = _log_return(p_asof_val, p_prev[0])

        vol = _ewma_vol(points, as_of_ts_ms, ewma_halflife_secs)
        features["ewma_vol_300s"] = vol
        z_mom = _safe_div(features.get("ret_60s"), vol)
        ret_300 = features.get("ret_300s")
        z_rev = _safe_div(-ret_300, vol) if ret_300 is not None else None
        if z_mom is not None:
            z_mom = _clip(z_mom, -clip_sigma, clip_sigma)
        if z_rev is not None:
            z_rev = _clip(z_rev, -clip_sigma, clip_sigma)
        features["z_mom"] = z_mom
        features["z_rev"] = z_rev
        features["mom_ortho"] = None

        events_used = _count_events(points, as_of_ts_ms - max_lookback * 1000, as_of_ts_ms)

        row = {
            "schema_version": "ref_window_v1",
            "symbol": symbol,
            "as_of_ts_ms": as_of_ts_ms,
            "window_start_ts_ms": window_start_ts_ms,
            "window_end_ts_ms": window_end_ts_ms,
            "features": features,
            "label_up": 1 if p_end >= p_start else 0,
            "meta": {
                "n_ref_events_used": events_used,
                "label_price_t0_ts_ms": t0_ts,
                "label_price_t1_ts_ms": t1_ts,
                "leakage_guard": "STRICT",
                "tz": tz,
            },
        }
        rows.append(row)
    return rows


def build_microstructure_dataset_from_decisions(
    decision_tape_paths: List[Path],
    reference_labels_index: Dict[Tuple[str, int], int],
    feature_contract: Optional[List[Dict[str, Any]]] = None,
) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for path in decision_tape_paths:
        for line in path.read_text().splitlines():
            if not line:
                continue
            record = json.loads(line)
            if record.get("schema_version") is None:
                continue
            slug = record.get("market_slug")
            if not slug:
                continue
            window = window_start_end_ms(str(slug))
            if window is None:
                continue
            window_start_ts_ms, window_end_ts_ms = window
            notes = record.get("notes") or {}
            resolved = notes.get("resolved_market") or {}
            symbol = resolved.get("reference_symbol")
            if symbol is None:
                continue
            label = reference_labels_index.get((str(symbol), window_start_ts_ms))
            if label is None:
                continue
            as_of_ts_ms = record.get("t_decision_wall_ms")
            if as_of_ts_ms is None:
                continue

            book = record.get("book") or {}
            exec_cost = record.get("exec_cost") or {}
            signals = notes.get("signals") or {}
            entry_gate = notes.get("entry_gate") or {}

            row = {
                "schema_version": "micro_decision_v1",
                "symbol": str(symbol),
                "as_of_ts_ms": int(as_of_ts_ms),
                "window_start_ts_ms": window_start_ts_ms,
                "window_end_ts_ms": window_end_ts_ms,
                "asset_id": record.get("asset_id"),
                "token_id": record.get("token_id"),
                "outcome": record.get("outcome"),
                "p_market_exec_buy": record.get("p_market_exec_buy"),
                "p_market_exec_sell": record.get("p_market_exec_sell"),
                "edge_net_buy": record.get("edge_net_buy"),
                "edge_net_sell": record.get("edge_net_sell"),
                "gates_allow": record.get("gates", {}).get("allow"),
                "gates_reasons": record.get("gates", {}).get("reasons"),
                "entry_allow": entry_gate.get("allow"),
                "entry_reasons": entry_gate.get("reasons"),
                "spread_bps": book.get("spread_bps"),
                "depth_at_qty_buy": exec_cost.get("depth_at_qty_buy"),
                "depth_at_qty_sell": exec_cost.get("depth_at_qty_sell"),
                "slippage_bps_buy": exec_cost.get("slippage_bps_buy"),
                "slippage_bps_sell": exec_cost.get("slippage_bps_sell"),
                "tox_10s": signals.get("tox_10s"),
                "z_mom": signals.get("z_mom"),
                "z_rev": signals.get("z_revert") or signals.get("z_rev"),
                "ewma_vol": signals.get("vol_ewma"),
                "label_up": label,
            }
            if feature_contract:
                row["features_meta"] = _feature_meta(record, feature_contract)
            rows.append(row)
    rows.sort(key=lambda row: (row["as_of_ts_ms"], str(row.get("asset_id")), str(row.get("token_id")), str(row.get("outcome"))))
    return rows


def _load_reference_points(paths: Iterable[Path], symbol: str) -> List[Tuple[int, float]]:
    points: List[Tuple[int, float]] = []
    for path in paths:
        for line in path.read_text().splitlines():
            if not line:
                continue
            record = json.loads(line)
            if record.get("channel") != "reference":
                continue
            raw = record.get("raw") or {}
            ref_symbol = raw.get("symbol")
            if ref_symbol is None:
                # Fallback to EventTape market field when symbol is absent.
                ref_symbol = record.get("market")
            if ref_symbol is None or str(ref_symbol) != symbol:
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
            points.append((ts, price))
    return points


def _price_before(points: List[Tuple[int, float]], ts_ms: int) -> Optional[Tuple[float, int]]:
    idx = bisect.bisect_left(points, (ts_ms, -float("inf"))) - 1
    if idx < 0:
        return None
    ts, price = points[idx]
    if ts >= ts_ms:
        return None
    return price, ts


def _price_at_or_after(points: List[Tuple[int, float]], ts_ms: int) -> Optional[Tuple[float, int]]:
    idx = bisect.bisect_left(points, (ts_ms, -float("inf")))
    if idx >= len(points):
        return None
    ts, price = points[idx]
    if ts < ts_ms:
        return None
    return price, ts


def _feature_meta(record: Dict[str, Any], contract: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    meta: Dict[str, Dict[str, Any]] = {}
    as_of_ts = _feature_asof_ts(record)
    for entry in contract:
        name = str(entry.get("name") or "")
        path = str(entry.get("path") or name)
        if not name:
            continue
        value = _extract_path(record, path)
        meta[name] = {"present": value is not None, "as_of_ts_ms": as_of_ts}
    return meta


def _feature_asof_ts(record: Dict[str, Any]) -> Optional[int]:
    value = record.get("feature_asof_ts_ms")
    if value is not None:
        return int(value)
    notes = record.get("notes") or {}
    value = notes.get("feature_asof_ts_ms")
    if value is not None:
        return int(value)
    return None


def _extract_path(record: Dict[str, Any], path: str) -> Optional[Any]:
    current: Any = record
    for key in path.split("."):
        if not isinstance(current, dict):
            return None
        current = current.get(key)
        if current is None:
            return None
    return current


def _ewma_vol(points: List[Tuple[int, float]], as_of_ts_ms: int, half_life_sec: int) -> Optional[float]:
    cutoff_ms = as_of_ts_ms - 900 * 1000
    filtered = [p for p in points if cutoff_ms <= p[0] < as_of_ts_ms]
    if len(filtered) < 2:
        return None
    var = 0.0
    for (t0, p0), (t1, p1) in zip(filtered, filtered[1:]):
        dt_sec = (t1 - t0) / 1000.0
        if dt_sec <= 0:
            continue
        r = _log_return(p1, p0)
        var = ewma_variance_update(var, r, dt_sec, half_life_sec)
    if var <= 0:
        return None
    return var ** 0.5


def _log_return(p_now: float, p_prev: float) -> float:
    if p_prev <= 0 or p_now <= 0:
        return 0.0
    return math_log(p_now / p_prev)


def _count_events(points: List[Tuple[int, float]], start_ms: int, end_ms: int) -> int:
    left = bisect.bisect_left(points, (start_ms, -float("inf")))
    right = bisect.bisect_left(points, (end_ms, -float("inf")))
    return max(0, right - left)


def _safe_div(num: Optional[float], denom: Optional[float]) -> Optional[float]:
    if num is None or denom is None or denom == 0:
        return None
    return num / denom


def _clip(value: float, lo: float, hi: float) -> float:
    if value < lo:
        return lo
    if value > hi:
        return hi
    return value


def math_log(value: float) -> float:
    import math

    return math.log(value)
