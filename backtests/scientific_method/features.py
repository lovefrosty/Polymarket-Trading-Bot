from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Deque, Dict, List, Optional, Tuple

from core.order_book import OrderBook


@dataclass
class FeatureHistory:
    imbalance: Dict[str, Deque[Tuple[int, float, float]]] = field(default_factory=dict)
    logit_price: Dict[str, Deque[Tuple[int, float]]] = field(default_factory=dict)
    vol_history: Dict[str, Dict[float, Deque[Tuple[int, float]]]] = field(default_factory=dict)
    ref_returns: Dict[str, Deque[Tuple[int, float]]] = field(default_factory=dict)

    def update_market(
        self,
        asset_id: str,
        t_ms: int,
        imbalance_l1: float,
        imbalance_depth: float,
        logit_price: Optional[float],
    ) -> None:
        self.imbalance.setdefault(asset_id, deque(maxlen=5000)).append((t_ms, imbalance_l1, imbalance_depth))
        if logit_price is not None:
            self.logit_price.setdefault(asset_id, deque(maxlen=5000)).append((t_ms, logit_price))

    def update_vol(self, symbol: str, half_life: float, t_ms: int, sigma: float) -> None:
        bucket = self.vol_history.setdefault(symbol, {})
        bucket.setdefault(half_life, deque(maxlen=5000)).append((t_ms, sigma))

    def update_ref_return(self, symbol: str, t_ms: int, r: float) -> None:
        self.ref_returns.setdefault(symbol, deque(maxlen=20000)).append((t_ms, r))


def book_features(book: OrderBook, depth_levels: int, qty: float) -> Dict[str, Optional[float]]:
    bid, bid_size = _best_level(book.bids, reverse=True)
    ask, ask_size = _best_level(book.asks, reverse=False)
    imbalance_l1 = _imbalance(bid_size, ask_size) if bid_size is not None and ask_size is not None else None
    imbalance_depth = _depth_imbalance(book, depth_levels)
    spread_bps = book.spread_bps()
    depth_at_qty = book.depth_at_qty("buy", qty)
    slippage_bps = book.expected_slippage_to_fill("buy", qty)
    exec_buy = book.vwap_to_fill("buy", qty)
    exec_sell = book.vwap_to_fill("sell", qty)
    return {
        "best_bid": bid,
        "best_ask": ask,
        "imbalance_l1": imbalance_l1,
        "imbalance_depth": imbalance_depth,
        "spread_bps": spread_bps,
        "depth_at_qty": depth_at_qty,
        "slippage_bps": slippage_bps,
        "p_market_exec_buy": exec_buy,
        "p_market_exec_sell": exec_sell,
    }


def imbalance_persistence(
    history: FeatureHistory,
    asset_id: str,
    t_ms: int,
    windows_sec: List[int],
) -> Dict[str, Optional[float]]:
    rows = history.imbalance.get(asset_id) or deque()
    out: Dict[str, Optional[float]] = {}
    for window in windows_sec:
        start = t_ms - window * 1000
        values = [val for ts, val, _ in rows if ts < t_ms and ts >= start]
        out[f"imbalance_mean_{window}s"] = _mean(values)
    return out


def odds_momentum(
    history: FeatureHistory,
    asset_id: str,
    t_ms: int,
    windows_sec: List[int],
) -> Dict[str, Optional[float]]:
    rows = history.logit_price.get(asset_id) or deque()
    out: Dict[str, Optional[float]] = {}
    for window in windows_sec:
        prev = _asof(rows, t_ms - window * 1000)
        now = _asof(rows, t_ms - 1)
        if prev is None or now is None:
            out[f"logit_ret_{window}s"] = None
            out[f"mean_revert_{window}s"] = None
            continue
        out[f"logit_ret_{window}s"] = now[1] - prev[1]
        out[f"mean_revert_{window}s"] = prev[1] - now[1]
    return out


def time_of_day_features(t_ms: int) -> Dict[str, float]:
    dt = datetime.fromtimestamp(t_ms / 1000.0, tz=timezone.utc)
    hour = dt.hour + dt.minute / 60.0
    angle = 2.0 * math.pi * hour / 24.0
    return {
        "hour_sin": math.sin(angle),
        "hour_cos": math.cos(angle),
        "is_weekend": 1.0 if dt.weekday() >= 5 else 0.0,
    }


def vol_features(
    history: FeatureHistory,
    symbol: str,
    t_ms: int,
    half_lives: List[float],
) -> Dict[str, Optional[float]]:
    out: Dict[str, Optional[float]] = {}
    for hl in half_lives:
        key = f"vol_ewma_{int(hl)}s"
        out[key] = _latest_before((history.vol_history.get(symbol) or {}).get(hl), t_ms)
    for hl in half_lives:
        key = f"vol_change_{int(hl)}s"
        out[key] = _vol_change((history.vol_history.get(symbol) or {}).get(hl), t_ms)
    return out


def vol_regime_percentile(
    history: FeatureHistory,
    symbol: str,
    t_ms: int,
    half_life_sec: float,
    window_sec: int,
) -> Optional[float]:
    series = (history.vol_history.get(symbol) or {}).get(half_life_sec) or deque()
    if not series:
        return None
    end = t_ms - 1
    start = end - window_sec * 1000
    values = [val for ts, val in series if ts < end and ts >= start]
    current = _latest_before(series, t_ms, half_life_sec)
    if current is None or not values:
        return None
    below = sum(1 for v in values if v <= current)
    return below / len(values)


def belief_lag_metric(
    history: FeatureHistory,
    target_symbol: str,
    base_symbol: str,
    t_ms: int,
    lags_sec: List[int],
    window_sec: int,
    min_corr: float,
) -> Dict[str, Optional[float]]:
    base = history.ref_returns.get(base_symbol)
    target = history.ref_returns.get(target_symbol)
    if not base or not target:
        return {"belief_lag_sec": None, "belief_lag_corr": None}
    window_start = t_ms - window_sec * 1000
    base_points = [(ts, r) for ts, r in base if window_start <= ts < t_ms]
    target_points = [(ts, r) for ts, r in target if window_start <= ts < t_ms]
    if not base_points or not target_points:
        return {"belief_lag_sec": None, "belief_lag_corr": None}
    best_lag = None
    best_corr = None
    for lag in lags_sec:
        corr = _lead_lag_corr(base_points, target_points, lag * 1000)
        if corr is None:
            continue
        if best_corr is None or corr > best_corr:
            best_corr = corr
            best_lag = lag
    if best_corr is None or best_corr < min_corr:
        return {"belief_lag_sec": None, "belief_lag_corr": best_corr}
    return {"belief_lag_sec": float(best_lag), "belief_lag_corr": float(best_corr)}


def logit(p: float) -> Optional[float]:
    if p is None or p <= 0 or p >= 1:
        return None
    return math.log(p / (1.0 - p))


def _best_level(levels: Dict[float, float], reverse: bool) -> Tuple[Optional[float], Optional[float]]:
    if not levels:
        return None, None
    price = max(levels) if reverse else min(levels)
    return price, levels.get(price)


def _depth_imbalance(book: OrderBook, levels: int) -> Optional[float]:
    if levels <= 0:
        return None
    bids = sorted(book.bids.items(), key=lambda x: x[0], reverse=True)[:levels]
    asks = sorted(book.asks.items(), key=lambda x: x[0])[:levels]
    bid_depth = sum(size for _, size in bids)
    ask_depth = sum(size for _, size in asks)
    return _imbalance(bid_depth, ask_depth)


def _imbalance(bid_size: float, ask_size: float) -> float:
    denom = bid_size + ask_size
    if denom <= 0:
        return 0.0
    return (bid_size - ask_size) / denom


def _mean(values: List[float]) -> Optional[float]:
    if not values:
        return None
    return sum(values) / len(values)


def _asof(rows: Deque[Tuple[int, float]], t_ms: int) -> Optional[Tuple[int, float]]:
    latest = None
    for ts, value in rows:
        if ts < t_ms:
            latest = (ts, value)
        else:
            break
    return latest


def _latest_before(series: Optional[Deque[Tuple[int, float]]], t_ms: int) -> Optional[float]:
    if not series:
        return None
    for ts, value in reversed(series):
        if ts < t_ms:
            return value
    return None


def _vol_change(series: Optional[Deque[Tuple[int, float]]], t_ms: int) -> Optional[float]:
    if not series or len(series) < 2:
        return None
    current = _latest_before(series, t_ms, 0.0)
    prev = None
    for ts, value in reversed(series):
        if ts < t_ms and value != current:
            prev = value
            break
    if current is None or prev is None or current <= 0 or prev <= 0:
        return None
    return math.log(current / prev)


def _lead_lag_corr(
    base_points: List[Tuple[int, float]],
    target_points: List[Tuple[int, float]],
    lag_ms: int,
) -> Optional[float]:
    base_map = {ts: r for ts, r in base_points}
    xs: List[float] = []
    ys: List[float] = []
    for ts, r in target_points:
        base_ts = ts - lag_ms
        if base_ts in base_map:
            xs.append(base_map[base_ts])
            ys.append(r)
    if len(xs) < 3:
        return None
    mean_x = sum(xs) / len(xs)
    mean_y = sum(ys) / len(ys)
    cov = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys))
    var_x = sum((x - mean_x) ** 2 for x in xs)
    var_y = sum((y - mean_y) ** 2 for y in ys)
    if var_x <= 0 or var_y <= 0:
        return None
    return cov / math.sqrt(var_x * var_y)
