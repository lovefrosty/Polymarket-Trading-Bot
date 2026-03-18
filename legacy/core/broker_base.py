from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional


@dataclass(frozen=True)
class OrderIntent:
    order_id: str
    client_order_id: str
    asset_id: str
    side: str
    size: float
    price: float
    mode: str
    t_decision_wall_ms: int
    as_of_ts_ms: int
    decision_id: Optional[str] = None
    reason: Optional[str] = None
    post_only: bool = False
    time_in_force: str = "GTC"
    reduce_only: bool = False
    quote_group_id: Optional[str] = None
    idempotency_key: Optional[str] = None


@dataclass(frozen=True)
class BrokerEvent:
    event_type: str
    order_id: str
    payload: Dict[str, Any]


@dataclass(frozen=True)
class BrokerSnapshot:
    open_orders: Dict[str, Any]
    meta: Dict[str, Any]


class BrokerBase:
    def submit(self, intent: OrderIntent) -> List[BrokerEvent]:
        raise RuntimeError("BrokerBase.submit must be implemented")

    def cancel(self, order_id: str) -> List[BrokerEvent]:
        raise RuntimeError("BrokerBase.cancel must be implemented")

    def replace(self, order_id: str, new_intent: OrderIntent) -> List[BrokerEvent]:
        raise RuntimeError("BrokerBase.replace must be implemented")

    def snapshot(self) -> BrokerSnapshot:
        return BrokerSnapshot(open_orders={}, meta={})
