"""Sum-to-one complement arbitrage scanner for binary Polymarket markets.

YES + NO tokens always merge to $1.00.  When the combined bid/ask
spreads create an edge above fees, profitable arb exists:

- **Maker buy arb** (primary): Place passive bids on BOTH tokens.
  When both fill → merge for $1.  Edge = $1 - YES_bid - NO_bid.
  Always positive; module boosts size when edge is large.

- **Taker buy arb** (rare): Cross both asks → merge for $1.
  Edge = $1 - (YES_ask + NO_ask) × (1 + fee%).
  Only profitable when combined ask < $1 after fees.

When maker arb edge exceeds threshold, the scanner returns a
size_multiplier > 1.0 that the runner applies to trade_size.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional


@dataclass(frozen=True)
class ComplementArbConfig:
    """Configuration for complement arb scanner."""

    enabled: bool = False
    # Minimum maker edge (bps) to trigger size boost.
    min_maker_edge_bps: float = 100.0
    # Minimum taker edge (bps, after fees) to flag taker opportunity.
    min_taker_edge_bps: float = 30.0
    # Fee per side in bps (for taker edge calculation).
    fee_bps: float = 25.0
    # Trade-size multiplier when maker arb is active.
    maker_size_multiplier: float = 2.0


@dataclass(frozen=True)
class ComplementArbSignal:
    """Result of a single complement arb evaluation."""

    # Maker edge: $1 - YES_bid - NO_bid (in bps).
    maker_edge_bps: float = 0.0
    # Taker edge: $1 - (YES_ask + NO_ask) × (1 + fee%) (in bps).
    taker_edge_bps: float = 0.0
    # Whether maker edge exceeds threshold.
    maker_arb_active: bool = False
    # Whether taker edge exceeds threshold.
    taker_arb_active: bool = False
    # Sizing multiplier for this cycle (1.0 = no change).
    size_multiplier: float = 1.0
    # Diagnostic fields.
    complement_sum_bid: float = 0.0
    complement_sum_ask: float = 0.0


class ComplementArbScanner:
    """Evaluates complement arb opportunities per cycle."""

    def __init__(self, config: Optional[ComplementArbConfig] = None) -> None:
        self.config = config or ComplementArbConfig()
        self._total_maker_signals: int = 0
        self._total_taker_signals: int = 0
        self._total_evaluations: int = 0

    def evaluate(
        self,
        *,
        yes_bid: Optional[float],
        yes_ask: Optional[float],
        no_bid: Optional[float],
        no_ask: Optional[float],
    ) -> ComplementArbSignal:
        """Evaluate complement arb given both token BBOs."""
        if not self.config.enabled:
            return ComplementArbSignal()

        if yes_bid is None or yes_ask is None or no_bid is None or no_ask is None:
            return ComplementArbSignal()
        if yes_bid <= 0 or yes_ask <= 0 or no_bid <= 0 or no_ask <= 0:
            return ComplementArbSignal()

        self._total_evaluations += 1
        sum_bid = float(yes_bid) + float(no_bid)
        sum_ask = float(yes_ask) + float(no_ask)

        # Maker edge: accumulate both via passive bids, merge for $1.
        maker_edge_bps = (1.0 - sum_bid) * 10_000.0

        # Taker edge: cross both asks, merge for $1 (each side pays fee).
        fee_mult = 1.0 + self.config.fee_bps / 10_000.0
        taker_cost = sum_ask * fee_mult
        taker_edge_bps = (1.0 - taker_cost) * 10_000.0

        maker_active = maker_edge_bps >= self.config.min_maker_edge_bps
        taker_active = taker_edge_bps >= self.config.min_taker_edge_bps

        size_mult = 1.0
        if maker_active:
            size_mult = self.config.maker_size_multiplier
            self._total_maker_signals += 1
        if taker_active:
            self._total_taker_signals += 1

        return ComplementArbSignal(
            maker_edge_bps=maker_edge_bps,
            taker_edge_bps=taker_edge_bps,
            maker_arb_active=maker_active,
            taker_arb_active=taker_active,
            size_multiplier=size_mult,
            complement_sum_bid=sum_bid,
            complement_sum_ask=sum_ask,
        )

    @property
    def stats(self) -> Dict[str, int]:
        return {
            "total_evaluations": self._total_evaluations,
            "total_maker_signals": self._total_maker_signals,
            "total_taker_signals": self._total_taker_signals,
        }
