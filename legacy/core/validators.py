from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Dict, List, Optional, Tuple

from core.order_book import OrderBook


@dataclass(frozen=True)
class HypotheticalOrder:
    asset_id: str
    side: str
    price: float
    size: float
    t_decision_wall: str
    t_decision_mono_ns: int
    t_decision_event_ts_ms: int


@dataclass(frozen=True)
class OrderConstraints:
    min_tick: float
    min_size: float
    min_price: float
    max_price: float
    max_spread_bps: float
    max_slippage_bps: float
    max_book_staleness_ms: int


@dataclass
class SimBalances:
    usd: float
    tokens: Dict[str, float]
    default_token_balance: float

    def token_balance(self, asset_id: str) -> float:
        return self.tokens.get(asset_id, self.default_token_balance)


def validate_hypothetical_order(
    order: HypotheticalOrder,
    book: OrderBook,
    constraints: OrderConstraints,
    balances: SimBalances,
    now_mono_ns: int,
    execution_mode: str = "TAKER_SIM",
) -> Tuple[bool, List[str], Dict[str, float]]:
    reasons: List[str] = []
    metrics: Dict[str, float] = {}

    side = _normalize_side(order.side)
    if side is None:
        reasons.append("UNKNOWN_SIDE")
        metrics["ok"] = 0.0
        return False, reasons, metrics

    exec_price = book.executable_price(side, order.size)
    diagnostics = book.execution_diagnostics(side, order.size, now_mono_ns)
    metrics["depth_at_qty"] = diagnostics.depth_at_qty
    if diagnostics.spread_bps is not None:
        metrics["spread_bps"] = diagnostics.spread_bps
    if diagnostics.slippage_bps is not None:
        metrics["slippage_bps"] = diagnostics.slippage_bps
    if diagnostics.book_age_ms is not None:
        metrics["book_age_ms"] = diagnostics.book_age_ms
    if exec_price is not None:
        metrics["exec_price"] = exec_price

    if book.best_bid() is None or book.best_ask() is None:
        reasons.append("MARKET_NOT_READY")

    if book.book_is_stale(now_mono_ns, constraints.max_book_staleness_ms):
        reasons.append("BOOK_STALE")

    if execution_mode == "MAKER_LIMIT":
        if order.price < constraints.min_price or order.price > constraints.max_price:
            reasons.append("PRICE_BOUNDS")
    else:
        if exec_price is None:
            reasons.append("NO_EXECUTION_PRICE")
        else:
            if exec_price < constraints.min_price or exec_price > constraints.max_price:
                reasons.append("PRICE_BOUNDS")

    if execution_mode == "MAKER_LIMIT" and constraints.min_tick > 0:
        ticks = order.price / constraints.min_tick
        if abs(ticks - round(ticks)) > 1e-9:
            reasons.append("MIN_TICK")

    if order.size < constraints.min_size:
        reasons.append("MIN_SIZE")

    if side == "buy":
        if exec_price is not None:
            notional = exec_price * order.size
            metrics["notional"] = notional
            if balances.usd < notional:
                reasons.append("INSUFFICIENT_BALANCE")
    else:
        if balances.token_balance(order.asset_id) < order.size:
            reasons.append("INSUFFICIENT_BALANCE")

    if diagnostics.depth_at_qty < order.size:
        reasons.append("DEPTH_TOO_THIN")

    if diagnostics.spread_bps is not None and diagnostics.spread_bps > constraints.max_spread_bps:
        reasons.append("SPREAD_TOO_WIDE")

    if diagnostics.slippage_bps is None or not math.isfinite(diagnostics.slippage_bps):
        reasons.append("SLIPPAGE_TOO_HIGH")
    elif diagnostics.slippage_bps > constraints.max_slippage_bps:
        reasons.append("SLIPPAGE_TOO_HIGH")

    metrics["ok"] = 1.0 if not reasons else 0.0
    ok = not reasons
    return ok, reasons, metrics


def _normalize_side(side: str) -> Optional[str]:
    side_lower = side.lower()
    if side_lower in {"buy", "bid"}:
        return "buy"
    if side_lower in {"sell", "ask"}:
        return "sell"
    return None
