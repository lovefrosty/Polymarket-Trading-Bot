from __future__ import annotations

from dataclasses import asdict, dataclass, field
import math
from typing import Any, Dict, Optional


def _alpha(dt_sec: float, half_life_sec: float) -> float:
    if half_life_sec <= 0:
        raise ValueError("half_life_sec must be positive")
    if dt_sec <= 0:
        return 0.0
    return 1.0 - math.exp(-math.log(2.0) * dt_sec / half_life_sec)


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def _tanh_score(value: float) -> float:
    if not math.isfinite(value):
        return 0.0
    return float(_clamp(math.tanh(value), -1.0, 1.0))


def _sanitize_probability(value: Optional[float], epsilon: float) -> Optional[float]:
    if value is None:
        return None
    try:
        p = float(value)
    except (TypeError, ValueError):
        return None
    if p < 0.0 or p > 1.0:
        return None
    return _clamp(p, epsilon, 1.0 - epsilon)


@dataclass(frozen=True)
class TrendConfig:
    short_half_life_sec: float = 15.0
    long_half_life_sec: float = 60.0
    regime_threshold: float = 0.35


@dataclass(frozen=True)
class MomentumConfig:
    half_life_sec: float = 30.0
    regime_threshold: float = 0.35


@dataclass(frozen=True)
class MeanReversionConfig:
    z_clip: float = 3.0
    regime_threshold: float = 0.35


@dataclass(frozen=True)
class DispersionFloorConfig:
    abs_floor: float = 0.0025
    half_life_sec: float = 120.0


@dataclass(frozen=True)
class ClassicSignalConfig:
    warmup_updates: int = 5
    epsilon: float = 1e-6
    trend: TrendConfig = field(default_factory=TrendConfig)
    momentum: MomentumConfig = field(default_factory=MomentumConfig)
    mean_reversion: MeanReversionConfig = field(default_factory=MeanReversionConfig)
    dispersion_floor: DispersionFloorConfig = field(default_factory=DispersionFloorConfig)


@dataclass(frozen=True)
class ClassicSignalSnapshot:
    as_of_ts_ms: int
    market_as_of_ts_ms: Optional[int]
    fair_as_of_ts_ms: Optional[int]
    valid: bool
    invalid_reason: Optional[str]
    warmup_remaining: int
    market_anchor: Optional[float]
    p_fair: Optional[float]
    residual: Optional[float]
    trend_score: float
    momentum_score: float
    mean_reversion_score: float
    residual_zscore: Optional[float]
    trend_regime: str
    momentum_regime: str
    reversion_regime: str
    composite_regime: str
    dispersion: Optional[float]
    dispersion_floored: bool

    def as_dict(self) -> Dict[str, Any]:
        return asdict(self)


class ClassicSignalState:
    def __init__(self, config: Optional[ClassicSignalConfig] = None) -> None:
        self.config = config or ClassicSignalConfig()
        self._last_as_of_ts_ms: Optional[int] = None
        self._sample_count = 0
        self._trend_short_ema = 0.0
        self._trend_long_ema = 0.0
        self._momentum_ema = 0.0
        self._residual_var = 0.0
        self._last_residual: Optional[float] = None
        self._snapshot = ClassicSignalSnapshot(
            as_of_ts_ms=0,
            market_as_of_ts_ms=None,
            fair_as_of_ts_ms=None,
            valid=False,
            invalid_reason="warmup",
            warmup_remaining=max(0, int(self.config.warmup_updates)),
            market_anchor=None,
            p_fair=None,
            residual=None,
            trend_score=0.0,
            momentum_score=0.0,
            mean_reversion_score=0.0,
            residual_zscore=None,
            trend_regime="unknown",
            momentum_regime="unknown",
            reversion_regime="unknown",
            composite_regime="unknown",
            dispersion=None,
            dispersion_floored=False,
        )

    def snapshot(self) -> ClassicSignalSnapshot:
        return self._snapshot

    def update(
        self,
        *,
        as_of_ts_ms: int,
        p_fair: Optional[float],
        best_bid: Optional[float],
        best_ask: Optional[float],
        best_bid_size: Optional[float],
        best_ask_size: Optional[float],
        market_as_of_ts_ms: Optional[int] = None,
        fair_as_of_ts_ms: Optional[int] = None,
    ) -> ClassicSignalSnapshot:
        try:
            as_of = int(as_of_ts_ms)
        except (TypeError, ValueError):
            as_of = 0

        if self._last_as_of_ts_ms is not None and as_of <= self._last_as_of_ts_ms:
            self._snapshot = self._invalid_snapshot(
                as_of_ts_ms=as_of,
                market_as_of_ts_ms=market_as_of_ts_ms,
                fair_as_of_ts_ms=fair_as_of_ts_ms,
                market_anchor=_market_anchor(best_bid, best_ask, best_bid_size, best_ask_size),
                p_fair=p_fair,
                invalid_reason="timestamp_regression",
            )
            return self._snapshot

        anchor = _market_anchor(best_bid, best_ask, best_bid_size, best_ask_size)
        if anchor is None:
            self._snapshot = self._invalid_snapshot(
                as_of_ts_ms=as_of,
                market_as_of_ts_ms=market_as_of_ts_ms,
                fair_as_of_ts_ms=fair_as_of_ts_ms,
                market_anchor=None,
                p_fair=p_fair,
                invalid_reason="missing_market_anchor",
            )
            return self._snapshot

        fair_internal = _sanitize_probability(p_fair, self.config.epsilon)
        anchor_internal = _sanitize_probability(anchor, self.config.epsilon)
        if p_fair is None or fair_internal is None or anchor_internal is None:
            invalid_reason = "missing_p_fair" if p_fair is None else "probability_out_of_bounds"
            self._snapshot = self._invalid_snapshot(
                as_of_ts_ms=as_of,
                market_as_of_ts_ms=market_as_of_ts_ms,
                fair_as_of_ts_ms=fair_as_of_ts_ms,
                market_anchor=anchor,
                p_fair=p_fair,
                invalid_reason=invalid_reason,
            )
            return self._snapshot

        residual = float(anchor_internal - fair_internal)
        dt_sec = 0.0
        if self._last_as_of_ts_ms is not None:
            dt_sec = max(0.0, float(as_of - self._last_as_of_ts_ms) / 1000.0)

        if self._sample_count == 0 or self._last_as_of_ts_ms is None:
            self._trend_short_ema = residual
            self._trend_long_ema = residual
            self._momentum_ema = 0.0
            self._residual_var = 0.0
            residual_delta = 0.0
        else:
            trend_short_alpha = _alpha(dt_sec, self.config.trend.short_half_life_sec)
            trend_long_alpha = _alpha(dt_sec, self.config.trend.long_half_life_sec)
            momentum_alpha = _alpha(dt_sec, self.config.momentum.half_life_sec)
            dispersion_alpha = _alpha(dt_sec, self.config.dispersion_floor.half_life_sec)
            self._trend_short_ema = (
                (1.0 - trend_short_alpha) * self._trend_short_ema + trend_short_alpha * residual
            )
            self._trend_long_ema = (
                (1.0 - trend_long_alpha) * self._trend_long_ema + trend_long_alpha * residual
            )
            residual_delta = residual - float(self._last_residual or 0.0)
            self._momentum_ema = (
                (1.0 - momentum_alpha) * self._momentum_ema + momentum_alpha * residual_delta
            )
            self._residual_var = (
                (1.0 - dispersion_alpha) * self._residual_var + dispersion_alpha * (residual * residual)
            )

        residual_std = math.sqrt(max(self._residual_var, 0.0))
        dispersion_floor = max(0.0, float(self.config.dispersion_floor.abs_floor))
        dispersion = max(residual_std, dispersion_floor)
        dispersion_floored = bool(dispersion == dispersion_floor and residual_std < dispersion_floor)
        if dispersion <= 0.0:
            dispersion = dispersion_floor if dispersion_floor > 0.0 else 1e-9
            dispersion_floored = True

        trend_raw = (self._trend_short_ema - self._trend_long_ema) / dispersion
        momentum_raw = self._momentum_ema / dispersion
        residual_zscore = residual / dispersion
        mean_reversion_raw = -residual_zscore / max(1.0, float(self.config.mean_reversion.z_clip))

        trend_score = _tanh_score(trend_raw)
        momentum_score = _tanh_score(momentum_raw)
        mean_reversion_score = _tanh_score(mean_reversion_raw)

        self._sample_count += 1
        self._last_as_of_ts_ms = as_of
        self._last_residual = residual
        warmup_remaining = max(0, int(self.config.warmup_updates) - int(self._sample_count))
        valid = warmup_remaining == 0
        invalid_reason = None if valid else "warmup"

        trend_regime = _signed_regime(trend_score, self.config.trend.regime_threshold, "uptrend", "downtrend")
        momentum_regime = _signed_regime(
            momentum_score,
            self.config.momentum.regime_threshold,
            "positive_momentum",
            "negative_momentum",
        )
        reversion_regime = _signed_regime(
            mean_reversion_score,
            self.config.mean_reversion.regime_threshold,
            "cheap_vs_fair",
            "rich_vs_fair",
        )

        composite_regime = "mixed"
        if not valid:
            composite_regime = "warmup"
        elif trend_score >= self.config.trend.regime_threshold and momentum_score >= self.config.momentum.regime_threshold:
            composite_regime = "trend_up"
        elif trend_score <= -self.config.trend.regime_threshold and momentum_score <= -self.config.momentum.regime_threshold:
            composite_regime = "trend_down"
        elif mean_reversion_score >= self.config.mean_reversion.regime_threshold:
            composite_regime = "revert_to_fair_up"
        elif mean_reversion_score <= -self.config.mean_reversion.regime_threshold:
            composite_regime = "revert_to_fair_down"

        self._snapshot = ClassicSignalSnapshot(
            as_of_ts_ms=as_of,
            market_as_of_ts_ms=_maybe_int(market_as_of_ts_ms),
            fair_as_of_ts_ms=_maybe_int(fair_as_of_ts_ms),
            valid=valid,
            invalid_reason=invalid_reason,
            warmup_remaining=warmup_remaining,
            market_anchor=anchor,
            p_fair=None if p_fair is None else float(p_fair),
            residual=residual,
            trend_score=trend_score,
            momentum_score=momentum_score,
            mean_reversion_score=mean_reversion_score,
            residual_zscore=float(residual_zscore),
            trend_regime=trend_regime,
            momentum_regime=momentum_regime,
            reversion_regime=reversion_regime,
            composite_regime=composite_regime,
            dispersion=float(dispersion),
            dispersion_floored=dispersion_floored,
        )
        return self._snapshot

    def _invalid_snapshot(
        self,
        *,
        as_of_ts_ms: int,
        market_as_of_ts_ms: Optional[int],
        fair_as_of_ts_ms: Optional[int],
        market_anchor: Optional[float],
        p_fair: Optional[float],
        invalid_reason: str,
    ) -> ClassicSignalSnapshot:
        return ClassicSignalSnapshot(
            as_of_ts_ms=int(as_of_ts_ms),
            market_as_of_ts_ms=_maybe_int(market_as_of_ts_ms),
            fair_as_of_ts_ms=_maybe_int(fair_as_of_ts_ms),
            valid=False,
            invalid_reason=str(invalid_reason),
            warmup_remaining=max(0, int(self.config.warmup_updates) - int(self._sample_count)),
            market_anchor=None if market_anchor is None else float(market_anchor),
            p_fair=None if p_fair is None else float(p_fair),
            residual=None,
            trend_score=0.0,
            momentum_score=0.0,
            mean_reversion_score=0.0,
            residual_zscore=None,
            trend_regime="unknown",
            momentum_regime="unknown",
            reversion_regime="unknown",
            composite_regime="invalid",
            dispersion=None if self._snapshot.dispersion is None else float(self._snapshot.dispersion),
            dispersion_floored=bool(self._snapshot.dispersion_floored),
        )


def _market_anchor(
    best_bid: Optional[float],
    best_ask: Optional[float],
    best_bid_size: Optional[float],
    best_ask_size: Optional[float],
) -> Optional[float]:
    bid = _maybe_float(best_bid)
    ask = _maybe_float(best_ask)
    if bid is None or ask is None:
        return None
    bid_sz = _maybe_float(best_bid_size)
    ask_sz = _maybe_float(best_ask_size)
    if bid_sz is not None and ask_sz is not None and bid_sz > 0.0 and ask_sz > 0.0:
        denom = bid_sz + ask_sz
        if denom > 0.0:
            return float((ask * bid_sz + bid * ask_sz) / denom)
    return float((bid + ask) / 2.0)


def _maybe_float(value: Optional[object]) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _maybe_int(value: Optional[object]) -> Optional[int]:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _signed_regime(score: float, threshold: float, positive: str, negative: str) -> str:
    thr = max(0.0, float(threshold))
    if score >= thr:
        return positive
    if score <= -thr:
        return negative
    return "neutral"
