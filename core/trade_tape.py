from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any, Callable, Dict, Optional
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
    def __init__(
        self,
        log_dir: str,
        run_id: str,
        date_key_provider: Optional[Callable[[], str]] = None,
    ) -> None:
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.run_id = run_id
        self._date_key_provider = date_key_provider or _default_date_key_provider
        self._handles: Dict[str, Any] = {}
        self._lock = threading.Lock()
        self._last_event_id = 0
        self._event_seq = 0
        self._known_event_ids = set()
        self._intent_event_ids: Dict[str, int] = {}

    def next_event_id(self) -> int:
        with self._lock:
            self._event_seq += 1
            return self._event_seq

    def write(self, event: Dict[str, Any]) -> None:
        payload = dict(event)
        payload.setdefault("schema_version", "trade_v1")
        payload.setdefault("run_id", self.run_id)
        self._validate(payload)
        line = json.dumps(payload, separators=(",", ":"), ensure_ascii=True, sort_keys=True)
        with self._lock:
            handle = self._get_handle()
            handle.write(line + "\n")
            handle.flush()

    def close(self) -> None:
        with self._lock:
            for handle in self._handles.values():
                handle.close()
            self._handles.clear()

    def _validate(self, payload: Dict[str, Any]) -> None:
        missing = REQUIRED_FIELDS - payload.keys()
        if missing:
            raise ValueError(f"trade_tape_missing_fields:{sorted(missing)}")
        if payload.get("schema_version") != "trade_v1":
            raise ValueError("trade_tape_schema_mismatch")
        event_type = payload.get("event_type")
        if event_type not in EVENT_TYPES:
            raise ValueError(f"trade_tape_unknown_event:{event_type}")
        event_id = int(payload.get("event_id"))
        if event_id in self._known_event_ids:
            raise ValueError(f"trade_tape_duplicate_event_id:{event_id}")
        if event_id <= self._last_event_id and self._known_event_ids:
            raise ValueError("trade_tape_event_id_not_monotonic")
        self._last_event_id = event_id
        self._known_event_ids.add(event_id)

        order_id = str(payload.get("order_id"))
        parent_event_id = payload.get("parent_event_id")
        if event_type == "order_intent":
            if parent_event_id is not None:
                raise ValueError("trade_tape_intent_parent_invalid")
            self._intent_event_ids[order_id] = event_id
        else:
            if parent_event_id is None:
                raise ValueError("trade_tape_missing_parent")
            parent_id = int(parent_event_id)
            if parent_id not in self._known_event_ids:
                raise ValueError("trade_tape_unknown_parent")

        required_by_type = TYPE_FIELDS.get(event_type, set())
        type_missing = required_by_type - payload.keys()
        if type_missing:
            raise ValueError(f"trade_tape_missing_fields_for_type:{event_type}:{sorted(type_missing)}")

    def _get_handle(self):
        date_key = str(self._date_key_provider())
        filename = f"trade_{date_key}.jsonl"
        path = self.log_dir / filename
        handle = self._handles.get(path.as_posix())
        if handle is None or handle.closed:
            handle = path.open("a", encoding="utf-8")
            self._handles[path.as_posix()] = handle
        return handle


def _default_date_key_provider() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d")
