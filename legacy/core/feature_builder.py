from __future__ import annotations

from dataclasses import dataclass
import bisect
import hashlib
import math
from typing import Any, Dict, List, Optional, Tuple

from core.reference_price import ReferenceQuote
from core.volatility import ewma_variance_update


FEATURE_ORDER = [
    "ret_60s",
    "ret_300s",
    "ret_900s",
    "ewma_vol_300s",
    "z_mom",
    "z_rev",
]


@dataclass(frozen=True)
class FeatureConfig:
    lookbacks: Dict[str, int]
    ewma_halflife_secs: int
    clip_sigma: float
    ewma_window_secs: int = 900
    max_history_ms: int = 6 * 60 * 60 * 1000


class FeatureBuildError(ValueError):
    def __init__(self, code: str, detail: Optional[str] = None) -> None:
        message = code if detail is None else f"{code}:{detail}"
        super().__init__(message)
        self.code = code
        self.detail = detail


class ReferenceFeatureState:
    def __init__(self, max_history_ms: int) -> None:
        self._points: List[Tuple[int, float]] = []
        self._max_event_ts: Optional[int] = None
        self._max_history_ms = max_history_ms

    def ingest(self, quote: ReferenceQuote) -> None:
        if quote.t_event_ms is None:
            return
        ts = int(quote.t_event_ms)
        price = float(quote.value)
        idx = bisect.bisect_right(self._points, (ts, float("inf")))
        self._points.insert(idx, (ts, price))
        self._max_event_ts = ts if self._max_event_ts is None else max(self._max_event_ts, ts)
        self._prune()

    def points(self) -> List[Tuple[int, float]]:
        return self._points

    def _prune(self) -> None:
        if self._max_event_ts is None:
            return
        cutoff = self._max_event_ts - self._max_history_ms
        idx = bisect.bisect_left(self._points, (cutoff, -float("inf")))
        if idx > 0:
            del self._points[:idx]


def default_feature_config() -> FeatureConfig:
    return FeatureConfig(
        lookbacks={"ret_60s": 60, "ret_300s": 300, "ret_900s": 900},
        ewma_halflife_secs=300,
        clip_sigma=8.0,
        ewma_window_secs=900,
        max_history_ms=6 * 60 * 60 * 1000,
    )


def feature_order_hash(feature_order: List[str]) -> str:
    joined = ",".join(feature_order).encode("utf-8")
    return hashlib.sha256(joined).hexdigest()


def build_feature_vector(
    decision_context: Dict[str, Any],
    ref_state: ReferenceFeatureState,
    config: FeatureConfig,
) -> Tuple[List[str], List[float]]:
    decision_ts_ms = decision_context.get("t_decision_wall_ms")
    if decision_ts_ms is None:
        raise FeatureBuildError("DECISION_TS_MISSING")
    try:
        decision_ts_ms = int(decision_ts_ms)
    except (TypeError, ValueError) as exc:
        raise FeatureBuildError("DECISION_TS_INVALID") from exc

    points = ref_state.points()
    if not points:
        raise FeatureBuildError("FEATURES_INSUFFICIENT_HISTORY")
    idx = bisect.bisect_left(points, (decision_ts_ms, -float("inf")))
    history = points[:idx]
    if len(history) < 2:
        raise FeatureBuildError("FEATURES_INSUFFICIENT_HISTORY")

    p_asof = _price_before(history, decision_ts_ms)
    if p_asof is None:
        raise FeatureBuildError("FEATURE_MISSING")
    p_asof_val = p_asof[0]

    features: Dict[str, Optional[float]] = {}
    for name, lookback_sec in config.lookbacks.items():
        lookback_ts = decision_ts_ms - lookback_sec * 1000
        p_prev = _price_before(history, lookback_ts)
        if p_prev is None:
            features[name] = None
        else:
            features[name] = _log_return(p_asof_val, p_prev[0])

    vol = _ewma_vol(history, decision_ts_ms, config.ewma_halflife_secs, config.ewma_window_secs)
    features["ewma_vol_300s"] = vol

    z_mom = _safe_div(features.get("ret_60s"), vol)
    ret_300 = features.get("ret_300s")
    z_rev = _safe_div(-ret_300, vol) if ret_300 is not None else None
    if z_mom is not None:
        z_mom = _clip(z_mom, -config.clip_sigma, config.clip_sigma)
    if z_rev is not None:
        z_rev = _clip(z_rev, -config.clip_sigma, config.clip_sigma)
    features["z_mom"] = z_mom
    features["z_rev"] = z_rev

    vector: List[float] = []
    for key in FEATURE_ORDER:
        value = features.get(key)
        if value is None or not math.isfinite(float(value)):
            raise FeatureBuildError("NAN_FEATURES", key)
        vector.append(float(value))

    return FEATURE_ORDER, vector


def _price_before(points: List[Tuple[int, float]], ts_ms: int) -> Optional[Tuple[float, int]]:
    idx = bisect.bisect_left(points, (ts_ms, -float("inf"))) - 1
    if idx < 0:
        return None
    ts, price = points[idx]
    if ts >= ts_ms:
        return None
    return price, ts


def _ewma_vol(
    points: List[Tuple[int, float]],
    as_of_ts_ms: int,
    half_life_sec: int,
    window_sec: int,
) -> Optional[float]:
    cutoff_ms = as_of_ts_ms - window_sec * 1000
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
    return math.log(p_now / p_prev)


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
