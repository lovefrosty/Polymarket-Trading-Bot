"""Signal 6: Spot price momentum for informed market making.

Tracks short-term rate of change in a reference price (e.g., BTC spot
from Binance).  When momentum is positive, lean toward buying; when
negative, lean toward selling.

This is a simplified Cartea & Wang-style optimal quoting adjustment:
directional flow information → quote skew.

The signal is agnostic to the price source.  Feed it mid-prices from
any exchange WebSocket, REST poll, or the Polymarket book itself.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import Deque, Optional


@dataclass(frozen=True)
class MomentumSignal:
    """Output of spot momentum computation."""

    # Extra skew ticks (positive = price rising → lean toward buying).
    extra_skew_ticks: int = 0
    # Rate of change in bps (first → last in window).
    momentum_bps: float = 0.0
    # Number of samples in window.
    samples: int = 0


class SpotMomentum:
    """Short-term price momentum → directional skew ticks.

    Parameters:
        window: Number of price samples to track.
        max_skew_ticks: Maximum skew ticks output.
        activation_bps: Minimum momentum (bps) to produce signal.
        full_scale_bps: Momentum (bps) at which max skew is reached.
    """

    def __init__(
        self,
        window: int = 20,
        max_skew_ticks: int = 2,
        activation_bps: float = 20.0,
        full_scale_bps: float = 100.0,
    ) -> None:
        self._prices: Deque[float] = deque(maxlen=max(3, int(window)))
        self._max_skew = max(1, int(max_skew_ticks))
        self._activation_bps = float(activation_bps)
        self._full_scale_bps = max(float(activation_bps) + 1.0, float(full_scale_bps))

    def update(self, price: float) -> MomentumSignal:
        """Record a new price observation and return the momentum signal."""
        if price <= 0:
            return MomentumSignal(samples=len(self._prices))
        self._prices.append(float(price))
        if len(self._prices) < 3:
            return MomentumSignal(samples=len(self._prices))

        # Rate of change from oldest to newest in window
        first = self._prices[0]
        last = self._prices[-1]
        if first <= 0:
            return MomentumSignal(samples=len(self._prices))

        momentum_bps = (last - first) / first * 10_000.0

        if abs(momentum_bps) < self._activation_bps:
            return MomentumSignal(momentum_bps=momentum_bps, samples=len(self._prices))

        # Linear map from activation..full_scale → 1..max_skew
        excess = abs(momentum_bps) - self._activation_bps
        scale_range = self._full_scale_bps - self._activation_bps
        normalized = min(1.0, excess / scale_range)
        ticks = max(1, round(normalized * self._max_skew))

        return MomentumSignal(
            extra_skew_ticks=ticks if momentum_bps > 0 else -ticks,
            momentum_bps=momentum_bps,
            samples=len(self._prices),
        )

    @property
    def current_momentum_bps(self) -> float:
        if len(self._prices) < 3:
            return 0.0
        first = self._prices[0]
        last = self._prices[-1]
        if first <= 0:
            return 0.0
        return (last - first) / first * 10_000.0

    def reset(self) -> None:
        """Clear the price window."""
        self._prices.clear()
