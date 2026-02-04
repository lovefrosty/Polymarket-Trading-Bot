from __future__ import annotations

from dataclasses import dataclass
import os
from typing import Any, Callable, Dict, List, Optional

from core.broker_base import BrokerBase, BrokerEvent, OrderIntent
from core.decision_tape import DecisionRecord, TimeMapper
from core.trade_tape import TradeTape


@dataclass(frozen=True)
class RiskGateResult:
    allow: bool
    reasons: List[str]
    error_code: Optional[str]


def default_risk_gate(record: DecisionRecord, intent: Dict[str, Any]) -> RiskGateResult:
    notes = record.notes or {}
    entry_gate = notes.get("entry_gate") or {}
    if not entry_gate.get("allow", True):
        reasons = entry_gate.get("reasons") or []
        return RiskGateResult(allow=False, reasons=[str(r) for r in reasons], error_code="RISK_GATE")
    return RiskGateResult(allow=True, reasons=[], error_code=None)


class ExecutionRunner:
    def __init__(
        self,
        trade_tape: TradeTape,
        time_mapper: TimeMapper,
        broker: Optional[BrokerBase] = None,
        risk_gate: Optional[Callable[[DecisionRecord, Dict[str, Any]], RiskGateResult]] = None,
        enable_trading: Optional[bool] = None,
    ) -> None:
        self._trade_tape = trade_tape
        self._time_mapper = time_mapper
        self._broker = broker
        self._risk_gate = risk_gate or default_risk_gate
        if enable_trading is None:
            enable_trading = os.getenv("ENABLE_TRADING", "").strip().lower() in {"1", "true", "yes", "on"}
        self._enable_trading = bool(enable_trading)
        self._decision_seq = 0

    def handle_decision(self, record: DecisionRecord, intents: List[Dict[str, Any]]) -> None:
        if not intents:
            return
        self._decision_seq += 1
        for idx, intent in enumerate(intents):
            order_id = self._order_id(record, idx)
            client_order_id = f"{order_id}:client"
            intent_event_id = self._trade_tape.next_event_id()
            intent_payload = {
                "schema_version": "trade_v1",
                "run_id": record.run_id,
                "event_id": intent_event_id,
                "parent_event_id": None,
                "event_type": "order_intent",
                "order_id": order_id,
                "client_order_id": client_order_id,
                "asset_id": intent.get("asset_id"),
                "side": intent.get("side"),
                "size": intent.get("size"),
                "price": intent.get("price"),
                "mode": intent.get("mode"),
                "t_decision_wall_ms": record.t_decision_wall_ms,
                "t_event_wall_ms": record.t_decision_wall_ms,
                "t_event_mono_ns": record.t_decision_mono_ns,
                "as_of_ts_ms": int(intent.get("as_of_ts_ms") or record.t_decision_wall_ms),
                "decision_id": intent.get("decision_id"),
                "reason": intent.get("reason"),
            }
            self._trade_tape.write(intent_payload)

            if not self._enable_trading:
                continue

            gate = self._risk_gate(record, intent)
            if not gate.allow:
                self._emit_reject(
                    order_id,
                    intent_event_id,
                    gate.reasons,
                    gate.error_code,
                    int(intent.get("as_of_ts_ms") or record.t_decision_wall_ms),
                )
                continue

            if self._broker is None:
                continue

            try:
                size = float(intent.get("size"))
                price = float(intent.get("price"))
            except (TypeError, ValueError):
                self._emit_reject(
                    order_id,
                    intent_event_id,
                    ["INVALID_INTENT"],
                    "INVALID_INTENT",
                    int(intent.get("as_of_ts_ms") or record.t_decision_wall_ms),
                )
                continue

            broker_intent = OrderIntent(
                order_id=order_id,
                client_order_id=client_order_id,
                asset_id=str(intent.get("asset_id")),
                side=str(intent.get("side")),
                size=size,
                price=price,
                mode=str(intent.get("mode")),
                t_decision_wall_ms=record.t_decision_wall_ms,
                as_of_ts_ms=int(intent.get("as_of_ts_ms") or record.t_decision_wall_ms),
                decision_id=intent.get("decision_id"),
                reason=intent.get("reason"),
            )
            events = self._broker.submit(broker_intent)
            for event in events:
                self._emit_broker_event(event, intent_event_id, broker_intent.as_of_ts_ms)

    def _order_id(self, record: DecisionRecord, intent_idx: int) -> str:
        return f"{record.run_id}:{self._decision_seq}:{record.asset_id}:{intent_idx}"

    def _emit_reject(
        self,
        order_id: str,
        parent_event_id: int,
        reasons: List[str],
        error_code: Optional[str],
        as_of_ts_ms: int,
    ) -> None:
        event_id = self._trade_tape.next_event_id()
        payload = {
            "schema_version": "trade_v1",
            "run_id": self._trade_tape.run_id,
            "event_id": event_id,
            "parent_event_id": parent_event_id,
            "event_type": "order_reject",
            "order_id": order_id,
            "t_event_wall_ms": int(as_of_ts_ms),
            "t_event_mono_ns": int(self._time_mapper.mono_ns_from_wall_ms(as_of_ts_ms)),
            "as_of_ts_ms": int(as_of_ts_ms),
            "reason": ",".join(reasons) if reasons else "RISK_GATE",
            "error_code": error_code or "RISK_GATE",
        }
        self._trade_tape.write(payload)

    def _emit_broker_event(self, event: BrokerEvent, parent_event_id: int, as_of_ts_ms: int) -> None:
        event_id = self._trade_tape.next_event_id()
        t_event_wall_ms = int(event.payload.get("t_event_wall_ms") or as_of_ts_ms)
        payload = {
            "schema_version": "trade_v1",
            "run_id": self._trade_tape.run_id,
            "event_id": event_id,
            "parent_event_id": parent_event_id,
            "event_type": event.event_type,
            "order_id": event.order_id,
            "t_event_wall_ms": t_event_wall_ms,
            "t_event_mono_ns": int(self._time_mapper.mono_ns_from_wall_ms(t_event_wall_ms)),
            "as_of_ts_ms": int(as_of_ts_ms),
        }
        payload.update(event.payload)
        self._trade_tape.write(payload)
