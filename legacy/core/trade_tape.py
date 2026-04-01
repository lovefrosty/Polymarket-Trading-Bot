from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from typing import Any, Dict, Optional
import threading


EVENT_TYPES = {
    "order_intent",
    "order_submit",
    "order_ack",
    "order_fill",
    "order_cancel",
    "order_reject",
    "broker_error",
}

REQUIRED_FIELDS = {
    "schema_version",
    "run_id",
    "event_id",
    "event_type",
    "order_id",
    "t_event_wall_ms",
    "t_event_mono_ns",
    "as_of_ts_ms",
}

TYPE_FIELDS = {
    "order_intent": {
        "client_order_id",
        "asset_id",
        "side",
        "size",
        "price",
        "mode",
        "t_decision_wall_ms",
    },
    "order_submit": {"broker", "status", "t_send_wall_ms"},
    "order_ack": {"broker", "status", "t_ack_wall_ms"},
    "order_fill": {"fill_price", "fill_size", "fees_bps", "t_fill_wall_ms"},
    "order_cancel": {"reason"},
    "order_reject": {"reason"},
    "broker_error": {"error_code"},
}


@dataclass(frozen=True)
class TradeEvent:
    payload: Dict[str, Any]


class TradeTape:
    def __init__(self, log_dir: str, run_id: str) -> None:
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.run_id = run_id
        self._handle: Optional[Any] = None
        self._lock = threading.Lock()
        self._event_seq = 0
        self._known_event_ids = set()
        self._last_numeric_event_id: Optional[int] = None

    def next_event_id(self) -> int:
        with self._lock:
            self._event_seq += 1
            return self._event_seq

    @staticmethod
    def deterministic_event_id(
        *,
        run_id: str,
        order_id: str,
        event_type: str,
        parent_event_id: Optional[object],
        event_seq_within_order: int,
        raw_subset: Optional[Dict[str, Any]] = None,
    ) -> str:
        canonical = json.dumps(raw_subset or {}, separators=(",", ":"), ensure_ascii=True, sort_keys=True)
        material = "|".join(
            [
                str(run_id),
                str(order_id),
                str(event_type),
                "" if parent_event_id is None else str(parent_event_id),
                str(int(event_seq_within_order)),
                canonical,
            ]
        )
        return hashlib.sha256(material.encode("utf-8")).hexdigest()[:24]

    def write(self, event: Dict[str, Any]) -> None:
        payload = dict(event)
        payload.setdefault("schema_version", "trade_v1")
        payload.setdefault("run_id", self.run_id)
        if "t_exec_wall" not in payload and "t_event_wall_ms" in payload:
            payload["t_exec_wall"] = payload["t_event_wall_ms"]
        self._validate(payload)
        line = json.dumps(payload, separators=(",", ":"), ensure_ascii=True, sort_keys=True)
        with self._lock:
            handle = self._get_handle()
            handle.write(line + "\n")
            handle.flush()

    def close(self) -> None:
        with self._lock:
            if self._handle is not None and not self._handle.closed:
                self._handle.close()
            self._handle = None

    def _validate(self, payload: Dict[str, Any]) -> None:
        missing = REQUIRED_FIELDS - payload.keys()
        if missing:
            raise ValueError(f"trade_tape_missing_fields:{sorted(missing)}")
        if payload.get("schema_version") != "trade_v1":
            raise ValueError("trade_tape_schema_mismatch")
        event_type = payload.get("event_type")
        if event_type not in EVENT_TYPES:
            raise ValueError(f"trade_tape_unknown_event:{event_type}")

        event_id = str(payload.get("event_id"))
        if event_id in self._known_event_ids:
            raise ValueError(f"trade_tape_duplicate_event_id:{event_id}")
        numeric_event_id = _maybe_numeric_event_id(payload.get("event_id"))
        if numeric_event_id is not None and self._last_numeric_event_id is not None:
            if numeric_event_id <= self._last_numeric_event_id:
                raise ValueError("trade_tape_event_id_not_monotonic")
        if numeric_event_id is not None:
            self._last_numeric_event_id = numeric_event_id
        self._known_event_ids.add(event_id)

        parent_event_id = payload.get("parent_event_id")
        if event_type == "order_intent":
            if parent_event_id is not None:
                raise ValueError("trade_tape_intent_parent_invalid")
        else:
            if parent_event_id is None:
                raise ValueError("trade_tape_missing_parent")
            if str(parent_event_id) not in self._known_event_ids:
                raise ValueError("trade_tape_unknown_parent")

        required_by_type = TYPE_FIELDS.get(event_type, set())
        type_missing = required_by_type - payload.keys()
        if type_missing:
            raise ValueError(f"trade_tape_missing_fields_for_type:{event_type}:{sorted(type_missing)}")

    def _get_handle(self):
        if self._handle is None or self._handle.closed:
            path = self.log_dir / "trade_tape.jsonl"
            self._handle = path.open("a", encoding="utf-8")
        return self._handle


def _maybe_numeric_event_id(value: Optional[object]) -> Optional[int]:
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return int(value)
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return None
