from __future__ import annotations

from dataclasses import dataclass
import time
from typing import Any, Dict, List, Optional

from core_mm.book_manager import BookManager
from core_mm.execution import ExecutionResult
from core_mm.positions import PositionTracker


@dataclass(frozen=True)
class PaperOrder:
    order_id: str
    token_id: str
    side: str
    price: float
    size: float
    placed_at_ms: int
    client_order_id: Optional[str] = None
    quote_group_id: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None
    status: str = "open"


class PaperBroker:
    def __init__(
        self,
        *,
        book_manager: BookManager,
        position_tracker: Optional[PositionTracker] = None,
        fee_bps: float = 25.0,
        fee_mode: str = "taker",
    ) -> None:
        self._book_manager = book_manager
        self._positions = position_tracker or PositionTracker()
        self._orders: Dict[str, PaperOrder] = {}
        self._fills: List[Dict[str, Any]] = []
        self._fill_cursor = 0
        self._next_id = 1
        self._fee_bps = float(fee_bps)
        self._fee_mode = str(fee_mode).lower()
        self._stats: Dict[str, float] = {
            "realized_gross_pnl": 0.0,
            "realized_net_pnl": 0.0,
            "cumulative_fees": 0.0,
            "turnover": 0.0,
            "win_count": 0.0,
            "loss_count": 0.0,
        }

    @property
    def position_tracker(self) -> PositionTracker:
        return self._positions

    def place_order(
        self,
        *,
        token_id: str,
        side: str,
        price: float,
        size: float,
        client_order_id: Optional[str] = None,
        quote_group_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        neg_risk: bool = False,
        time_in_force: Optional[str] = None,
    ) -> ExecutionResult:
        order_id = f"paper-{self._next_id}"
        self._next_id += 1
        placed_at_ms = _now_ms()
        order = PaperOrder(
            order_id=order_id,
            token_id=str(token_id),
            side=str(side).lower(),
            price=float(price),
            size=float(size),
            placed_at_ms=placed_at_ms,
            client_order_id=client_order_id,
            quote_group_id=quote_group_id,
            metadata=dict(metadata or {}),
        )
        self._orders[order_id] = order
        fill = self._try_fill(order)
        payload: Dict[str, Any] = {
            "orderID": order_id,
            "status": order.status,
            "neg_risk": bool(neg_risk),
            "client_order_id": client_order_id,
            "quote_group_id": quote_group_id,
        }
        if fill is not None:
            payload["fill"] = fill
        return ExecutionResult(True, payload)

    def cancel_order(self, order_id: str) -> ExecutionResult:
        order = self._orders.get(str(order_id))
        if order is None:
            return ExecutionResult(False, payload={}, error="order_not_found")
        self._orders[str(order_id)] = PaperOrder(**{**order.__dict__, "status": "canceled"})
        return ExecutionResult(True, {"canceled": [str(order_id)]})

    def cancel_all(self) -> ExecutionResult:
        for order_id, order in list(self._orders.items()):
            self._orders[order_id] = PaperOrder(**{**order.__dict__, "status": "canceled"})
        return ExecutionResult(True, {"canceled": list(self._orders.keys())})

    def get_open_orders(self) -> ExecutionResult:
        return ExecutionResult(
            True,
            {"orders": [order.__dict__.copy() for order in self._orders.values() if order.status == "open"]},
        )

    def get_positions(self) -> ExecutionResult:
        positions = [
            {"token_id": token_id, "size": position.size, "avg_price": position.avg_price}
            for token_id, position in self._positions.snapshot().items()
        ]
        return ExecutionResult(True, {"positions": positions})

    def fills(self) -> List[Dict[str, Any]]:
        return list(self._fills)

    def drain_new_fills(self) -> List[Dict[str, Any]]:
        if self._fill_cursor >= len(self._fills):
            return []
        out = self._fills[self._fill_cursor :]
        self._fill_cursor = len(self._fills)
        return list(out)

    def stats(self) -> Dict[str, float]:
        return dict(self._stats)

    def sweep_fills(self, token_id: Optional[str] = None) -> List[Dict[str, Any]]:
        fills: List[Dict[str, Any]] = []
        for order in list(self._orders.values()):
            if order.status != "open":
                continue
            if token_id is not None and order.token_id != str(token_id):
                continue
            fill = self._try_fill(order, allow_touch_fill=True)
            if fill is not None:
                fills.append(fill)
        return fills

    def _try_fill(self, order: PaperOrder, *, allow_touch_fill: bool = False) -> Optional[Dict[str, Any]]:
        book = self._book_manager.get_book(order.token_id)
        if book is None:
            return None
        fill_price: Optional[float] = None
        fill_trigger = "cross"
        liquidity_mode = "taker"
        if order.side == "buy" and book.best_ask is not None and float(order.price) >= float(book.best_ask):
            fill_price = float(book.best_ask)
        elif order.side == "sell" and book.best_bid is not None and float(order.price) <= float(book.best_bid):
            fill_price = float(book.best_bid)
        elif allow_touch_fill:
            if order.side == "buy" and book.best_bid is not None and float(order.price) >= float(book.best_bid):
                fill_price = float(order.price)
                fill_trigger = "touch"
                liquidity_mode = "maker" if self._fee_mode == "maker" else "simulated_touch"
            elif order.side == "sell" and book.best_ask is not None and float(order.price) <= float(book.best_ask):
                fill_price = float(order.price)
                fill_trigger = "touch"
                liquidity_mode = "maker" if self._fee_mode == "maker" else "simulated_touch"
        if fill_price is None:
            return None

        prior_position = self._positions.get_position(order.token_id)
        closed_qty = 0.0
        realized_gross_pnl = 0.0
        if order.side == "sell" and prior_position.size > 0:
            closed_qty = min(float(order.size), float(prior_position.size))
            realized_gross_pnl = (float(fill_price) - float(prior_position.avg_price)) * closed_qty
        gross_notional = float(order.size) * float(fill_price)
        fee_usdc = gross_notional * self._fee_bps / 10_000.0
        realized_net_pnl = realized_gross_pnl - fee_usdc
        updated_position = self._positions.apply_fill(token_id=order.token_id, side=order.side, size=order.size, price=fill_price)
        ts_ms = _now_ms()
        fill = {
            "order_id": order.order_id,
            "token_id": order.token_id,
            "side": order.side,
            "size": order.size,
            "price": fill_price,
            "ts_ms": ts_ms,
            "placed_at_ms": order.placed_at_ms,
            "client_order_id": order.client_order_id,
            "quote_group_id": order.quote_group_id,
            "gross_notional": gross_notional,
            "fee_bps": self._fee_bps,
            "fee_usdc": fee_usdc,
            "net_notional": gross_notional - fee_usdc,
            "liquidity_mode": liquidity_mode,
            "fill_trigger": fill_trigger,
            "realized_gross_pnl_delta": realized_gross_pnl,
            "realized_net_pnl_delta": realized_net_pnl,
            "inventory_after_fill": {
                "size": updated_position.size,
                "avg_price": updated_position.avg_price,
            },
            "placement_metadata": dict(order.metadata or {}),
        }
        self._stats["realized_gross_pnl"] += realized_gross_pnl
        self._stats["realized_net_pnl"] += realized_net_pnl
        self._stats["cumulative_fees"] += fee_usdc
        self._stats["turnover"] += gross_notional
        if realized_net_pnl > 0:
            self._stats["win_count"] += 1
        elif realized_net_pnl < 0:
            self._stats["loss_count"] += 1
        self._fills.append(fill)
        self._orders[order.order_id] = PaperOrder(**{**order.__dict__, "status": "filled"})
        return fill


def _now_ms() -> int:
    return int(time.time() * 1000)
