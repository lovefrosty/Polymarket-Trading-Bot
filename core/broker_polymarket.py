from __future__ import annotations

from dataclasses import dataclass
from importlib import metadata as importlib_metadata
import time
from typing import Any, Dict, List, Optional

from core.broker_base import BrokerBase, BrokerEvent, BrokerSnapshot, OrderIntent


EXPECTED_CLOB_CLIENT_VERSION = "0.20.0"


class BrokerContractError(RuntimeError):
    pass


@dataclass(frozen=True)
class PolymarketBrokerConfig:
    host: str = "https://clob.polymarket.com"
    chain_id: int = 137
    dry_run: bool = False
    timeout_secs: float = 10.0
    expected_client_version: str = EXPECTED_CLOB_CLIENT_VERSION
    strict_contract: bool = True


class PolymarketBroker(BrokerBase):
    def __init__(
        self,
        api_key: str,
        secret: str,
        passphrase: str,
        private_key: str,
        config: Optional[PolymarketBrokerConfig] = None,
    ) -> None:
        self._api_key = api_key
        self._secret = secret
        self._passphrase = passphrase
        self._private_key = private_key
        self._config = config or PolymarketBrokerConfig()
        self._client = None
        self._order_args_type = None
        self._order_type = None
        self._client_version: Optional[str] = None
        self._init_error: Optional[str] = None
        if not self._config.dry_run:
            self._init_client()

    @staticmethod
    def assert_contract(
        expected_version: str = EXPECTED_CLOB_CLIENT_VERSION,
        strict: bool = True,
    ) -> Dict[str, Any]:
        try:
            version = importlib_metadata.version("py-clob-client")
        except Exception as exc:
            raise BrokerContractError(f"py_clob_client_missing:{exc}") from exc

        if strict and expected_version and version != expected_version:
            raise BrokerContractError(
                f"py_clob_client_version_mismatch:expected={expected_version}:actual={version}"
            )

        try:
            from py_clob_client.client import ClobClient  # type: ignore
            from py_clob_client.clob_types import OrderArgs, OrderType  # type: ignore
        except Exception as exc:
            raise BrokerContractError(f"py_clob_client_import_error:{exc}") from exc

        required_methods = ["create_order", "post_order", "cancel", "get_orders"]
        missing = [name for name in required_methods if not callable(getattr(ClobClient, name, None))]
        if missing:
            raise BrokerContractError(f"py_clob_client_missing_methods:{','.join(missing)}")

        if not hasattr(OrderType, "GTC"):
            raise BrokerContractError("py_clob_client_order_type_missing:GTC")

        return {
            "version": version,
            "required_methods": required_methods,
            "strict": bool(strict),
        }

    def submit(self, intent: OrderIntent) -> List[BrokerEvent]:
        ts_ms = _now_ms()
        if self._config.dry_run:
            return _dry_run_events(intent, ts_ms)
        if self._client is None:
            return [
                BrokerEvent(
                    event_type="broker_error",
                    order_id=intent.order_id,
                    payload={
                        "error_code": "BROKER_INIT_FAILED",
                        "reason": self._init_error or "client_unavailable",
                        "t_event_wall_ms": ts_ms,
                    },
                )
            ]
        try:
            payload = self._submit_live(intent)
            status = str(payload.get("status") or payload.get("state") or "accepted")
            return [
                BrokerEvent(
                    event_type="order_submit",
                    order_id=intent.order_id,
                    payload={
                        "broker": "polymarket",
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
                        "t_event_wall_ms": ts_ms,
                        "t_send_wall_ms": ts_ms,
                        "raw": payload,
                    },
                ),
                BrokerEvent(
                    event_type="order_ack",
                    order_id=intent.order_id,
                    payload={
                        "broker": "polymarket",
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
                        "t_event_wall_ms": _now_ms(),
                        "t_ack_wall_ms": _now_ms(),
                        "raw": payload,
                    },
                ),
            ]
        except Exception as exc:
            return [
                BrokerEvent(
                    event_type="order_reject",
                    order_id=intent.order_id,
                    payload={
                        "reason": "BROKER_SUBMIT_ERROR",
                        "error_code": "BROKER_SUBMIT_ERROR",
                        "detail": str(exc),
                        "t_event_wall_ms": _now_ms(),
                    },
                )
            ]

    def cancel(self, order_id: str) -> List[BrokerEvent]:
        ts_ms = _now_ms()
        if self._config.dry_run:
            return [
                BrokerEvent(
                    event_type="order_cancel",
                    order_id=order_id,
                    payload={"reason": "DRY_RUN_CANCEL", "t_event_wall_ms": ts_ms},
                )
            ]
        if self._client is None:
            return [
                BrokerEvent(
                    event_type="broker_error",
                    order_id=order_id,
                    payload={"error_code": "BROKER_INIT_FAILED", "t_event_wall_ms": ts_ms},
                )
            ]
        try:
            raw = self._client.cancel(order_id=order_id)
            return [
                BrokerEvent(
                    event_type="order_cancel",
                    order_id=order_id,
                    payload={"reason": "CANCELED", "t_event_wall_ms": _now_ms(), "raw": raw},
                )
            ]
        except Exception as exc:
            return [
                BrokerEvent(
                    event_type="broker_error",
                    order_id=order_id,
                    payload={
                        "error_code": "BROKER_CANCEL_ERROR",
                        "detail": str(exc),
                        "t_event_wall_ms": _now_ms(),
                    },
                )
            ]

    def replace(self, order_id: str, new_intent: OrderIntent) -> List[BrokerEvent]:
        events = self.cancel(order_id)
        events.extend(self.submit(new_intent))
        return events

    def snapshot(self) -> BrokerSnapshot:
        if self._config.dry_run:
            return BrokerSnapshot(open_orders={}, meta={"broker": "polymarket", "mode": "dry_run"})
        if self._client is None:
            return BrokerSnapshot(open_orders={}, meta={"broker": "polymarket", "error": self._init_error or "init_failed"})
        try:
            raw = self._client.get_orders()
            open_orders = _normalize_open_orders(raw)
            return BrokerSnapshot(
                open_orders=open_orders,
                meta={
                    "broker": "polymarket",
                    "client_version": self._client_version,
                    "open_order_count": len(open_orders),
                },
            )
        except Exception as exc:
            return BrokerSnapshot(open_orders={}, meta={"broker": "polymarket", "error": str(exc)})

    def _init_client(self) -> None:
        try:
            contract = self.assert_contract(
                expected_version=self._config.expected_client_version,
                strict=self._config.strict_contract,
            )
            from py_clob_client.client import ClobClient  # type: ignore
            from py_clob_client.clob_types import OrderArgs, OrderType  # type: ignore

            self._order_args_type = OrderArgs
            self._order_type = OrderType
            self._client_version = str(contract.get("version") or "unknown")
            self._client = ClobClient(
                host=self._config.host,
                chain_id=self._config.chain_id,
                key=self._private_key,
                creds={"key": self._api_key, "secret": self._secret, "passphrase": self._passphrase},
            )
        except Exception as exc:
            self._init_error = str(exc)
            self._client = None
            if isinstance(exc, BrokerContractError):
                raise

    def _submit_live(self, intent: OrderIntent) -> Dict[str, Any]:
        if self._order_args_type is None or self._order_type is None or self._client is None:
            raise RuntimeError("broker_contract_not_initialized")

        order_args = self._order_args_type(
            price=float(intent.price),
            size=float(intent.size),
            side=intent.side.upper(),
            token_id=intent.asset_id,
        )
        signed_order = self._client.create_order(order_args=order_args)

        tif_name = str(intent.time_in_force or "GTC").upper()
        order_type = getattr(self._order_type, tif_name, None)
        if order_type is None:
            if self._config.strict_contract:
                raise BrokerContractError(f"unsupported_time_in_force:{tif_name}")
            order_type = getattr(self._order_type, "GTC")

        raw = self._client.post_order(order=signed_order, order_type=order_type)
        if isinstance(raw, dict):
            return raw
        return {"status": "accepted", "raw": str(raw)}


def _normalize_open_orders(raw: Any) -> Dict[str, Dict[str, Any]]:
    if raw is None:
        return {}
    if isinstance(raw, dict) and isinstance(raw.get("orders"), list):
        rows = raw.get("orders") or []
    elif isinstance(raw, list):
        rows = raw
    else:
        rows = [raw]

    normalized: Dict[str, Dict[str, Any]] = {}
    for idx, row in enumerate(rows):
        if not isinstance(row, dict):
            continue
        order_id = str(row.get("id") or row.get("order_id") or row.get("orderId") or f"snapshot:{idx}")
        side = str(row.get("side") or "").lower()
        if side not in {"buy", "sell"}:
            side = "buy" if side in {"bid", "yes"} else "sell"
        normalized[order_id] = {
            "order_id": order_id,
            "token_id": str(row.get("token_id") or row.get("tokenId") or row.get("asset_id") or ""),
            "side": side,
            "price": _as_float(row.get("price")),
            "size": _as_float(row.get("size") or row.get("remaining_size") or row.get("remainingSize")),
            "status": str(row.get("status") or row.get("state") or "open"),
            "client_order_id": str(row.get("client_order_id") or row.get("clientOrderId") or ""),
            "quote_group_id": str(row.get("quote_group_id") or ""),
        }
    return normalized


def _as_float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _dry_run_events(intent: OrderIntent, ts_ms: int) -> List[BrokerEvent]:
    return [
        BrokerEvent(
            event_type="order_submit",
            order_id=intent.order_id,
            payload={
                "broker": "polymarket",
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
                "t_event_wall_ms": ts_ms,
                "t_send_wall_ms": ts_ms,
            },
        ),
        BrokerEvent(
            event_type="order_ack",
            order_id=intent.order_id,
            payload={
                "broker": "polymarket",
                "status": "accepted",
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
                "t_event_wall_ms": ts_ms,
                "t_ack_wall_ms": ts_ms,
            },
        ),
    ]


def _now_ms() -> int:
    return int(time.time() * 1000)
