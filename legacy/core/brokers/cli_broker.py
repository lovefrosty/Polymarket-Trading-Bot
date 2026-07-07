from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import subprocess
from typing import Any, Dict, List, Optional, Sequence

from core.broker_base import BrokerBase, BrokerEvent, BrokerSnapshot, OrderIntent


@dataclass(frozen=True)
class CLIBrokerConfig:
    executable: str = "polymarket"
    timeout_secs: float = 10.0
    dry_run: bool = True
    json_flag: str = "--json"
    preflight_args: Sequence[str] = ("status", "--json")
    strict_preflight: bool = True


@dataclass(frozen=True)
class _CLIResult:
    argv: List[str]
    returncode: int
    stdout: str
    stderr: str
    parsed: Optional[Any]


class CLIBroker(BrokerBase):
    def __init__(self, config: Optional[CLIBrokerConfig] = None) -> None:
        self._config = config or CLIBrokerConfig()
        self._eligibility_checked = False
        self._eligible = False

    def submit(self, intent: OrderIntent) -> List[BrokerEvent]:
        if self._config.dry_run:
            return self._dry_run_submit(intent)
        eligibility = self._ensure_eligible(intent)
        if eligibility is not None:
            return [eligibility]
        result = self._run_json(self.build_submit_argv(intent), timeout_secs=self._config.timeout_secs)
        error = self._result_error(intent.order_id, result, error_code_prefix="CLI_SUBMIT")
        if error is not None:
            return [error]

        parsed = result.parsed if isinstance(result.parsed, dict) else {}
        raw_payload = _raw_payload(result)
        status = _status(parsed, default="accepted")
        events: List[BrokerEvent] = [
            BrokerEvent(
                event_type="order_submit",
                order_id=intent.order_id,
                payload={
                    "event_id": _broker_event_id(intent.order_id, "order_submit", 0, parsed),
                    "broker": "cli",
                    "status": "submitted",
                    "asset_id": intent.asset_id,
                    "side": intent.side,
                    "size": intent.size,
                    "price": intent.price,
                    "mode": intent.mode,
                    "client_order_id": intent.client_order_id,
                    "post_only": intent.post_only,
                    "time_in_force": intent.time_in_force,
                    "quote_group_id": intent.quote_group_id,
                    "idempotency_key": intent.idempotency_key,
                    "decision_id": intent.decision_id,
                    "as_of_ts_ms": intent.as_of_ts_ms,
                    "t_event_wall_ms": int(parsed.get("ts_ms") or intent.as_of_ts_ms),
                    "t_send_wall_ms": int(parsed.get("ts_ms") or intent.as_of_ts_ms),
                    "raw": raw_payload,
                },
            ),
            BrokerEvent(
                event_type="order_ack",
                order_id=intent.order_id,
                payload={
                    "event_id": _broker_event_id(intent.order_id, "order_ack", 1, parsed),
                    "broker": "cli",
                    "status": status,
                    "asset_id": intent.asset_id,
                    "side": intent.side,
                    "size": intent.size,
                    "price": intent.price,
                    "mode": intent.mode,
                    "client_order_id": intent.client_order_id,
                    "post_only": intent.post_only,
                    "time_in_force": intent.time_in_force,
                    "quote_group_id": intent.quote_group_id,
                    "idempotency_key": intent.idempotency_key,
                    "decision_id": intent.decision_id,
                    "as_of_ts_ms": intent.as_of_ts_ms,
                    "t_event_wall_ms": int(parsed.get("ack_ts_ms") or parsed.get("ts_ms") or intent.as_of_ts_ms),
                    "t_ack_wall_ms": int(parsed.get("ack_ts_ms") or parsed.get("ts_ms") or intent.as_of_ts_ms),
                    "raw": raw_payload,
                },
            ),
        ]
        for idx, fill in enumerate(_extract_fills(parsed, fallback_order_id=intent.order_id), start=2):
            fill_price = _safe_float(fill.get("fill_price") or fill.get("price") or intent.price, fallback=intent.price)
            fill_size = _safe_float(fill.get("fill_size") or fill.get("size"), fallback=0.0)
            fees_bps = _safe_float(fill.get("fees_bps") or fill.get("fee_bps"), fallback=0.0)
            filled_size = _safe_float(fill.get("filled_size") or fill.get("cumulative_filled_size"), fallback=fill_size)
            remaining_size = _safe_float(fill.get("remaining_size"), fallback=max(0.0, float(intent.size) - float(filled_size)))
            events.append(
                BrokerEvent(
                    event_type="order_fill",
                    order_id=intent.order_id,
                    payload={
                        "event_id": _broker_event_id(intent.order_id, "order_fill", idx, fill),
                        "fill_event_id": str(fill.get("fill_event_id") or _broker_event_id(intent.order_id, "fill_event", idx, fill)),
                        "fill_price": fill_price,
                        "fill_size": fill_size,
                        "fees_bps": fees_bps,
                        "filled_size": filled_size,
                        "remaining_size": remaining_size,
                        "decision_id": intent.decision_id,
                        "as_of_ts_ms": intent.as_of_ts_ms,
                        "t_event_wall_ms": int(fill.get("ts_ms") or parsed.get("ts_ms") or intent.as_of_ts_ms),
                        "t_fill_wall_ms": int(fill.get("ts_ms") or parsed.get("ts_ms") or intent.as_of_ts_ms),
                        "raw": raw_payload,
                    },
                )
            )
        return events

    def cancel(self, order_id: str) -> List[BrokerEvent]:
        if self._config.dry_run:
            return [
                BrokerEvent(
                    event_type="order_cancel",
                    order_id=order_id,
                    payload={
                        "event_id": _broker_event_id(order_id, "order_cancel", 0, {"dry_run": True}),
                        "reason": "DRY_RUN_CANCEL",
                        "t_event_wall_ms": 0,
                        "raw": {"dry_run": True},
                    },
                )
            ]
        result = self._run_json(self.build_cancel_argv(order_id), timeout_secs=self._config.timeout_secs)
        error = self._result_error(order_id, result, error_code_prefix="CLI_CANCEL")
        if error is not None:
            return [error]
        parsed = result.parsed if isinstance(result.parsed, dict) else {}
        return [
            BrokerEvent(
                event_type="order_cancel",
                order_id=order_id,
                payload={
                    "event_id": _broker_event_id(order_id, "order_cancel", 0, parsed),
                    "reason": str(parsed.get("reason") or parsed.get("status") or "CANCELED"),
                    "t_event_wall_ms": int(parsed.get("ts_ms") or 0),
                    "raw": _raw_payload(result),
                },
            )
        ]

    def replace(self, order_id: str, new_intent: OrderIntent) -> List[BrokerEvent]:
        events = self.cancel(order_id)
        if any(event.event_type == "broker_error" for event in events):
            return events
        events.extend(self.submit(new_intent))
        return events

    def snapshot(self) -> BrokerSnapshot:
        if self._config.dry_run:
            return BrokerSnapshot(open_orders={}, meta={"broker": "cli", "mode": "dry_run"})
        result = self._run_json(self.build_open_orders_argv(), timeout_secs=self._config.timeout_secs)
        parsed = result.parsed
        if result.returncode != 0 or parsed is None:
            return BrokerSnapshot(
                open_orders={},
                meta={
                    "broker": "cli",
                    "error": _cli_error_code(result, prefix="CLI_SNAPSHOT"),
                    "raw": _raw_payload(result),
                },
            )
        open_orders = _normalize_open_orders(parsed)
        return BrokerSnapshot(
            open_orders=open_orders,
            meta={
                "broker": "cli",
                "open_order_count": len(open_orders),
                "raw": _raw_payload(result),
            },
        )

    def build_submit_argv(self, intent: OrderIntent) -> List[str]:
        return [
            self._config.executable,
            "order",
            "place",
            "--token",
            str(intent.asset_id),
            "--price",
            _fmt_float(intent.price),
            "--size",
            _fmt_float(intent.size),
            "--side",
            str(intent.side).lower(),
            self._config.json_flag,
        ]

    def build_cancel_argv(self, order_id: str) -> List[str]:
        return [
            self._config.executable,
            "order",
            "cancel",
            "--order-id",
            str(order_id),
            self._config.json_flag,
        ]

    def build_open_orders_argv(self) -> List[str]:
        return [
            self._config.executable,
            "order",
            "list",
            "--status",
            "open",
            self._config.json_flag,
        ]

    def build_fills_argv(self, order_id: str) -> List[str]:
        return [
            self._config.executable,
            "fill",
            "list",
            "--order-id",
            str(order_id),
            self._config.json_flag,
        ]

    def _ensure_eligible(self, intent: OrderIntent) -> Optional[BrokerEvent]:
        if self._eligibility_checked:
            return None if self._eligible else _broker_error(intent.order_id, "CLI_GEO_RESTRICTED", {"eligibility_cached": False})

        argv = [self._config.executable, *self._config.preflight_args]
        result = self._run_json(argv, timeout_secs=self._config.timeout_secs)
        self._eligibility_checked = True
        if result.returncode != 0:
            self._eligible = False
            if self._config.strict_preflight:
                return _broker_error(intent.order_id, _cli_error_code(result, prefix="CLI_PREFLIGHT"), _raw_payload(result))
            return None
        parsed = result.parsed
        if not isinstance(parsed, dict):
            self._eligible = False
            if self._config.strict_preflight:
                return _broker_error(intent.order_id, "CLI_PREFLIGHT_BAD_JSON", _raw_payload(result))
            return None
        eligible = _interpret_eligible(parsed)
        self._eligible = bool(eligible)
        if self._eligible:
            return None
        return _broker_error(intent.order_id, "CLI_GEO_RESTRICTED", _raw_payload(result))

    def _run_json(self, argv: Sequence[str], timeout_secs: float) -> _CLIResult:
        try:
            completed = subprocess.run(
                list(argv),
                capture_output=True,
                text=True,
                timeout=max(0.1, float(timeout_secs)),
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            return _CLIResult(
                argv=list(argv),
                returncode=-999,
                stdout=exc.stdout.decode("utf-8", errors="replace") if isinstance(exc.stdout, bytes) else str(exc.stdout or ""),
                stderr=exc.stderr.decode("utf-8", errors="replace") if isinstance(exc.stderr, bytes) else str(exc.stderr or ""),
                parsed=None,
            )
        stdout = completed.stdout or ""
        parsed = None
        if stdout.strip():
            try:
                parsed = json.loads(stdout)
            except json.JSONDecodeError:
                parsed = None
        return _CLIResult(
            argv=list(argv),
            returncode=int(completed.returncode),
            stdout=stdout,
            stderr=completed.stderr or "",
            parsed=parsed,
        )

    def _result_error(self, order_id: str, result: _CLIResult, error_code_prefix: str) -> Optional[BrokerEvent]:
        if result.returncode == -999:
            return _broker_error(order_id, f"{error_code_prefix}_TIMEOUT", _raw_payload(result))
        if result.returncode != 0:
            return _broker_error(order_id, _cli_error_code(result, prefix=error_code_prefix), _raw_payload(result))
        if result.parsed is None:
            return _broker_error(order_id, f"{error_code_prefix}_BAD_JSON", _raw_payload(result))
        return None

    def _dry_run_submit(self, intent: OrderIntent) -> List[BrokerEvent]:
        argv = self.build_submit_argv(intent)
        raw = {"dry_run": True, "argv": argv}
        ts_ms = int(intent.as_of_ts_ms)
        return [
            BrokerEvent(
                event_type="order_submit",
                order_id=intent.order_id,
                payload={
                    "event_id": _broker_event_id(intent.order_id, "order_submit", 0, raw),
                    "broker": "cli",
                    "status": "submitted",
                    "asset_id": intent.asset_id,
                    "side": intent.side,
                    "size": intent.size,
                    "price": intent.price,
                    "mode": intent.mode,
                    "client_order_id": intent.client_order_id,
                    "post_only": intent.post_only,
                    "time_in_force": intent.time_in_force,
                    "quote_group_id": intent.quote_group_id,
                    "idempotency_key": intent.idempotency_key,
                    "decision_id": intent.decision_id,
                    "as_of_ts_ms": intent.as_of_ts_ms,
                    "t_event_wall_ms": ts_ms,
                    "t_send_wall_ms": ts_ms,
                    "raw": raw,
                },
            ),
            BrokerEvent(
                event_type="order_ack",
                order_id=intent.order_id,
                payload={
                    "event_id": _broker_event_id(intent.order_id, "order_ack", 1, raw),
                    "broker": "cli",
                    "status": "dry_run",
                    "asset_id": intent.asset_id,
                    "side": intent.side,
                    "size": intent.size,
                    "price": intent.price,
                    "mode": intent.mode,
                    "client_order_id": intent.client_order_id,
                    "post_only": intent.post_only,
                    "time_in_force": intent.time_in_force,
                    "quote_group_id": intent.quote_group_id,
                    "idempotency_key": intent.idempotency_key,
                    "decision_id": intent.decision_id,
                    "as_of_ts_ms": intent.as_of_ts_ms,
                    "t_event_wall_ms": ts_ms,
                    "t_ack_wall_ms": ts_ms,
                    "raw": raw,
                },
            ),
        ]


def _fmt_float(value: float) -> str:
    return f"{float(value):.8f}".rstrip("0").rstrip(".")


def _status(payload: Dict[str, Any], default: str) -> str:
    for key in ("status", "state", "result"):
        value = payload.get(key)
        if value is not None:
            return str(value)
    return str(default)


def _extract_fills(payload: Dict[str, Any], fallback_order_id: str) -> List[Dict[str, Any]]:
    fills = payload.get("fills")
    if isinstance(fills, list):
        out = []
        for fill in fills:
            if isinstance(fill, dict):
                row = dict(fill)
                row.setdefault("order_id", fallback_order_id)
                out.append(row)
        if out:
            return out
    filled_size = _safe_float(payload.get("filled_size") or payload.get("matched_size"), fallback=0.0)
    if filled_size <= 0.0:
        return []
    return [
        {
            "order_id": fallback_order_id,
            "fill_size": filled_size,
            "fill_price": _safe_float(payload.get("fill_price") or payload.get("price"), fallback=0.0),
            "fees_bps": _safe_float(payload.get("fees_bps") or payload.get("fee_bps"), fallback=0.0),
            "filled_size": filled_size,
            "remaining_size": _safe_float(payload.get("remaining_size"), fallback=0.0),
            "ts_ms": payload.get("fill_ts_ms") or payload.get("ts_ms"),
        }
    ]


def _normalize_open_orders(payload: Any) -> Dict[str, Any]:
    rows = payload
    if isinstance(payload, dict):
        rows = payload.get("orders") or payload.get("data") or []
    if not isinstance(rows, list):
        return {}
    out: Dict[str, Any] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        order_id = row.get("order_id") or row.get("id")
        if order_id is None:
            continue
        out[str(order_id)] = dict(row)
    return out


def _broker_error(order_id: str, error_code: str, raw: Dict[str, Any]) -> BrokerEvent:
    parsed = raw.get("parsed")
    parsed_dict = parsed if isinstance(parsed, dict) else {}
    return BrokerEvent(
        event_type="broker_error",
        order_id=order_id,
        payload={
            "event_id": _broker_event_id(order_id, "broker_error", 0, {"error_code": error_code, **raw}),
            "error_code": str(error_code),
            "t_event_wall_ms": int(parsed_dict.get("ts_ms") or 0),
            "raw": raw,
        },
    )


def _raw_payload(result: _CLIResult) -> Dict[str, Any]:
    return {
        "argv": list(result.argv),
        "returncode": int(result.returncode),
        "stdout": result.stdout,
        "stderr": result.stderr,
        "parsed": result.parsed,
    }


def _cli_error_code(result: _CLIResult, prefix: str) -> str:
    if result.returncode == -999:
        return f"{prefix}_TIMEOUT"
    if result.returncode != 0:
        return f"{prefix}_EXIT_NONZERO"
    if result.parsed is None:
        return f"{prefix}_BAD_JSON"
    return f"{prefix}_ERROR"


def _broker_event_id(order_id: str, event_type: str, idx: int, payload: Dict[str, Any]) -> str:
    material = json.dumps(payload, separators=(",", ":"), ensure_ascii=True, sort_keys=True)
    return hashlib.sha256(f"{order_id}|{event_type}|{idx}|{material}".encode("utf-8")).hexdigest()[:24]


def _safe_float(value: Optional[object], fallback: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(fallback)


def _interpret_eligible(payload: Dict[str, Any]) -> bool:
    for key in ("eligible", "can_trade", "trading_enabled"):
        if key in payload:
            return bool(payload.get(key))
    for key in ("geoblocked", "geo_blocked", "blocked"):
        if key in payload:
            return not bool(payload.get(key))
    return False
