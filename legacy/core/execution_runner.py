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


@dataclass(frozen=True)
class ExecutionRunnerConfig:
    sim_exec: bool = False
    cli_exec: bool = False
    min_execution_confidence: float = 0.0
    max_order_notional_pct: Optional[float] = None
    max_market_exposure_pct: Optional[float] = None
    failure_cooldown_ms: int = 0
    force_flat_near_expiry_ms: Optional[int] = None


def default_risk_gate(record: DecisionRecord, intent: Dict[str, Any]) -> RiskGateResult:
    if not bool((record.gates or {}).get("allow", True)):
        reasons = (record.gates or {}).get("reasons") or ["GATES_BLOCKED"]
        return RiskGateResult(allow=False, reasons=[str(r) for r in reasons], error_code="GATES_BLOCKED")
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
        config: Optional[ExecutionRunnerConfig] = None,
    ) -> None:
        self._trade_tape = trade_tape
        self._time_mapper = time_mapper
        self._broker = broker
        self._risk_gate = risk_gate or default_risk_gate
        self._config = config or ExecutionRunnerConfig()
        if self._config.sim_exec and self._config.cli_exec:
            raise ValueError("execution_runner_exec_mode_conflict")
        if enable_trading is None:
            enable_trading = os.getenv("ENABLE_TRADING", "").strip().lower() in {"1", "true", "yes", "on"}
        self._enable_trading = bool(enable_trading)
        self._decision_seq = 0
        self._last_trade_event_id_by_order: Dict[str, object] = {}
        self._event_seq_by_order: Dict[str, int] = {}
        self._last_failure_ts_by_asset: Dict[str, int] = {}
        self._order_context_by_order: Dict[str, Dict[str, Any]] = {}

    def handle_decision(self, record: DecisionRecord, intents: List[Dict[str, Any]]) -> None:
        if not intents:
            return
        self._decision_seq += 1
        for idx, intent in enumerate(intents):
            intent_type = str(intent.get("intent_type") or "order")
            if intent_type == "quote":
                self._handle_quote_intent(record, idx, intent)
                continue
            order_id = self._order_id(record, idx)
            client_order_id = f"{order_id}:client"
            as_of_ts_ms = int(intent.get("as_of_ts_ms") or record.t_decision_wall_ms)
            intent_payload = {
                "schema_version": "trade_v1",
                "run_id": record.run_id,
                "event_id": self._new_event_id(
                    order_id=order_id,
                    event_type="order_intent",
                    parent_event_id=None,
                    raw_subset={
                        "asset_id": intent.get("asset_id"),
                        "side": intent.get("side"),
                        "size": intent.get("size"),
                        "price": intent.get("price"),
                        "decision_id": intent.get("decision_id") or record.run_id,
                    },
                ),
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
                "as_of_ts_ms": as_of_ts_ms,
                "decision_id": intent.get("decision_id"),
                "reason": intent.get("reason"),
                "post_only": bool(intent.get("post_only", False)),
                "time_in_force": str(intent.get("time_in_force") or "GTC"),
                "reduce_only": bool(intent.get("reduce_only", False)),
                "quote_group_id": intent.get("quote_group_id"),
            }
            self._trade_tape.write(intent_payload)
            self._last_trade_event_id_by_order[order_id] = intent_payload["event_id"]
            self._remember_order_context(order_id, intent_payload)

            if not self._enable_trading:
                continue

            gate = self._pre_execution_guard(record, intent)
            if not gate.allow:
                self._emit_reject(
                    order_id=order_id,
                    reasons=gate.reasons,
                    error_code=gate.error_code,
                    as_of_ts_ms=as_of_ts_ms,
                )
                continue

            if self._broker is None:
                continue

            try:
                size = float(intent.get("size"))
                price = float(intent.get("price"))
            except (TypeError, ValueError):
                self._emit_reject(
                    order_id=order_id,
                    reasons=["INVALID_INTENT"],
                    error_code="INVALID_INTENT",
                    as_of_ts_ms=as_of_ts_ms,
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
                as_of_ts_ms=as_of_ts_ms,
                decision_id=intent.get("decision_id"),
                reason=intent.get("reason"),
                post_only=bool(intent.get("post_only", False)),
                time_in_force=str(intent.get("time_in_force") or "GTC"),
                reduce_only=bool(intent.get("reduce_only", False)),
                quote_group_id=intent.get("quote_group_id"),
                idempotency_key=intent.get("idempotency_key"),
            )
            events = self._broker.submit(broker_intent)
            self._record_failures(broker_intent.asset_id, as_of_ts_ms, events)
            for event in events:
                self._emit_broker_event(event, broker_intent.as_of_ts_ms)

    def _handle_quote_intent(self, record: DecisionRecord, intent_idx: int, intent: Dict[str, Any]) -> None:
        order_id = str(intent.get("order_id") or self._order_id(record, intent_idx))
        client_order_id = str(intent.get("client_order_id") or f"{order_id}:client")
        action = str(intent.get("action") or "submit").lower()
        as_of_ts = int(intent.get("as_of_ts_ms") or record.t_decision_wall_ms)

        intent_event_id = self._new_event_id(
            order_id=order_id,
            event_type="order_intent",
            parent_event_id=None,
            raw_subset={
                "asset_id": intent.get("asset_id"),
                "side": intent.get("side"),
                "size": intent.get("size"),
                "price": intent.get("price"),
                "quote_action": action,
            },
        )
        self._trade_tape.write(
            {
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
                "t_event_wall_ms": as_of_ts,
                "t_event_mono_ns": int(self._time_mapper.mono_ns_from_wall_ms(as_of_ts)),
                "as_of_ts_ms": as_of_ts,
                "decision_id": intent.get("decision_id"),
                "reason": intent.get("reason"),
                "quote_group_id": intent.get("quote_group_id"),
                "quote_action": action,
                "post_only": bool(intent.get("post_only", False)),
                "time_in_force": str(intent.get("time_in_force") or "GTC"),
                "reduce_only": bool(intent.get("reduce_only", False)),
            }
        )
        self._last_trade_event_id_by_order[order_id] = intent_event_id
        self._remember_order_context(
            order_id,
            {
                "client_order_id": client_order_id,
                "asset_id": intent.get("asset_id"),
                "side": intent.get("side"),
                "size": intent.get("size"),
                "price": intent.get("price"),
                "mode": intent.get("mode"),
                "decision_id": intent.get("decision_id"),
                "quote_group_id": intent.get("quote_group_id"),
                "post_only": bool(intent.get("post_only", False)),
                "time_in_force": str(intent.get("time_in_force") or "GTC"),
                "reduce_only": bool(intent.get("reduce_only", False)),
            },
        )

        if not self._enable_trading:
            return
        if self._broker is None:
            return

        gate = self._pre_execution_guard(record, intent)
        if not gate.allow:
            self._emit_reject(
                order_id=order_id,
                reasons=gate.reasons,
                error_code=gate.error_code,
                as_of_ts_ms=as_of_ts,
            )
            return

        if action == "cancel":
            events = self._broker.cancel(order_id)
            self._record_failures(str(intent.get("asset_id") or ""), as_of_ts, events)
            for event in events:
                self._emit_broker_event(event, as_of_ts)
            return

        try:
            size = float(intent.get("size"))
            price = float(intent.get("price"))
        except (TypeError, ValueError):
            self._emit_reject(order_id=order_id, reasons=["INVALID_INTENT"], error_code="INVALID_INTENT", as_of_ts_ms=as_of_ts)
            return

        broker_intent = OrderIntent(
            order_id=order_id,
            client_order_id=client_order_id,
            asset_id=str(intent.get("asset_id")),
            side=str(intent.get("side")),
            size=size,
            price=price,
            mode=str(intent.get("mode") or "MAKE"),
            t_decision_wall_ms=record.t_decision_wall_ms,
            as_of_ts_ms=as_of_ts,
            decision_id=intent.get("decision_id"),
            reason=intent.get("reason"),
            post_only=bool(intent.get("post_only", True)),
            time_in_force=str(intent.get("time_in_force") or "GTC"),
            reduce_only=bool(intent.get("reduce_only", False)),
            quote_group_id=intent.get("quote_group_id"),
            idempotency_key=intent.get("idempotency_key"),
        )

        if action == "replace":
            replace_target = str(intent.get("replace_order_id") or order_id)
            events = self._broker.replace(replace_target, broker_intent)
        else:
            events = self._broker.submit(broker_intent)
        self._record_failures(broker_intent.asset_id, as_of_ts, events)
        for event in events:
            self._emit_broker_event(event, broker_intent.as_of_ts_ms)

    def _order_id(self, record: DecisionRecord, intent_idx: int) -> str:
        return f"{record.run_id}:{self._decision_seq}:{record.asset_id}:{intent_idx}"

    def _pre_execution_guard(self, record: DecisionRecord, intent: Dict[str, Any]) -> RiskGateResult:
        gate = self._risk_gate(record, intent)
        if not gate.allow:
            return gate

        book = record.book or {}
        if bool(book.get("book_stale")):
            return RiskGateResult(False, ["BOOK_STALE"], "BOOK_STALE")

        confidence = _extract_confidence(record)
        if confidence is None:
            confidence = 0.0
        if confidence < float(self._config.min_execution_confidence):
            return RiskGateResult(False, ["LOW_CONFIDENCE"], "LOW_CONFIDENCE")

        asset_id = str(intent.get("asset_id") or record.asset_id or "")
        now_ms = int(intent.get("as_of_ts_ms") or record.t_decision_wall_ms)
        if self._config.failure_cooldown_ms > 0:
            last_failure = self._last_failure_ts_by_asset.get(asset_id)
            if last_failure is not None and now_ms - int(last_failure) < int(self._config.failure_cooldown_ms):
                return RiskGateResult(False, ["FAILURE_COOLDOWN"], "FAILURE_COOLDOWN")

        max_order_notional_pct = _extract_pct(
            intent=intent,
            notes=record.notes or {},
            direct_key="order_notional_pct",
            nested_key="risk_context",
        )
        if (
            self._config.max_order_notional_pct is not None
            and max_order_notional_pct is not None
            and max_order_notional_pct > float(self._config.max_order_notional_pct)
        ):
            return RiskGateResult(False, ["MAX_ORDER_NOTIONAL_PCT"], "MAX_ORDER_NOTIONAL_PCT")

        max_market_exposure_pct = _extract_pct(
            intent=intent,
            notes=record.notes or {},
            direct_key="market_exposure_pct",
            nested_key="risk_context",
        )
        if (
            self._config.max_market_exposure_pct is not None
            and max_market_exposure_pct is not None
            and max_market_exposure_pct > float(self._config.max_market_exposure_pct)
        ):
            return RiskGateResult(False, ["MAX_MARKET_EXPOSURE_PCT"], "MAX_MARKET_EXPOSURE_PCT")

        if self._config.force_flat_near_expiry_ms is not None:
            ms_remaining = _time_remaining_ms(record)
            reduce_only = bool(intent.get("reduce_only", False))
            if ms_remaining is not None and ms_remaining <= int(self._config.force_flat_near_expiry_ms) and not reduce_only:
                return RiskGateResult(False, ["FORCE_FLAT_ONLY"], "FORCE_FLAT_ONLY")

        return RiskGateResult(True, [], None)

    def _record_failures(self, asset_id: str, as_of_ts_ms: int, events: List[BrokerEvent]) -> None:
        if any(event.event_type in {"order_reject", "broker_error"} for event in events):
            self._last_failure_ts_by_asset[str(asset_id)] = int(as_of_ts_ms)

    def _emit_reject(
        self,
        *,
        order_id: str,
        reasons: List[str],
        error_code: Optional[str],
        as_of_ts_ms: int,
    ) -> None:
        context = self._order_context_by_order.get(order_id, {})
        parent_event_id = self._last_trade_event_id_by_order.get(order_id)
        event_id = self._new_event_id(
            order_id=order_id,
            event_type="order_reject",
            parent_event_id=parent_event_id,
            raw_subset={"reasons": list(reasons), "error_code": error_code or "RISK_GATE"},
        )
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
            "client_order_id": context.get("client_order_id"),
            "asset_id": context.get("asset_id"),
            "side": context.get("side"),
            "size": context.get("size"),
            "price": context.get("price"),
            "decision_id": context.get("decision_id"),
            "reason": ",".join(reasons) if reasons else "RISK_GATE",
            "error_code": error_code or "RISK_GATE",
        }
        self._trade_tape.write(payload)
        self._last_trade_event_id_by_order[order_id] = event_id

    def _emit_broker_event(self, event: BrokerEvent, as_of_ts_ms: int) -> None:
        context = self._order_context_by_order.get(event.order_id, {})
        payload = dict(context)
        payload.update(event.payload)
        broker_event_id = payload.pop("event_id", None)
        payload.pop("parent_event_id", None)
        t_event_wall_ms = int(payload.get("t_event_wall_ms") or as_of_ts_ms)
        parent_event_id = self._last_trade_event_id_by_order.get(event.order_id)
        event_id = self._new_event_id(
            order_id=event.order_id,
            event_type=event.event_type,
            parent_event_id=parent_event_id,
            raw_subset=payload,
        )
        payload.update(
            {
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
        )
        if broker_event_id is not None:
            payload["broker_event_id"] = broker_event_id
        self._trade_tape.write(payload)
        self._last_trade_event_id_by_order[event.order_id] = payload["event_id"]

    def _new_event_id(
        self,
        *,
        order_id: str,
        event_type: str,
        parent_event_id: Optional[object],
        raw_subset: Optional[Dict[str, Any]] = None,
    ) -> str:
        seq = int(self._event_seq_by_order.get(order_id, 0))
        event_id = TradeTape.deterministic_event_id(
            run_id=self._trade_tape.run_id,
            order_id=order_id,
            event_type=event_type,
            parent_event_id=parent_event_id,
            event_seq_within_order=seq,
            raw_subset=raw_subset or {},
        )
        self._event_seq_by_order[order_id] = seq + 1
        return event_id

    def _remember_order_context(self, order_id: str, payload: Dict[str, Any]) -> None:
        self._order_context_by_order[order_id] = {
            "client_order_id": payload.get("client_order_id"),
            "asset_id": payload.get("asset_id"),
            "side": payload.get("side"),
            "size": payload.get("size"),
            "price": payload.get("price"),
            "mode": payload.get("mode"),
            "decision_id": payload.get("decision_id"),
            "quote_group_id": payload.get("quote_group_id"),
            "post_only": payload.get("post_only"),
            "time_in_force": payload.get("time_in_force"),
            "reduce_only": payload.get("reduce_only"),
        }


def _extract_confidence(record: DecisionRecord) -> Optional[float]:
    notes = record.notes or {}
    confidence = notes.get("confidence")
    if confidence is None:
        confidence = notes.get("chosen_action", {}).get("confidence")
    if confidence is None:
        return None
    try:
        return float(confidence)
    except (TypeError, ValueError):
        return None


def _extract_pct(
    *,
    intent: Dict[str, Any],
    notes: Dict[str, Any],
    direct_key: str,
    nested_key: str,
) -> Optional[float]:
    candidates = [
        intent.get(direct_key),
        notes.get(direct_key),
        (notes.get(nested_key) or {}).get(direct_key),
    ]
    for value in candidates:
        if value is None:
            continue
        try:
            return float(value)
        except (TypeError, ValueError):
            continue
    return None


def _time_remaining_ms(record: DecisionRecord) -> Optional[int]:
    notes = record.notes or {}
    signals = notes.get("signals") or {}
    value = signals.get("time_remaining_sec")
    if value is None:
        value = notes.get("time_remaining_sec")
    if value is None:
        return None
    try:
        return int(float(value) * 1000.0)
    except (TypeError, ValueError):
        return None
