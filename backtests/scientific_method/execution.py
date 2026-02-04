from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from core.order_book import OrderBook


@dataclass(frozen=True)
class ExecutionResult:
    vwap_price: Optional[float]
    depth_at_qty: float
    slippage_bps: Optional[float]
    fee_rate: float
    fee_applied_once: bool


def simulate_execution(book: OrderBook, side: str, qty: float, fee_rate: float) -> ExecutionResult:
    if side == "buy":
        vwap = book.vwap_to_fill("buy", qty)
    else:
        vwap = book.vwap_to_fill("sell", qty)
    depth = book.depth_at_qty(side, qty)
    slippage = None
    if vwap is not None:
        slippage = book.expected_slippage_to_fill(side, qty)
    return ExecutionResult(
        vwap_price=vwap,
        depth_at_qty=depth,
        slippage_bps=slippage,
        fee_rate=fee_rate,
        fee_applied_once=True,
    )


def apply_fee(price: float, fee_rate: float, side: str) -> float:
    if side == "buy":
        return price * (1.0 + fee_rate)
    return price * (1.0 - fee_rate)
