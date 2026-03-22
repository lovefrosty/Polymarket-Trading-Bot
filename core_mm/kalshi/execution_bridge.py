"""Kalshi execution bridge — adapts KalshiClient to ExecutionAdapter interface.

ExecutionAdapter expects a client with:
    create_order(order_args) → prepared_order
    post_order(prepared_order, tif) → response dict
    cancel(order_id=...) → response dict
    cancel_all() → response dict
    get_orders() → list of order dicts
    get_positions() → list of position dicts

KalshiClient already implements all of these, plus it uses
KalshiOrderArgs (compatible with the OrderArgs duck-type that
ExecutionAdapter passes to create_order).

This module also provides helper functions for translating between
our virtual token ID scheme and Kalshi's ticker-based scheme for
order status and fill reporting.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from core_mm.kalshi.client import KalshiClient, _parse_virtual_token


def normalize_kalshi_order(order: Dict[str, Any]) -> Dict[str, Any]:
    """Convert a Kalshi order response to the format PaperBroker/LiveBroker expects.

    Kalshi returns:
        {"order_id": "...", "ticker": "...", "action": "buy"|"sell",
         "side": "yes", "yes_price": 55, "remaining_count": 10, ...}

    We convert to:
        {"order_id": "...", "token_id": "{ticker}:yes", "side": "buy"|"sell",
         "price": 0.55, "size": 10, ...}
    """
    ticker = str(order.get("ticker") or "")
    action = str(order.get("action") or "").lower()
    yes_price = float(order.get("yes_price") or order.get("yes_price_dollars") or 0)
    # Normalize price to dollars
    price_dollars = yes_price / 100.0 if yes_price > 1.0 else yes_price

    # Map action back to our convention:
    # action="buy" → buying YES → token={ticker}:yes, side=buy
    # action="sell" → selling YES (= buying NO) → token={ticker}:yes, side=sell
    if action == "buy":
        token_id = f"{ticker}:yes"
        side = "buy"
    else:
        token_id = f"{ticker}:yes"
        side = "sell"

    size = float(
        order.get("remaining_count")
        or order.get("count")
        or order.get("size")
        or 0
    )

    return {
        "order_id": str(order.get("order_id") or order.get("id") or ""),
        "orderID": str(order.get("order_id") or order.get("id") or ""),
        "token_id": token_id,
        "side": side,
        "price": price_dollars,
        "size": size,
        "placed_at_ms": _parse_placed_at(order),
        "raw_kalshi": order,
    }


def normalize_kalshi_fill(fill: Dict[str, Any]) -> Dict[str, Any]:
    """Convert a Kalshi fill to the format UserFeedState/PaperBroker expects.

    Kalshi fill:
        {"trade_id": "...", "ticker": "...", "action": "buy"|"sell",
         "side": "yes", "yes_price": 55, "count": 5, "created_time": "..."}

    We convert to UserEvent-compatible dict:
        {"event_type": "trade", "order_id": "...", "token_id": "{ticker}:yes",
         "side": "buy"|"sell", "price": 0.55, "size": 5}
    """
    ticker = str(fill.get("ticker") or "")
    action = str(fill.get("action") or "").lower()
    yes_price = float(fill.get("yes_price") or fill.get("yes_price_dollars") or 0)
    price_dollars = yes_price / 100.0 if yes_price > 1.0 else yes_price

    if action == "buy":
        token_id = f"{ticker}:yes"
        side = "buy"
    else:
        token_id = f"{ticker}:yes"
        side = "sell"

    size = float(fill.get("count") or fill.get("size") or 0)

    return {
        "event_type": "trade",
        "type": "trade",
        "trade_id": str(fill.get("trade_id") or fill.get("id") or ""),
        "order_id": str(fill.get("order_id") or ""),
        "token_id": token_id,
        "asset_id": token_id,
        "side": side,
        "price": price_dollars,
        "size": size,
        "raw_kalshi": fill,
    }


def _parse_placed_at(order: Dict[str, Any]) -> int:
    """Extract placement timestamp in ms from Kalshi order."""
    for key in ("created_time", "placed_time", "updated_time"):
        val = order.get(key)
        if val is None:
            continue
        if isinstance(val, (int, float)):
            v = float(val)
            return int(v * 1000) if v < 1e12 else int(v)
        try:
            from datetime import datetime
            text = str(val).replace("Z", "+00:00")
            dt = datetime.fromisoformat(text)
            return int(dt.timestamp() * 1000)
        except Exception:
            continue
    return 0
