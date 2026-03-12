from __future__ import annotations

from dataclasses import dataclass
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
    status: str = "open"


class PaperBroker:
    def __init__(self, *, book_manager: BookManager, position_tracker: Optional[PositionTracker] = None) -> None:
        self._book_manager = book_manager
        self._positions = position_tracker or PositionTracker()
        self._orders: Dict[str, PaperOrder] = {}
        self._fills: List[Dict[str, Any]] = []
        self._next_id = 1

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
        neg_risk: bool = False,
        time_in_force: Optional[str] = None,
    ) -> ExecutionResult:
        order_id = f"paper-{self._next_id}"
        self._next_id += 1
        order = PaperOrder(order_id=order_id, token_id=str(token_id), side=str(side).lower(), price=float(price), size=float(size))
        self._orders[order_id] = order
        fill = self._try_fill(order)
        payload: Dict[str, Any] = {"orderID": order_id, "status": order.status, "neg_risk": bool(neg_risk)}
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
        if order.side == "buy" and book.best_ask is not None and float(order.price) >= float(book.best_ask):
            fill_price = float(book.best_ask)
        elif order.side == "sell" and book.best_bid is not None and float(order.price) <= float(book.best_bid):
            fill_price = float(book.best_bid)
        elif allow_touch_fill:
            if order.side == "buy" and book.best_bid is not None and float(order.price) >= float(book.best_bid):
                fill_price = float(order.price)
            elif order.side == "sell" and book.best_ask is not None and float(order.price) <= float(book.best_ask):
                fill_price = float(order.price)
        if fill_price is None:
            return None

        self._positions.apply_fill(token_id=order.token_id, side=order.side, size=order.size, price=fill_price)
        fill = {
            "order_id": order.order_id,
            "token_id": order.token_id,
            "side": order.side,
            "size": order.size,
            "price": fill_price,
        }
        self._fills.append(fill)
        self._orders[order.order_id] = PaperOrder(**{**order.__dict__, "status": "filled"})
        return fill
