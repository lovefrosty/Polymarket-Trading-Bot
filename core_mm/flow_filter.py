from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class FlowFilterDecision:
    volume_ratio: float
    allow_buy: bool
    allow_sell: bool
    reason: str
    # Signed imbalance in basis points: positive = bid-heavy (buy pressure),
    # negative = ask-heavy (sell pressure).  (bid - ask) / mid_depth * 10000.
    imbalance_bps: float = 0.0
    # EWMA-smoothed imbalance (0.0 for stateless evaluate_volume_ratio calls).
    ewma_imbalance_bps: float = 0.0


def evaluate_volume_ratio(
    bid_volume: float,
    ask_volume: float,
    *,
    # Single symmetric threshold: buy is suppressed when ratio < imbalance_threshold,
    # sell is suppressed when ratio > 1/imbalance_threshold.
    # Default 0.7 → suppress_buy below 0.7, suppress_sell above ~1.429.
    imbalance_threshold: float = 0.7,
) -> FlowFilterDecision:
    bid = max(0.0, float(bid_volume))
    ask = max(0.0, float(ask_volume))
    thresh = max(1e-9, min(1.0 - 1e-9, float(imbalance_threshold)))
    upper = 1.0 / thresh

    total = bid + ask
    imbalance_bps = ((bid - ask) / total * 10_000.0) if total > 0.0 else 0.0

    if bid == 0.0 and ask == 0.0:
        return FlowFilterDecision(
            volume_ratio=1.0,
            allow_buy=False,
            allow_sell=False,
            reason="no_near_mid_depth",
            imbalance_bps=0.0,
        )
    if ask == 0.0:
        return FlowFilterDecision(
            volume_ratio=float("inf"),
            allow_buy=True,
            allow_sell=False,
            reason="buy_pressure_only",
            imbalance_bps=10_000.0,
        )
    if bid == 0.0:
        return FlowFilterDecision(
            volume_ratio=0.0,
            allow_buy=False,
            allow_sell=True,
            reason="sell_pressure_only",
            imbalance_bps=-10_000.0,
        )

    ratio = bid / ask
    if ratio < thresh:
        return FlowFilterDecision(
            volume_ratio=ratio,
            allow_buy=False,
            allow_sell=True,
            reason="suppress_buys",
            imbalance_bps=imbalance_bps,
        )
    if ratio > upper:
        return FlowFilterDecision(
            volume_ratio=ratio,
            allow_buy=True,
            allow_sell=False,
            reason="suppress_sells",
            imbalance_bps=imbalance_bps,
        )
    return FlowFilterDecision(
        volume_ratio=ratio,
        allow_buy=True,
        allow_sell=True,
        reason="two_sided",
        imbalance_bps=imbalance_bps,
    )


class FlowFilter:
    """Stateful wrapper around evaluate_volume_ratio() with EWMA smoothing.

    A single noisy snapshot can briefly push the bid/ask ratio outside the
    suppression band and incorrectly block quoting for an entire cycle.
    The EWMA smooths over that noise: the decision is based on the rolling
    average imbalance rather than the instantaneous snapshot.

    EWMA alpha = 2 / (ewma_span + 1).  Default span=10 → alpha≈0.182.
    """

    def __init__(self, imbalance_threshold: float = 0.7, ewma_span: int = 10) -> None:
        self._threshold = float(imbalance_threshold)
        self._alpha = 2.0 / (int(ewma_span) + 1)
        self._ewma_imbalance_bps: Optional[float] = None  # None until first update
        self._prev_ewma_imbalance_bps: Optional[float] = None
        self._emergency_cooldown: int = 0
        self._total_emergency_triggers: int = 0

    def update(self, bid_volume: float, ask_volume: float) -> FlowFilterDecision:
        if self._emergency_cooldown > 0:
            self._emergency_cooldown -= 1

        raw = evaluate_volume_ratio(bid_volume, ask_volume, imbalance_threshold=self._threshold)

        # Save previous EWMA before updating (for reversal detection)
        prev_ewma = self._ewma_imbalance_bps

        # Update EWMA; initialise to raw value on first call (no warm-up lag)
        if self._ewma_imbalance_bps is None:
            self._ewma_imbalance_bps = raw.imbalance_bps
        else:
            self._ewma_imbalance_bps = (
                self._alpha * raw.imbalance_bps + (1.0 - self._alpha) * self._ewma_imbalance_bps
            )

        self._prev_ewma_imbalance_bps = prev_ewma
        ewma_bps = self._ewma_imbalance_bps

        # Convert EWMA bps → bid/ask ratio using the identity:
        #   imbalance_bps = (V_b - V_a) / (V_b + V_a) * 10000
        #   solving: ratio = (10000 + bps) / (10000 - bps)
        if abs(ewma_bps) >= 9999.0:
            ewma_ratio = float("inf") if ewma_bps > 0 else 0.0
        else:
            ewma_ratio = (10_000.0 + ewma_bps) / (10_000.0 - ewma_bps)

        # Zero-depth case: raw result is authoritative — EWMA cannot fix absent liquidity
        if raw.reason == "no_near_mid_depth":
            return FlowFilterDecision(
                volume_ratio=raw.volume_ratio,
                allow_buy=False,
                allow_sell=False,
                reason=raw.reason,
                imbalance_bps=raw.imbalance_bps,
                ewma_imbalance_bps=ewma_bps,
            )

        thresh = max(1e-9, min(1.0 - 1e-9, self._threshold))
        upper = 1.0 / thresh
        if ewma_ratio < thresh:
            allow_buy, allow_sell, reason = False, True, "suppress_buys_ewma"
        elif ewma_ratio > upper:
            allow_buy, allow_sell, reason = True, False, "suppress_sells_ewma"
        else:
            allow_buy, allow_sell, reason = True, True, "two_sided"

        return FlowFilterDecision(
            volume_ratio=ewma_ratio,
            allow_buy=allow_buy,
            allow_sell=allow_sell,
            reason=reason,
            imbalance_bps=raw.imbalance_bps,
            ewma_imbalance_bps=ewma_bps,
        )

    @property
    def in_emergency_cooldown(self) -> bool:
        return self._emergency_cooldown > 0

    def check_reversal(
        self,
        threshold_bps: float = 2000.0,
        cooldown_cycles: int = 4,
        min_magnitude_bps: float = 0.0,
    ) -> bool:
        """Return True and arm cooldown only if:
        - EWMA moved at least threshold_bps in one cycle (momentum), AND
        - the resulting EWMA magnitude is at least min_magnitude_bps (regime gate).

        The regime gate prevents triggering on harmless reversions to neutral
        (e.g. EWMA going from +5000 back to 0) while still catching genuine
        escalating-pressure events (e.g. +3000 → +8000).
        """
        if self._prev_ewma_imbalance_bps is None or self._ewma_imbalance_bps is None:
            return False
        delta = abs(self._ewma_imbalance_bps - self._prev_ewma_imbalance_bps)
        if delta >= float(threshold_bps) and abs(self._ewma_imbalance_bps) >= float(min_magnitude_bps):
            self._emergency_cooldown = int(cooldown_cycles)
            self._total_emergency_triggers += 1
            return True
        return False

    @property
    def total_emergency_triggers(self) -> int:
        return self._total_emergency_triggers
