from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

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
        diagnostics = book.execution_diagnostics(intent.side.lower(), intent.size, target_mono_ns)

        if book.last_recv_mono_ns > target_mono_ns:
            return [self._reject(intent, "LEAKAGE_GUARD", "LEAKAGE_GUARD", diagnostics=diagnostics)]
        if book.book_is_stale(target_mono_ns, constraint.max_book_staleness_ms):
            return [self._reject(intent, "BOOK_STALE", "BOOK_STALE", diagnostics=diagnostics)]

        side = intent.side.lower()
        exec_price = diagnostics.vwap_price
        if exec_price is None:
            return [self._reject(intent, "INSUFFICIENT_DEPTH", "INSUFFICIENT_DEPTH", diagnostics=diagnostics)]
        if exec_price < constraint.min_price or exec_price > constraint.max_price:
            return [self._reject(intent, "PRICE_BOUNDS", "PRICE_BOUNDS", diagnostics=diagnostics)]

        fee_status = self._fee_status_by_asset.get(intent.asset_id, "unknown")
        fee_mode = intent.mode if intent.mode else self._config.fee_mode
        fee_bps_val = fee_bps(exec_price, fee_mode, fee_status)
        fill_payload = self._event_payload(
            intent,
            diagnostics=diagnostics,
            fee_bps_val=fee_bps_val,
            target_wall_ms=target_wall_ms,
        )

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
            payload=fill_payload,
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

    def _reject(
        self,
        intent: OrderIntent,
        reason: str,
        error_code: str,
        diagnostics: Optional[Any] = None,
    ) -> BrokerEvent:
        payload: Dict[str, Any] = {
            "reason": reason,
            "error_code": error_code,
            "t_event_wall_ms": int(intent.as_of_ts_ms),
            "simulated": True,
            "asset_id": str(intent.asset_id),
            "side": str(intent.side).lower(),
            "mode": str(intent.mode or self._config.fee_mode).upper(),
            "client_order_id": str(intent.client_order_id),
        }
        if diagnostics is not None:
            payload.update(
                {
                    "depth_at_qty": float(getattr(diagnostics, "depth_at_qty", 0.0) or 0.0),
                    "spread_bps": self._float_or_none(getattr(diagnostics, "spread_bps", None)),
                    "book_age_ms": self._float_or_none(getattr(diagnostics, "book_age_ms", None)),
                }
            )
        return BrokerEvent(
            event_type="order_reject",
            order_id=intent.order_id,
            payload=payload,
        )

    def _event_payload(
        self,
        intent: OrderIntent,
        diagnostics: Any,
        fee_bps_val: float,
        target_wall_ms: int,
    ) -> Dict[str, Any]:
        mode = str(intent.mode or self._config.fee_mode).upper()
        return {
            "broker": "sim",
            "simulated": True,
            "asset_id": str(intent.asset_id),
            "side": str(intent.side).lower(),
            "mode": mode,
            "client_order_id": str(intent.client_order_id),
            "fill_price": float(diagnostics.vwap_price),
            "fill_size": float(intent.size),
            "fees_bps": float(fee_bps_val),
            "vwap_price": self._float_or_none(getattr(diagnostics, "vwap_price", None)),
            "depth_at_qty": float(getattr(diagnostics, "depth_at_qty", 0.0) or 0.0),
            "slippage_bps": self._float_or_none(getattr(diagnostics, "slippage_bps", None)),
            "spread_bps": self._float_or_none(getattr(diagnostics, "spread_bps", None)),
            "book_age_ms": self._float_or_none(getattr(diagnostics, "book_age_ms", None)),
            "fee_bps": float(fee_bps_val),
            "fee_mode": mode,
            "fill_model": "book_vwap",
            "latency_ms": int(self._config.latency_ms),
            "t_event_wall_ms": int(target_wall_ms),
            "t_fill_wall_ms": int(target_wall_ms),
        }

    @staticmethod
    def _float_or_none(value: Optional[float]) -> Optional[float]:
        if value is None:
            return None
        return float(value)
