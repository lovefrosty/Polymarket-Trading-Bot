from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional

from core.broker_base import BrokerBase, BrokerEvent, OrderIntent
from core.decision_tape import TimeMapper
from core.fees import fee_bps
from core.order_book import OrderBook
from core.validators import OrderConstraints


@dataclass(frozen=True)
class SimBrokerConfig:
    latency_ms: int = 0
    fee_mode: str = "TAKE"


class SimBroker(BrokerBase):
    def __init__(
        self,
        books: Dict[str, OrderBook],
        constraints: Dict[str, OrderConstraints],
        time_mapper: TimeMapper,
        fee_status_by_asset: Optional[Dict[str, str]] = None,
        config: Optional[SimBrokerConfig] = None,
    ) -> None:
        self._books = books
        self._constraints = constraints
        self._time_mapper = time_mapper
        self._fee_status_by_asset = fee_status_by_asset or {}
        self._config = config or SimBrokerConfig()

    def submit(self, intent: OrderIntent) -> List[BrokerEvent]:
        book = self._books.get(intent.asset_id)
        constraint = self._constraints.get(intent.asset_id)
        if book is None or constraint is None:
            return [self._reject(intent, "NO_BOOK", "NO_BOOK")]

        target_wall_ms = int(intent.as_of_ts_ms) + int(self._config.latency_ms)
        target_mono_ns = self._time_mapper.mono_ns_from_wall_ms(target_wall_ms)

        if book.last_recv_mono_ns > target_mono_ns:
            return [self._reject(intent, "LEAKAGE_GUARD", "LEAKAGE_GUARD")]
        if book.book_is_stale(target_mono_ns, constraint.max_book_staleness_ms):
            return [self._reject(intent, "BOOK_STALE", "BOOK_STALE")]

        side = intent.side.lower()
        exec_price = book.vwap_to_fill(side, intent.size)
        if exec_price is None:
            return [self._reject(intent, "INSUFFICIENT_DEPTH", "INSUFFICIENT_DEPTH")]
        if exec_price < constraint.min_price or exec_price > constraint.max_price:
            return [self._reject(intent, "PRICE_BOUNDS", "PRICE_BOUNDS")]

        fee_status = self._fee_status_by_asset.get(intent.asset_id, "unknown")
        fee_mode = intent.mode if intent.mode else self._config.fee_mode
        fee_bps_val = fee_bps(exec_price, fee_mode, fee_status)

        submit_event = BrokerEvent(
            event_type="order_submit",
            order_id=intent.order_id,
            payload={
                "broker": "sim",
                "status": "submitted",
                "t_event_wall_ms": int(intent.as_of_ts_ms),
                "t_send_wall_ms": int(intent.as_of_ts_ms),
            },
        )
        ack_event = BrokerEvent(
            event_type="order_ack",
            order_id=intent.order_id,
            payload={
                "broker": "sim",
                "status": "accepted",
                "t_event_wall_ms": target_wall_ms,
                "t_ack_wall_ms": target_wall_ms,
            },
        )
        fill_event = BrokerEvent(
            event_type="order_fill",
            order_id=intent.order_id,
            payload={
                "fill_price": float(exec_price),
                "fill_size": float(intent.size),
                "fees_bps": float(fee_bps_val),
                "t_event_wall_ms": target_wall_ms,
                "t_fill_wall_ms": target_wall_ms,
            },
        )
        return [submit_event, ack_event, fill_event]

    def cancel(self, order_id: str) -> List[BrokerEvent]:
        return [
            BrokerEvent(
                event_type="order_cancel",
                order_id=order_id,
                payload={"reason": "SIM_CANCEL", "t_event_wall_ms": 0},
            )
        ]

    def replace(self, order_id: str, new_intent: OrderIntent) -> List[BrokerEvent]:
        return [
            BrokerEvent(
                event_type="broker_error",
                order_id=order_id,
                payload={"error_code": "SIM_REPLACE_UNSUPPORTED", "t_event_wall_ms": 0},
            )
        ]

    def _reject(self, intent: OrderIntent, reason: str, error_code: str) -> BrokerEvent:
        return BrokerEvent(
            event_type="order_reject",
            order_id=intent.order_id,
            payload={"reason": reason, "error_code": error_code, "t_event_wall_ms": int(intent.as_of_ts_ms)},
        )
