from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional


@dataclass(frozen=True)
class DesiredQuote:
    quote_key: str
    token_id: str
    side: str
    price: float
    size: float
    metadata: Optional[Dict[str, Any]] = None


@dataclass(frozen=True)
class RestingOrder:
    quote_key: str
    order_id: str
    token_id: str
    side: str
    price: float
    size: float
    placed_at_ms: int


@dataclass(frozen=True)
class OrderAction:
    action: str
    quote_key: str
    existing_order_id: Optional[str] = None
    desired_quote: Optional[DesiredQuote] = None
    reason: Optional[str] = None


class SmartOrderManager:
    def __init__(
        self,
        *,
        price_change_threshold: float = 0.005,
        size_change_threshold: float = 0.10,
        stale_after_ms: int = 300_000,
    ) -> None:
        self._price_change_threshold = float(price_change_threshold)
        self._size_change_threshold = float(size_change_threshold)
        self._stale_after_ms = int(stale_after_ms)

    def decide_one(
        self,
        quote_key: str,
        *,
        desired_quote: Optional[DesiredQuote],
        existing: Optional[RestingOrder],
        now_ms: int,
    ) -> OrderAction:
        if desired_quote is None or desired_quote.size <= 0:
            if existing is not None:
                return OrderAction("CANCEL", quote_key, existing_order_id=existing.order_id, reason="desired_empty")
            return OrderAction("NOOP", quote_key, reason="no_desired_or_existing")

        if existing is None:
            return OrderAction("PLACE", quote_key, desired_quote=desired_quote, reason="no_existing")

        if int(now_ms) - int(existing.placed_at_ms) > self._stale_after_ms:
            return OrderAction(
                "CANCEL_AND_REPLACE",
                quote_key,
                existing_order_id=existing.order_id,
                desired_quote=desired_quote,
                reason="stale_existing",
            )

        price_diff = abs(float(existing.price) - float(desired_quote.price))
        size_diff_ratio = _size_diff_ratio(float(existing.size), float(desired_quote.size))
        if price_diff > self._price_change_threshold or size_diff_ratio > self._size_change_threshold:
            return OrderAction(
                "CANCEL_AND_REPLACE",
                quote_key,
                existing_order_id=existing.order_id,
                desired_quote=desired_quote,
                reason="material_change",
            )
        return OrderAction("NOOP", quote_key, existing_order_id=existing.order_id, desired_quote=desired_quote, reason="within_threshold")

    def plan(
        self,
        *,
        desired_quotes: Dict[str, DesiredQuote],
        existing_orders: Dict[str, RestingOrder],
        now_ms: int,
    ) -> List[OrderAction]:
        actions: List[OrderAction] = []
        all_keys = sorted(set(desired_quotes.keys()) | set(existing_orders.keys()))
        for quote_key in all_keys:
            actions.append(
                self.decide_one(
                    quote_key,
                    desired_quote=desired_quotes.get(quote_key),
                    existing=existing_orders.get(quote_key),
                    now_ms=now_ms,
                )
            )
        return actions


def _size_diff_ratio(existing_size: float, desired_size: float) -> float:
    if existing_size <= 0:
        return 1.0 if desired_size > 0 else 0.0
    return abs(existing_size - desired_size) / existing_size
