from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Tuple


@dataclass
class OrderSummary:
    order_id: str
    asset_id: Optional[str]
    side: Optional[str]
    status: str
    filled_qty: float
    avg_fill_price: Optional[float]
    fees_paid: float


@dataclass
class OrderStateSnapshot:
    orders: Dict[str, OrderSummary]
    net_position: Dict[str, float]


def rebuild_order_state(events: Iterable[Dict[str, Any]]) -> OrderStateSnapshot:
    orders: Dict[str, OrderSummary] = {}
    net_position: Dict[str, float] = {}
    seen_fills = set()

    for event in events:
        event_type = str(event.get("event_type"))
        order_id = str(event.get("order_id"))
        if not order_id:
            continue
        summary = orders.get(order_id)
        if summary is None:
            summary = OrderSummary(
                order_id=order_id,
                asset_id=event.get("asset_id"),
                side=event.get("side"),
                status="new",
                filled_qty=0.0,
                avg_fill_price=None,
                fees_paid=0.0,
            )
            orders[order_id] = summary

        if event_type == "order_intent":
            summary.asset_id = event.get("asset_id")
            summary.side = event.get("side")
            summary.status = "intent"
            continue
        if event_type == "order_submit":
            summary.status = "submitted"
            continue
        if event_type == "order_ack":
            summary.status = "ack"
            continue
        if event_type == "order_cancel":
            summary.status = "canceled"
            continue
        if event_type == "order_reject":
            summary.status = "rejected"
            continue
        if event_type == "broker_error":
            summary.status = "error"
            continue
        if event_type != "order_fill":
            continue

        dedupe_key = _fill_dedupe_key(event)
        if dedupe_key in seen_fills:
            continue
        seen_fills.add(dedupe_key)

        fill_size = float(event.get("fill_size") or 0.0)
        fill_price = event.get("fill_price")
        try:
            fill_price_val = float(fill_price) if fill_price is not None else None
        except (TypeError, ValueError):
            fill_price_val = None
        fees_bps = float(event.get("fees_bps") or 0.0)

        if fill_size > 0:
            prev_qty = summary.filled_qty
            new_qty = prev_qty + fill_size
            summary.filled_qty = new_qty
            if fill_price_val is not None:
                if summary.avg_fill_price is None:
                    summary.avg_fill_price = fill_price_val
                else:
                    summary.avg_fill_price = (
                        summary.avg_fill_price * prev_qty + fill_price_val * fill_size
                    ) / new_qty
            summary.fees_paid += _fee_paid(fill_price_val, fill_size, fees_bps)
            summary.status = "filled"

            asset = summary.asset_id
            if asset:
                side = summary.side or ""
                sign = 1.0 if side.lower() == "buy" else -1.0 if side.lower() == "sell" else 0.0
                net_position[asset] = net_position.get(asset, 0.0) + sign * fill_size

    return OrderStateSnapshot(orders=orders, net_position=net_position)


def _fee_paid(price: Optional[float], size: float, fee_bps: float) -> float:
    if price is None or size <= 0:
        return 0.0
    return price * size * (fee_bps / 10000.0)


def _fill_dedupe_key(event: Dict[str, Any]) -> Tuple[str, str, str, str]:
    tx_hash = event.get("tx_hash")
    log_index = event.get("log_index")
    if tx_hash is not None and log_index is not None:
        return (str(event.get("order_id")), str(tx_hash), str(log_index), "chain")
    return (
        str(event.get("order_id")),
        str(event.get("t_fill_wall_ms")),
        str(event.get("fill_size")),
        str(event.get("fill_price")),
    )
