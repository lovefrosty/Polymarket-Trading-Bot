from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence

from core_mm.positions import PositionTracker


@dataclass(frozen=True)
class UserEvent:
    event_type: str
    order_id: Optional[str]
    token_id: Optional[str]
    side: Optional[str]
    status: Optional[str]
    size: float = 0.0
    size_matched: float = 0.0
    price: Optional[float] = None
    raw: Optional[Dict[str, Any]] = None


@dataclass(frozen=True)
class UserOrderState:
    order_id: str
    token_id: Optional[str]
    side: Optional[str]
    status: str
    size: float
    size_matched: float
    avg_fill_price: Optional[float]


class UserFeedState:
    def __init__(self, *, position_tracker: Optional[PositionTracker] = None) -> None:
        self._position_tracker = position_tracker or PositionTracker()
        self._orders: Dict[str, UserOrderState] = {}
        self._seen_fills: set[tuple[str, float, Optional[float], str]] = set()

    @property
    def position_tracker(self) -> PositionTracker:
        return self._position_tracker

    def get_order(self, order_id: str) -> Optional[UserOrderState]:
        return self._orders.get(str(order_id))

    def orders(self) -> Dict[str, UserOrderState]:
        return dict(self._orders)

    def apply_message(self, message: Dict[str, Any]) -> Sequence[UserEvent]:
        events = parse_user_message(message)
        for event in events:
            self.apply_event(event)
        return events

    def apply_event(self, event: UserEvent) -> None:
        order_id = event.order_id
        if order_id is None:
            return
        current = self._orders.get(order_id)
        if current is None:
            current = UserOrderState(
                order_id=order_id,
                token_id=event.token_id,
                side=event.side,
                status="new",
                size=event.size,
                size_matched=0.0,
                avg_fill_price=None,
            )
        if event.event_type == "trade":
            dedupe = (order_id, float(event.size), event.price, str(event.raw.get("trade_id") if event.raw else ""))
            if dedupe in self._seen_fills:
                return
            self._seen_fills.add(dedupe)
            next_matched = current.size_matched + float(event.size)
            avg_fill_price = current.avg_fill_price
            if event.price is not None and event.size > 0:
                if avg_fill_price is None or current.size_matched <= 0:
                    avg_fill_price = event.price
                else:
                    avg_fill_price = ((avg_fill_price * current.size_matched) + (event.price * event.size)) / next_matched
            self._orders[order_id] = UserOrderState(
                order_id=order_id,
                token_id=event.token_id or current.token_id,
                side=event.side or current.side,
                status="filled" if current.size and next_matched >= current.size else "partial_fill",
                size=max(current.size, event.size),
                size_matched=next_matched,
                avg_fill_price=avg_fill_price,
            )
            if event.token_id and event.side and event.price is not None and event.size > 0:
                self._position_tracker.apply_fill(
                    token_id=event.token_id,
                    side=event.side,
                    size=event.size,
                    price=event.price,
                )
            return

        merged_status = event.status or current.status
        merged_size = max(current.size, event.size)
        matched = max(current.size_matched, event.size_matched)
        self._orders[order_id] = UserOrderState(
            order_id=order_id,
            token_id=event.token_id or current.token_id,
            side=event.side or current.side,
            status=merged_status,
            size=merged_size,
            size_matched=matched,
            avg_fill_price=current.avg_fill_price,
        )


def parse_user_message(message: Dict[str, Any]) -> Sequence[UserEvent]:
    view = _payload_view(message)
    if isinstance(view, list):
        events: List[UserEvent] = []
        for item in view:
            if isinstance(item, dict):
                events.extend(parse_user_message(item))
        return tuple(events)
    if not isinstance(view, dict):
        return ()

    event_type = _normalize_event_type(view)
    if event_type == "trade":
        return (
            UserEvent(
                event_type="trade",
                order_id=_coerce_str(view.get("order_id") or view.get("orderID") or view.get("maker_order_id") or view.get("makerOrderId")),
                token_id=_coerce_str(view.get("asset_id") or view.get("assetId") or view.get("token_id") or view.get("tokenId")),
                side=_normalize_side(view.get("side") or view.get("maker_side") or view.get("makerSide")),
                status="trade",
                size=_coerce_float(view.get("size") or view.get("matched_size") or view.get("size_matched") or 0.0),
                size_matched=_coerce_float(view.get("size_matched") or view.get("matched_size") or 0.0),
                price=_coerce_float_or_none(view.get("price") or view.get("fill_price") or view.get("matched_price")),
                raw=dict(view),
            ),
        )

    if event_type == "order":
        status = _normalize_order_status(view.get("status") or view.get("event") or view.get("event_type"))
        return (
            UserEvent(
                event_type="order",
                order_id=_coerce_str(view.get("order_id") or view.get("orderID") or view.get("id")),
                token_id=_coerce_str(view.get("asset_id") or view.get("assetId") or view.get("token_id") or view.get("tokenId")),
                side=_normalize_side(view.get("side")),
                status=status,
                size=_coerce_float(view.get("size") or 0.0),
                size_matched=_coerce_float(view.get("size_matched") or view.get("matched_size") or 0.0),
                price=_coerce_float_or_none(view.get("price")),
                raw=dict(view),
            ),
        )
    return ()


def _payload_view(message: Dict[str, Any]) -> Any:
    if not isinstance(message, dict):
        return message
    data = message.get("data")
    if isinstance(data, (dict, list)):
        return data
    return message


def _normalize_event_type(view: Dict[str, Any]) -> str:
    event_type = str(view.get("event_type") or view.get("type") or view.get("event") or "").strip().lower()
    if event_type in {"trade", "fill", "matched"}:
        return "trade"
    if event_type in {"order", "placement", "update", "cancellation", "cancelled", "canceled"}:
        return "order"
    return event_type


def _normalize_order_status(value: Any) -> str:
    text = str(value or "unknown").strip().lower()
    mapping = {
        "placement": "placed",
        "placed": "placed",
        "update": "updated",
        "updated": "updated",
        "cancellation": "canceled",
        "cancelled": "canceled",
        "canceled": "canceled",
        "trade": "filled",
    }
    return mapping.get(text, text or "unknown")


def _normalize_side(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip().lower()
    if text in {"buy", "bid"}:
        return "buy"
    if text in {"sell", "ask"}:
        return "sell"
    return text or None


def _coerce_str(value: Any) -> Optional[str]:
    if value in (None, ""):
        return None
    return str(value)


def _coerce_float(value: Any) -> float:
    if value in (None, ""):
        return 0.0
    return float(value)


def _coerce_float_or_none(value: Any) -> Optional[float]:
    if value in (None, ""):
        return None
    return float(value)
