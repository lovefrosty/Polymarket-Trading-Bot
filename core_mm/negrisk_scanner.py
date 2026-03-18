"""NegRisk multi-outcome arbitrage scanner (monitoring only).

Polymarket neg_risk events have N outcomes whose YES prices should
sum to $1.00.  When sum(best_asks) < $1.00, a long arb exists:
buy all YES tokens → guaranteed $1.00 payout at resolution.

This module DETECTS opportunities and logs them.  It does NOT
execute trades.  Paper monitoring is the first step before wiring
execution (requires NegRisk Adapter contract interaction).
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Dict, List, Optional


@dataclass(frozen=True)
class NegRiskScanConfig:
    """Configuration for NegRisk arb scanner."""

    enabled: bool = False
    # Minimum arb edge (bps) to report as an opportunity.
    min_edge_bps: float = 50.0
    # Minimum number of outcomes to consider (3+ for multi-outcome).
    min_outcomes: int = 3
    # Gamma API URL for fetching events.
    gamma_api_url: str = "https://gamma-api.polymarket.com/events"
    timeout_secs: float = 5.0


@dataclass(frozen=True)
class NegRiskOpportunity:
    """A detected NegRisk arbitrage opportunity."""

    event_id: str
    question: str
    num_outcomes: int
    sum_best_asks: float
    arb_edge_bps: float
    outcome_labels: tuple[str, ...]
    outcome_asks: tuple[float, ...]


class NegRiskScanner:
    """Scans Gamma events for NegRisk arb opportunities."""

    def __init__(self, config: Optional[NegRiskScanConfig] = None) -> None:
        self.config = config or NegRiskScanConfig()
        self._opportunities: List[NegRiskOpportunity] = []
        self._total_scans: int = 0
        self._total_opportunities_found: int = 0

    def scan_events(self, events: List[Dict[str, Any]]) -> List[NegRiskOpportunity]:
        """Scan pre-fetched events for neg_risk arb opportunities.

        Args:
            events: List of Gamma API event objects.  Each event with
                ``negRisk=true`` and a ``markets`` list is evaluated.

        Returns:
            List of opportunities sorted by edge (descending).
        """
        self._total_scans += 1
        opportunities: List[NegRiskOpportunity] = []
        for event in events:
            opp = self._evaluate_event(event)
            if opp is not None:
                opportunities.append(opp)
        opportunities.sort(key=lambda o: -o.arb_edge_bps)
        self._opportunities = opportunities
        self._total_opportunities_found += len(opportunities)
        return opportunities

    def fetch_and_scan(self) -> List[NegRiskOpportunity]:
        """Fetch active neg_risk events from Gamma API and scan.

        Convenience wrapper; raises on network errors.
        """
        from core_mm.market_selector import _fetch_json

        url = (
            f"{self.config.gamma_api_url}"
            f"?active=true&closed=false&limit=100"
        )
        events = _fetch_json(url, self.config.timeout_secs)
        if not isinstance(events, list):
            return []
        # Filter to neg_risk events only
        neg_risk_events = [
            e for e in events
            if isinstance(e, dict) and (e.get("negRisk") or e.get("neg_risk"))
        ]
        return self.scan_events(neg_risk_events)

    def _evaluate_event(self, event: Dict[str, Any]) -> Optional[NegRiskOpportunity]:
        """Evaluate a single event for arb opportunity."""
        if not isinstance(event, dict):
            return None
        is_neg_risk = event.get("negRisk") or event.get("neg_risk")
        if not is_neg_risk:
            return None

        markets = event.get("markets")
        if not isinstance(markets, list):
            return None
        if len(markets) < self.config.min_outcomes:
            return None

        outcome_labels: List[str] = []
        outcome_asks: List[float] = []

        for market in markets:
            if not isinstance(market, dict):
                continue
            # Skip closed or inactive markets
            if market.get("closed"):
                continue
            if market.get("active") is False:
                continue

            label = str(
                market.get("groupItemTitle")
                or market.get("question")
                or market.get("title")
                or ""
            )
            best_ask = _extract_yes_ask(market)
            if best_ask is None or best_ask <= 0:
                continue

            outcome_labels.append(label)
            outcome_asks.append(best_ask)

        if len(outcome_asks) < self.config.min_outcomes:
            return None

        sum_asks = sum(outcome_asks)
        arb_edge_bps = (1.0 - sum_asks) * 10_000.0

        if arb_edge_bps < self.config.min_edge_bps:
            return None

        return NegRiskOpportunity(
            event_id=str(event.get("id") or event.get("conditionId") or ""),
            question=str(event.get("title") or event.get("question") or ""),
            num_outcomes=len(outcome_asks),
            sum_best_asks=sum_asks,
            arb_edge_bps=arb_edge_bps,
            outcome_labels=tuple(outcome_labels),
            outcome_asks=tuple(outcome_asks),
        )

    @property
    def latest_opportunities(self) -> List[NegRiskOpportunity]:
        return list(self._opportunities)

    @property
    def stats(self) -> Dict[str, int]:
        return {
            "total_scans": self._total_scans,
            "total_opportunities_found": self._total_opportunities_found,
            "current_opportunities": len(self._opportunities),
        }


def _extract_yes_ask(market: Dict[str, Any]) -> Optional[float]:
    """Extract the YES-side best ask from a Gamma market object."""
    # Direct best ask field
    for key in ("bestAsk", "best_ask"):
        val = market.get(key)
        if val is not None:
            try:
                return float(val)
            except (TypeError, ValueError):
                pass

    # outcomePrices: JSON string "[0.52, 0.48]" where first is YES
    prices = market.get("outcomePrices")
    if isinstance(prices, str):
        try:
            prices = json.loads(prices)
        except json.JSONDecodeError:
            return None
    if isinstance(prices, list) and prices:
        try:
            return float(prices[0])
        except (TypeError, ValueError, IndexError):
            return None

    return None
