from __future__ import annotations

from dataclasses import dataclass
import time
from typing import Any, Callable, Dict, List, Optional


@dataclass(frozen=True)
class ExecutionCallRecord:
    action: str
    started_at_ms: int
    latency_ms: float
    success: bool
    error: Optional[str]
    payload: Dict[str, Any]


@dataclass(frozen=True)
class ExecutionResult:
    success: bool
    payload: Dict[str, Any]
    error: Optional[str] = None


class ExecutionAdapter:
    """Thin wrapper around a py-clob-client compatible client."""

    def __init__(
        self,
        client: Any,
        *,
        order_args_type: Optional[type] = None,
        order_type: Any = None,
        refresh_auth: Optional[Callable[[], None]] = None,
        sleep_fn: Callable[[float], None] = time.sleep,
        max_retries: int = 2,
        base_backoff_secs: float = 0.25,
    ) -> None:
        self._client = client
        self._order_args_type = order_args_type
        self._order_type = order_type
        self._refresh_auth = refresh_auth
        self._sleep_fn = sleep_fn
        self._max_retries = int(max_retries)
        self._base_backoff_secs = float(base_backoff_secs)
        self.call_log: List[ExecutionCallRecord] = []

        if self._order_args_type is None or self._order_type is None:
            self._load_clob_types()

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
        payload = {
            "token_id": str(token_id),
            "side": str(side).upper(),
            "price": float(price),
            "size": float(size),
            "neg_risk": bool(neg_risk),
        }

        def _do_place() -> Dict[str, Any]:
            order_args = self._order_args_type(
                token_id=str(token_id),
                price=float(price),
                size=float(size),
                side=str(side).upper(),
            )
            signed = self._client.create_order(order_args)
            tif = time_in_force or getattr(self._order_type, "GTC", None)
            response = self._client.post_order(signed, tif)
            return dict(response or {})

        return self._execute("place_order", payload, _do_place)

    def cancel_order(self, order_id: str) -> ExecutionResult:
        payload = {"order_id": str(order_id)}
        return self._execute("cancel_order", payload, lambda: dict(self._client.cancel(order_id=order_id) or {}))

    def cancel_all(self) -> ExecutionResult:
        payload: Dict[str, Any] = {}
        return self._execute("cancel_all", payload, lambda: dict(self._client.cancel_all() or {}))

    def get_open_orders(self) -> ExecutionResult:
        payload: Dict[str, Any] = {}
        return self._execute("get_open_orders", payload, lambda: {"orders": self._client.get_orders() or []})

    def get_positions(self) -> ExecutionResult:
        payload: Dict[str, Any] = {}
        getter = getattr(self._client, "get_positions", None)
        if not callable(getter):
            raise AttributeError("client_missing_get_positions")
        return self._execute("get_positions", payload, lambda: {"positions": getter() or []})

    def _execute(self, action: str, payload: Dict[str, Any], fn: Callable[[], Dict[str, Any]]) -> ExecutionResult:
        attempt = 0
        while True:
            started_at_ms = _now_ms()
            started_mono = time.monotonic()
            try:
                result = fn()
                self._record(action, started_at_ms, started_mono, True, None, payload)
                return ExecutionResult(True, result)
            except Exception as exc:  # pragma: no cover - exercised via tests
                error_text = str(exc)
                retryable = _is_retryable_error(exc)
                is_nonce = _is_nonce_error(exc)
                if is_nonce and self._refresh_auth is not None:
                    self._refresh_auth()
                if retryable and attempt < self._max_retries:
                    self._record(action, started_at_ms, started_mono, False, error_text, payload)
                    self._sleep_fn(min(self._base_backoff_secs * (2 ** attempt), 5.0))
                    attempt += 1
                    continue
                self._record(action, started_at_ms, started_mono, False, error_text, payload)
                return ExecutionResult(False, payload={}, error=error_text)

    def _record(
        self,
        action: str,
        started_at_ms: int,
        started_mono: float,
        success: bool,
        error: Optional[str],
        payload: Dict[str, Any],
    ) -> None:
        self.call_log.append(
            ExecutionCallRecord(
                action=action,
                started_at_ms=started_at_ms,
                latency_ms=max(0.0, (time.monotonic() - started_mono) * 1000.0),
                success=success,
                error=error,
                payload=dict(payload),
            )
        )

    def _load_clob_types(self) -> None:
        from py_clob_client.clob_types import OrderArgs, OrderType  # type: ignore

        self._order_args_type = OrderArgs
        self._order_type = OrderType


def _now_ms() -> int:
    return int(time.time() * 1000)


def _is_retryable_error(exc: Exception) -> bool:
    text = str(exc).lower()
    status_code = getattr(exc, "status_code", None)
    if status_code in {429, 500, 502, 503, 504}:
        return True
    return any(token in text for token in ("429", "rate limit", "timeout", "timed out", "temporarily unavailable", "nonce"))


def _is_nonce_error(exc: Exception) -> bool:
    text = str(exc).lower()
    return "nonce" in text
