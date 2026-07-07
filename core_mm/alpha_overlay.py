"""Epic 3: Simple Alpha Overlays.

Three signals that adjust quoting behaviour without adding new data sources:

1. **Book Imbalance Alpha** — EWMA-smoothed bid/ask depth ratio → extra inventory
   skew ticks.  When the book is heavily bid-heavy, lean asks down to capture
   more spread on the sell side (and vice versa).

2. **Fill Asymmetry** — tracks ratio of adverse fills (fills that move against us)
   versus favourable fills.  High adverse ratio → widen spread to compensate for
   information leakage.

3. **Volatility Regime** — exponential-window realized volatility of mid-price
   changes.  Low vol → tighten spread; high vol → widen spread.

All three produce a single `AlphaSignal` dataclass consumed by main_loop to
adjust `inventory_skew_ticks` and `spread_multiplier`.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import Deque, Dict, Optional

from core_mm.spot_momentum import SpotMomentum


@dataclass(frozen=True)
class AlphaSignal:
    """Combined output of all overlay signals."""

    # Additional inventory skew ticks (positive = lean toward selling).
    extra_skew_ticks: int = 0

    # Spread multiplier adjustment (1.0 = no change, >1.0 = wider).
    spread_multiplier: float = 1.0

    # Diagnostic fields for telemetry.
    imbalance_alpha_bps: float = 0.0
    fill_adversity_ratio: float = 0.0
    vol_regime: str = "normal"
    realized_vol_bps: float = 0.0

    # Complement arbitrage signal (prediction-market specific).
    complement_skew_bps: float = 0.0

    # Depth ratio change signal.
    depth_change_signal: float = 0.0

    # Spot momentum signal (bps).
    spot_momentum_bps: float = 0.0


# ── Signal 1: Book Imbalance Alpha ──────────────────────────────────────────


class BookImbalanceAlpha:
    """Time-weighted EWMA of bid/ask depth imbalance → skew ticks.

    Imbalance = (bid_depth - ask_depth) / (bid_depth + ask_depth)
    Range: [-1, +1] → mapped to extra skew ticks.

    Positive imbalance (bid-heavy) = buying pressure → lean asks down (positive skew).
    Negative imbalance (ask-heavy) = selling pressure → lean bids up (negative skew).
    """

    def __init__(
        self,
        ewma_span: int = 20,
        max_extra_ticks: int = 2,
        activation_threshold: float = 0.25,
    ) -> None:
        self._alpha = 2.0 / (int(ewma_span) + 1)
        self._max_ticks = int(max_extra_ticks)
        self._threshold = float(activation_threshold)
        self._ewma: Optional[float] = None

    def update(self, bid_depth: float, ask_depth: float) -> int:
        """Update with current depths, return extra skew ticks."""
        total = float(bid_depth) + float(ask_depth)
        if total <= 0:
            return 0

        raw = (float(bid_depth) - float(ask_depth)) / total
        if self._ewma is None:
            self._ewma = raw
        else:
            self._ewma = self._alpha * raw + (1.0 - self._alpha) * self._ewma

        if abs(self._ewma) < self._threshold:
            return 0

        # Linear mapping from threshold..1.0 → 1..max_ticks
        magnitude = (abs(self._ewma) - self._threshold) / (1.0 - self._threshold)
        ticks = max(1, min(self._max_ticks, round(magnitude * self._max_ticks)))
        return ticks if self._ewma > 0 else -ticks

    @property
    def current_imbalance(self) -> float:
        return self._ewma if self._ewma is not None else 0.0


# ── Signal 2: Fill Asymmetry ────────────────────────────────────────────────


@dataclass(frozen=True)
class FillRecord:
    """Minimal fill info for adversity tracking."""

    side: str  # "buy" or "sell"
    price: float
    mid_at_fill: float
    ts_ms: int


class FillAsymmetry:
    """Tracks fill quality to detect adverse selection.

    A fill is "adverse" if:
    - Buy fill and price > mid (we paid above mid)
    - Sell fill and price < mid (we sold below mid)

    High adverse ratio → information leakage → widen spread.
    """

    def __init__(
        self,
        window_size: int = 50,
        widen_threshold: float = 0.65,
        widen_multiplier: float = 1.3,
    ) -> None:
        self._window: Deque[bool] = deque(maxlen=int(window_size))
        self._widen_threshold = float(widen_threshold)
        self._widen_multiplier = float(widen_multiplier)

    def record_fill(self, side: str, price: float, mid_at_fill: float) -> None:
        """Record whether a fill was adverse."""
        if mid_at_fill <= 0:
            return
        if str(side).lower() == "buy":
            adverse = float(price) > float(mid_at_fill)
        else:
            adverse = float(price) < float(mid_at_fill)
        self._window.append(adverse)

    @property
    def adversity_ratio(self) -> float:
        if len(self._window) < 5:
            return 0.0
        return sum(1 for a in self._window if a) / len(self._window)

    @property
    def spread_multiplier(self) -> float:
        ratio = self.adversity_ratio
        if ratio >= self._widen_threshold:
            return self._widen_multiplier
        return 1.0


# ── Signal 3: Volatility Regime ─────────────────────────────────────────────


class VolatilityRegime:
    """Realized volatility of mid-price changes → spread multiplier.

    Tracks recent mid-price moves (in bps), computes rolling stdev.

    Regime thresholds (configurable):
    - low:    stdev < 30 bps  → tighten spread (0.8x)
    - normal: 30-100 bps      → no change (1.0x)
    - high:   > 100 bps       → widen spread (1.5x)
    """

    def __init__(
        self,
        window_size: int = 30,
        low_vol_bps: float = 30.0,
        high_vol_bps: float = 100.0,
        low_vol_multiplier: float = 0.8,
        high_vol_multiplier: float = 1.5,
    ) -> None:
        self._moves: Deque[float] = deque(maxlen=int(window_size))
        self._last_mid: Optional[float] = None
        self._low_bps = float(low_vol_bps)
        self._high_bps = float(high_vol_bps)
        self._low_mult = float(low_vol_multiplier)
        self._high_mult = float(high_vol_multiplier)

    def update(self, mid_price: float) -> None:
        """Record a new mid-price observation."""
        if self._last_mid is not None and self._last_mid > 0 and float(mid_price) > 0:
            move_bps = abs(float(mid_price) - self._last_mid) / self._last_mid * 10_000.0
            self._moves.append(move_bps)
        self._last_mid = float(mid_price)

    @property
    def has_enough_data(self) -> bool:
        return len(self._moves) >= 5

    @property
    def realized_vol_bps(self) -> float:
        """Rolling standard deviation of mid-price moves in bps."""
        if len(self._moves) < 3:
            return 0.0
        mean = sum(self._moves) / len(self._moves)
        variance = sum((m - mean) ** 2 for m in self._moves) / len(self._moves)
        return variance ** 0.5

    @property
    def regime(self) -> str:
        if not self.has_enough_data:
            return "normal"  # Not enough data → don't adjust
        vol = self.realized_vol_bps
        if vol < self._low_bps:
            return "low"
        if vol > self._high_bps:
            return "high"
        return "normal"

    @property
    def spread_multiplier(self) -> float:
        regime = self.regime
        if regime == "low":
            return self._low_mult
        if regime == "high":
            return self._high_mult
        return 1.0


# ── Signal 4: Complement Arbitrage ─────────────────────────────────────────


class ComplementArbitrage:
    """Prediction-market specific: YES + NO mid should sum to ~$1.00.

    When the sum deviates from 1.0, one side is overpriced relative to the
    other.  This creates a directional skew signal unique to binary markets.

    complement_sum > 1.0 → overpriced → lean toward selling (positive skew)
    complement_sum < 1.0 → underpriced → lean toward buying (negative skew)

    The signal is the deviation in bps: (sum - 1.0) * 10000.
    """

    def __init__(self, dead_zone_bps: float = 50.0, max_skew_ticks: int = 1) -> None:
        self._dead_zone_bps = float(dead_zone_bps)
        self._max_ticks = int(max_skew_ticks)
        self._last_skew_bps: float = 0.0

    def update(self, yes_mid: float, no_mid: float) -> int:
        """Update with both token mids, return extra skew ticks."""
        if yes_mid <= 0 or no_mid <= 0:
            self._last_skew_bps = 0.0
            return 0
        complement_sum = float(yes_mid) + float(no_mid)
        self._last_skew_bps = (complement_sum - 1.0) * 10_000.0

        if abs(self._last_skew_bps) < self._dead_zone_bps:
            return 0

        # Map dead_zone..500 bps → 1..max_ticks
        magnitude = min(500.0, abs(self._last_skew_bps) - self._dead_zone_bps)
        raw_ticks = max(1, round((magnitude / 500.0) * self._max_ticks))
        return raw_ticks if self._last_skew_bps > 0 else -raw_ticks

    @property
    def skew_bps(self) -> float:
        return self._last_skew_bps


# ── Signal 5: Depth Ratio Change ──────────────────────────────────────────


class DepthRatioChange:
    """Detects sudden changes in bid/ask depth as a directional signal.

    A large increase in bid depth (new bid wall) suggests buying pressure;
    a large increase in ask depth suggests selling pressure.  The signal
    is the normalized delta: (bid_delta - ask_delta) / (|bid_delta| + |ask_delta|).

    Range: [-1, +1].  Positive = bid depth grew more → buy pressure.
    """

    def __init__(self, min_delta: float = 50.0) -> None:
        self._min_delta = float(min_delta)
        self._prev_bid_depth: Optional[float] = None
        self._prev_ask_depth: Optional[float] = None
        self._signal: float = 0.0

    def update(self, bid_depth: float, ask_depth: float) -> float:
        """Update with current depths, return signal in [-1, +1]."""
        if self._prev_bid_depth is None:
            self._prev_bid_depth = float(bid_depth)
            self._prev_ask_depth = float(ask_depth)
            self._signal = 0.0
            return 0.0

        bid_delta = float(bid_depth) - self._prev_bid_depth
        ask_delta = float(ask_depth) - (self._prev_ask_depth or 0.0)
        self._prev_bid_depth = float(bid_depth)
        self._prev_ask_depth = float(ask_depth)

        total = abs(bid_delta) + abs(ask_delta)
        if total < self._min_delta:
            self._signal = 0.0
            return 0.0

        self._signal = (bid_delta - ask_delta) / total
        return self._signal

    @property
    def signal(self) -> float:
        return self._signal


# ── Combined Alpha Overlay Manager ──────────────────────────────────────────


class AlphaOverlayManager:
    """Per-token manager that combines all alpha signals.

    Usage in main_loop._evaluate_token():
        overlay = self._get_alpha_overlay(token_id)
        overlay.update_book(bid_depth, ask_depth)
        overlay.update_mid(mid_price)
        signal = overlay.get_signal()
        # Apply: inventory_skew_ticks += signal.extra_skew_ticks
        #        spread_multiplier *= signal.spread_multiplier
    """

    def __init__(
        self,
        imbalance_ewma_span: int = 20,
        imbalance_max_ticks: int = 2,
        imbalance_threshold: float = 0.25,
        fill_window: int = 50,
        fill_widen_threshold: float = 0.65,
        fill_widen_multiplier: float = 1.3,
        vol_window: int = 30,
        vol_low_bps: float = 30.0,
        vol_high_bps: float = 100.0,
        vol_low_mult: float = 0.8,
        vol_high_mult: float = 1.5,
        complement_dead_zone_bps: float = 50.0,
        complement_max_ticks: int = 1,
        depth_min_delta: float = 50.0,
        momentum_window: int = 20,
        momentum_max_ticks: int = 2,
        momentum_activation_bps: float = 20.0,
        momentum_full_scale_bps: float = 100.0,
    ) -> None:
        self.imbalance = BookImbalanceAlpha(
            ewma_span=imbalance_ewma_span,
            max_extra_ticks=imbalance_max_ticks,
            activation_threshold=imbalance_threshold,
        )
        self.fill_asymmetry = FillAsymmetry(
            window_size=fill_window,
            widen_threshold=fill_widen_threshold,
            widen_multiplier=fill_widen_multiplier,
        )
        self.volatility = VolatilityRegime(
            window_size=vol_window,
            low_vol_bps=vol_low_bps,
            high_vol_bps=vol_high_bps,
            low_vol_multiplier=vol_low_mult,
            high_vol_multiplier=vol_high_mult,
        )
        self.complement = ComplementArbitrage(
            dead_zone_bps=complement_dead_zone_bps,
            max_skew_ticks=complement_max_ticks,
        )
        self.depth_change = DepthRatioChange(
            min_delta=depth_min_delta,
        )
        self.momentum = SpotMomentum(
            window=momentum_window,
            max_skew_ticks=momentum_max_ticks,
            activation_bps=momentum_activation_bps,
            full_scale_bps=momentum_full_scale_bps,
        )

    def update_book(self, bid_depth: float, ask_depth: float) -> None:
        """Call each cycle with current book depths."""
        self.imbalance.update(bid_depth, ask_depth)
        self.depth_change.update(bid_depth, ask_depth)

    def update_mid(self, mid_price: float) -> None:
        """Call each cycle with current mid-price."""
        self.volatility.update(mid_price)

    def update_complement(self, yes_mid: float, no_mid: float) -> None:
        """Call each cycle with both token mids for complement arb signal."""
        self.complement.update(yes_mid, no_mid)

    def update_spot(self, spot_price: float) -> None:
        """Call each cycle with a reference spot price (e.g. Binance BTC)."""
        self.momentum.update(spot_price)

    def record_fill(self, side: str, price: float, mid_at_fill: float) -> None:
        """Call when a fill occurs."""
        self.fill_asymmetry.record_fill(side, price, mid_at_fill)

    def get_signal(self) -> AlphaSignal:
        """Combine all signals into a single AlphaSignal."""
        # Read the last computed extra ticks from imbalance
        imb = self.imbalance.current_imbalance
        threshold = self.imbalance._threshold
        max_ticks = self.imbalance._max_ticks

        if abs(imb) < threshold:
            extra_ticks = 0
        else:
            magnitude = (abs(imb) - threshold) / (1.0 - threshold)
            extra_ticks = max(1, min(max_ticks, round(magnitude * max_ticks)))
            if imb < 0:
                extra_ticks = -extra_ticks

        # Add complement arbitrage skew (reads cached value from last update_complement)
        complement_ticks = 0
        comp_skew = self.complement.skew_bps
        if abs(comp_skew) >= self.complement._dead_zone_bps:
            mag = min(500.0, abs(comp_skew) - self.complement._dead_zone_bps)
            complement_ticks = max(1, round((mag / 500.0) * self.complement._max_ticks))
            if comp_skew < 0:
                complement_ticks = -complement_ticks
        extra_ticks += complement_ticks

        # Add spot momentum skew (reads state from last update_spot call)
        momentum_bps = self.momentum.current_momentum_bps
        if abs(momentum_bps) >= self.momentum._activation_bps:
            excess = abs(momentum_bps) - self.momentum._activation_bps
            scale_range = self.momentum._full_scale_bps - self.momentum._activation_bps
            normalized = min(1.0, excess / scale_range) if scale_range > 0 else 0.0
            m_ticks = max(1, round(normalized * self.momentum._max_skew))
            extra_ticks += m_ticks if momentum_bps > 0 else -m_ticks

        fill_mult = self.fill_asymmetry.spread_multiplier
        vol_mult = self.volatility.spread_multiplier
        combined_mult = fill_mult * vol_mult

        return AlphaSignal(
            extra_skew_ticks=extra_ticks,
            spread_multiplier=combined_mult,
            imbalance_alpha_bps=imb * 10_000.0,
            fill_adversity_ratio=self.fill_asymmetry.adversity_ratio,
            vol_regime=self.volatility.regime,
            realized_vol_bps=self.volatility.realized_vol_bps,
            complement_skew_bps=self.complement.skew_bps,
            depth_change_signal=self.depth_change.signal,
            spot_momentum_bps=momentum_bps,
        )
